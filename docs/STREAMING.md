# How Laolao streams captions from a model that cannot stream

Whisper takes a whole window of audio and returns a whole transcript. It has no
streaming mode. Laolao still shows words appearing as you speak and quietly
"correcting" themselves, and this document explains exactly how, precisely
enough to reuse the pattern elsewhere. Everything below is what `server.py`
does today; the line references are to `UtteranceProcessor`.

The one-sentence version: **keep a buffer of the current utterance, re-decode
the whole buffer from its start every few hundred milliseconds, and replace
the displayed line each time.** There is no incremental decoding and there is
no correction step. Each partial is a fresh transcription with more context
than the last, and the display throws the previous one away.

## 1. The moving parts

```
mic ──chunks──▶ VAD ──▶ rolling buffer ──snapshots──▶ worker thread ──▶ WebSocket ──▶ overlay
  250 ms         speech?     grows while                one Whisper        partial /       replaces
                             speaking                   call at a time     final / clear   or appends
```

- **Chunks.** Audio arrives as int16 16 kHz mono in fixed chunks
  (`chunk_ms`, 250 ms in the shipped config). Every chunk goes through
  `feed()` exactly once, on the audio thread, and nothing slow ever runs
  there.
- **VAD.** Each chunk is classified speech or not by Silero VAD (a small
  neural model on 32 ms windows; the chunk is speech if any window is). This
  is the only thing that decides where an utterance starts and ends.
- **Buffer.** `_buffer` holds the audio of the utterance in progress, from its
  first speech chunk to now. It is capped at `rolling_window_s` (5 s shipped).
- **Snapshots.** The engine never transcribes the live buffer; it copies it
  and hands the copy to a worker. That is what lets audio keep flowing while
  Whisper is busy.
- **Worker.** One thread, one Whisper call at a time, pulling from a queue.
- **Messages.** Three that matter: `partial` (replace the in-progress line),
  `final` (commit a line), `clear_partial` (drop the in-progress line without
  committing anything).

## 2. The state machine, chunk by chunk

`feed(chunk)` is the whole engine. Per chunk:

```
speech chunk:
    silence_count = 0
    in_utterance  = True
    buffer += chunk
    if len(buffer) >= rolling_window:   → COMMIT (section 4)
    if now - last_partial_t >= partial_interval_s:
        last_partial_t = now
        enqueue("partial", buffer.copy())        ← the "streaming"

silent chunk while in_utterance:
    buffer += chunk                               ← keep a little trailing silence
    silence_count += 1
    if silence_count >= silence_chunks:          → FLUSH (section 3)

silent chunk otherwise:
    nothing
```

Shipped timing: a partial is requested at most every 0.35 s
(`partial_interval_s`), and an utterance ends after 3 consecutive silent
chunks (`silence_chunks` × `chunk_ms` = 0.75 s of silence). Trailing silence
is deliberately kept in the buffer so the model hears the sentence end.

**Why it looks like streaming.** Say you speak for two seconds. The engine
requests a partial at roughly 0.35 s, 0.70 s, 1.05 s, … and each request is
the *entire* buffer so far: first ~0.35 s of audio, then ~0.70 s, then ~1.05 s.
Each transcription starts from the beginning of the utterance. The overlay
replaces the in-progress line with each result, so the reader sees a line
that grows. It is not being appended to; it is being redrawn from scratch,
slightly longer each time.

**Why it looks like it corrects itself.** Whisper's encoder attends to the
whole window. When the buffer gains another 350 ms, the model re-hears the
earlier words with more right-hand context, and an ambiguous character often
resolves differently. Nothing compares the new result to the old one. The
old line is simply gone. From the reader's side that is indistinguishable
from a correction.

## 3. Finishing an utterance: FLUSH

After `silence_chunks` silent chunks, `_flush()`:

```
in_utterance = False;  silence_count = 0
last_partial_text = "";  last_partial_t = 0
audio  = buffer;  buffer = empty
vad.reset()
enqueue("final", audio)
```

The final is a completely separate decode of the same audio the last partial
saw, plus the trailing silence. It is decoded with beam search
(`beam_size`, 4) where partials are greedy (`partial_beam_size`, 1), so it can
legitimately choose a different path through the same audio. When it arrives
the overlay removes the yellow in-progress line and appends a white committed
line. That swap is the last "correction" the reader sees, and the one that
costs the extra half second (see `DECODING.md` for the measurements).

## 4. Long sentences: COMMIT with an overlap tail

If the buffer reaches `rolling_window_s` while the speaker is still going,
`_commit_segment()`:

```
audio  = buffer                        (the full 5 s)
buffer = audio[-0.5 s:]                (keep a short tail)
last_partial_text = "";  last_partial_t = 0
enqueue("commit", audio)               (a final, tagged)
```

The whole window is committed as a final caption, so a long sentence never
silently loses its start when the window would otherwise slide. The 0.5 s
tail stays in the buffer so the next decode has acoustic continuity across the
cut rather than starting mid-phoneme. The utterance stays open.

The cost of the tail is that the next final can *repeat* the last word or two
of the committed line. Section 6 handles that.

## 5. The worker: coalescing, and why it never falls behind

The audio thread must never wait on Whisper. `_enqueue_transcribe()` puts
snapshots on a queue and one worker thread drains it. Two rules:

- **Partials coalesce.** If a transcription is in flight and a new partial is
  requested, every partial still waiting in the queue is discarded and
  replaced by the newest snapshot. A partial that ran late would emit text
  already older than what the reader can see; running it is pure waste. So
  the worker always sees at most one pending partial, and it is the freshest.
- **Finals and commits always queue** and are never dropped. They are the
  captions the reader keeps.

Consequence: if the model is slow, the *rate* of partials drops but the
display never lags behind the audio by more than one decode. If the model is
fast, partials arrive at the requested interval. Either way the audio buffer
is drained on time.

## 6. What the emission path filters

`_run_transcribe()` runs on the worker after each decode. In order:

**Partials** are shown only if all of: non-empty; different from the last
partial shown (identical text is not re-sent); and *plausible* (below). Then
Traditional → Simplified conversion is applied and the message is pushed.

**Finals and commits**, if non-empty and plausible, are converted and pushed,
with one exception: the **overlap-tail duplicate**. A flush that arrives
within 5 s of a commit, covers ≤ 2 s of audio, and whose text is a suffix of
the committed caption is the re-transcribed 0.5 s tail from section 4, not
new speech. It is dropped, and a `clear_partial` is sent instead so no stale
in-progress text lingers. Flush-after-flush is never deduplicated, so someone
genuinely saying "好，好" still gets both.

**Rejected or empty finals** send `clear_partial`. The reasoning: no final
will replace the last partial, so the overlay must be told to drop it, or the
yellow line stays on screen indefinitely.

**Plausibility** is one rule: characters per second of audio must not exceed
10 for CJK languages or 20 otherwise. Whisper's characteristic failure on
silence and noise is a fluent sentence from nowhere; a "sentence" arriving in
half a second of audio is that failure, and it is discarded before display.

## 7. What the overlay does with the messages

- `partial` → if there is an in-progress element, set its text; otherwise
  create one. **Replace, never append.**
- `final` → remove the in-progress element, append a committed line, evict
  the oldest committed line beyond `maxlines` (1 by default), and start a
  fade timer (25 s by default; 0 disables).
- `clear_partial` → remove the in-progress element.

The overlay holds no transcript state and does no merging. Every decision
about what text exists was made in the engine; the overlay is a display.

## 8. Reusing the pattern

For any model that returns a whole result for a whole input (speech to text,
OCR on a growing page, a translation model, a summariser over a growing
document), the recipe is:

1. **Gate the input** with something cheap that says "activity" or "no
   activity". For audio that is a VAD; for text it might be a keystroke
   timer.
2. **Buffer from the start of the current unit** (utterance, paragraph,
   page), not a sliding window. Sliding windows lose the start.
3. **Re-run the model on the whole buffer on a timer**, from a snapshot, on a
   worker. Never on the input thread.
4. **Coalesce in-progress requests to the newest one**; never drop the
   closing request.
5. **Replace the displayed in-progress result each time**, do not diff or
   append. The model's own reconsideration with more context *is* the
   correction mechanism.
6. **Close the unit on inactivity** with one final run, optionally at a
   higher quality setting than the in-progress runs.
7. **Cap the buffer**; on overflow, commit the whole thing and keep a short
   overlap tail; dedupe the tail's repeat on the next close.
8. **Filter before display**: reject outputs implausible for the input size,
   suppress identical repeats, and send an explicit "clear" when a unit
   closes without a keepable result.

The trade-off this buys you: latency to first output is one model call on a
short buffer, and quality improves continuously as context grows, at the cost
of re-decoding the same audio many times. That cost is what makes the choice
of model matter: a model with an expensive encoder and a cheap decoder
(large-v3-turbo) is ideal, because each re-run is dominated by a single
encoder pass.

The trade-off it does not solve: the in-progress line rewrites itself, and
nothing today measures how often. A line that changes six times before
settling can score perfectly and still be tiring to read. If you build this
elsewhere, count rewrites per final and time-to-settle per character from
day one; Laolao does not yet, and it is the next metric worth adding.
