"""Unit tests for server.UtteranceProcessor and the audio-queue plumbing.

Uses a fake backend and a scripted VAD — no Whisper model, no microphone,
no `slow` marker. Covers:

  * segment-commit on rolling-window overflow (long utterances keep their
    beginning; no audio is silently dropped)
  * clear_partial emitted when a final is rejected or empty
  * Electron WS audio goes through _audio_q + consumer thread, never
    synchronously on the caller's thread
  * t2s config flag gates OpenCC conversion
"""

from __future__ import annotations

import queue
import threading
import time

import numpy as np
import pytest

import server


# ──────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────

class FakeBackend:
    name = "fake"

    def __init__(self, text: str = "你好") -> None:
        self.text = text
        self.calls: list[np.ndarray] = []

    def transcribe(self, audio, language=None) -> str:
        self.calls.append(np.asarray(audio).copy())
        return self.text


class ScriptedVAD:
    """Returns the scripted booleans in order; False once exhausted."""

    def __init__(self, script) -> None:
        self.script = list(script)

    def is_speech(self, chunk) -> bool:
        return self.script.pop(0) if self.script else False

    def reset(self) -> None:
        pass


def make_cfg(**overrides) -> dict:
    cfg = dict(server.DEFAULTS)
    cfg.update({
        "language": "zh",
        "t2s": False,              # avoid OpenCC dependency in most tests
        "chunk_ms": 250,
        "rolling_window_s": 1.0,   # tiny window so overflow is easy to hit
        "silence_chunks": 2,
        "show_partial": False,     # finals only, unless a test opts in
        "partial_interval_s": 999.0,
    })
    cfg.update(overrides)
    return cfg


def wait_for(pred, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def pushed(monkeypatch):
    """Capture everything the processor push()es (from any thread)."""
    messages: list[dict] = []
    monkeypatch.setattr(server, "push", messages.append)
    return messages


def chunk_of(value: int, samples: int = 4000) -> np.ndarray:
    """250 ms of int16 audio filled with a marker value."""
    return np.full(samples, value, dtype=np.int16)


# ──────────────────────────────────────────────────────────────
# #9 — segment-commit on rolling-window overflow
# ──────────────────────────────────────────────────────────────

def test_long_utterance_commits_segments_covering_all_audio(pushed):
    backend = FakeBackend(text="你好")
    vad = ScriptedVAD([True] * 6)             # 6 speech chunks, then silence
    proc = server.UtteranceProcessor(backend, vad, make_cfg())

    # 6 speech chunks (values 1-6) + 2 silence chunks (values 7-8).
    # rolling_window = 1.0 s = 16 000 samples = 4 chunks:
    #   commit #1 after chunk 4 (values 1-4), keep 0.5 s tail (3-4)
    #   commit #2 after chunk 6 (values 3-6), keep tail (5-6)
    #   flush   #3 after 2 silence chunks (values 5-8)
    for v in range(1, 9):
        proc.feed(chunk_of(v))

    assert wait_for(lambda: len(backend.calls) >= 3), \
        f"expected 3 finals, worker saw {len(backend.calls)}"

    # First committed segment is the full window — the start of the long
    # sentence (value 1) was transcribed, not silently dropped.
    assert len(backend.calls[0]) == 16_000
    assert 1 in np.unique(backend.calls[0])

    # Every fed sample value appears in some final: nothing was dropped.
    seen = set()
    for audio in backend.calls[:3]:
        seen.update(np.unique(audio).tolist())
    assert seen == set(range(1, 9))

    # The fake backend returns identical text for all three segments, so the
    # short tail-flush final is suppressed by the overlap dedup guard; the
    # two window-commits are emitted. (Real segments transcribe differently.)
    assert wait_for(
        lambda: sum(1 for m in pushed if m.get("type") == "final") >= 2)
    assert wait_for(lambda: proc._n_dup_dropped == 1)


def test_commit_keeps_overlap_tail_and_stays_in_utterance(pushed):
    backend = FakeBackend(text="你好")
    vad = ScriptedVAD([True] * 4)
    proc = server.UtteranceProcessor(backend, vad, make_cfg())

    for v in range(1, 5):                     # exactly one window
        proc.feed(chunk_of(v))

    assert wait_for(lambda: len(backend.calls) >= 1)
    # Still mid-utterance after the commit; 0.5 s tail retained for context.
    assert proc._in_utterance
    assert len(proc._buffer) == 8_000
    assert set(np.unique(proc._buffer)) == {3, 4}
    # Partial dedup was reset so the next partial isn't suppressed.
    assert proc._last_partial_text == ""


# ──────────────────────────────────────────────────────────────
# #8 — clear_partial on rejected / empty finals
# ──────────────────────────────────────────────────────────────

def test_rejected_hallucination_pushes_clear_partial(pushed):
    # 200 chars over ~1.5 s of audio blows the 10 chars/s CJK cap.
    backend = FakeBackend(text="嗯" * 200)
    vad = ScriptedVAD([True, True, True, True])
    proc = server.UtteranceProcessor(
        backend, vad, make_cfg(rolling_window_s=10.0))

    for v in range(1, 5):
        proc.feed(chunk_of(v))
    proc.feed(chunk_of(9))                    # silence x2 → flush
    proc.feed(chunk_of(9))

    assert wait_for(
        lambda: any(m.get("type") == "clear_partial" for m in pushed))
    assert not any(m.get("type") == "final" for m in pushed)
    assert wait_for(lambda: proc._n_rejected == 1)


def test_empty_final_pushes_clear_partial(pushed):
    backend = FakeBackend(text="")
    vad = ScriptedVAD([True, True])
    proc = server.UtteranceProcessor(
        backend, vad, make_cfg(rolling_window_s=10.0))

    proc.feed(chunk_of(1))
    proc.feed(chunk_of(2))
    proc.feed(chunk_of(9))                    # silence x2 → flush
    proc.feed(chunk_of(9))

    assert wait_for(
        lambda: any(m.get("type") == "clear_partial" for m in pushed))
    assert not any(m.get("type") == "final" for m in pushed)
    assert proc._n_rejected == 0              # empty is not a hallucination


# ──────────────────────────────────────────────────────────────
# #6 — Electron audio goes through the queue, off the caller thread
# ──────────────────────────────────────────────────────────────

class _StubProcessor:
    def __init__(self) -> None:
        self.fed: list[np.ndarray] = []
        self.thread_idents: list[int] = []

    def feed(self, chunk) -> None:
        self.fed.append(chunk)
        self.thread_idents.append(threading.get_ident())


def test_feed_electron_audio_enqueues_without_calling_feed(monkeypatch):
    monkeypatch.setattr(server, "_audio_q", queue.Queue())
    stub = _StubProcessor()
    monkeypatch.setattr(server, "_processor", stub)

    pcm = np.ones(4000, dtype=np.int16)
    server.feed_electron_audio(pcm)

    assert stub.fed == []                     # not fed synchronously
    assert server._audio_q.qsize() == 1
    assert np.array_equal(server._audio_q.get_nowait(), pcm)


def test_consume_loop_feeds_on_its_own_thread(monkeypatch):
    monkeypatch.setattr(server, "_audio_q", queue.Queue())
    monkeypatch.setattr(server, "_running", True)
    stub = _StubProcessor()

    t = threading.Thread(target=server._consume_loop, args=(stub,), daemon=True)
    t.start()
    try:
        server.feed_electron_audio(np.ones(4000, dtype=np.int16))
        assert wait_for(lambda: len(stub.fed) == 1)
        assert stub.thread_idents[0] == t.ident
        assert stub.thread_idents[0] != threading.get_ident()
    finally:
        monkeypatch.setattr(server, "_running", False)
        t.join(timeout=2)
    assert not t.is_alive()


# ──────────────────────────────────────────────────────────────
# Overlap-tail dedup — a flush right after a window-commit that merely
# re-transcribes the 0.5 s overlap tail is dropped; genuine speech is not.
# ──────────────────────────────────────────────────────────────

class SeqBackend:
    """Returns the scripted texts in order; empty string once exhausted."""

    name = "fake-seq"

    def __init__(self, texts) -> None:
        self.texts = list(texts)
        self.calls: list[np.ndarray] = []

    def transcribe(self, audio, language=None) -> str:
        self.calls.append(np.asarray(audio).copy())
        return self.texts.pop(0) if self.texts else ""


def test_flush_after_commit_drops_overlap_tail_duplicate(pushed):
    # Commit emits "你好"; the flush right after covers only the 0.5 s
    # overlap tail (+2 silence chunks = 1.0 s) and transcribes to the same
    # text — the overlap-tail signature. It must be dropped.
    backend = FakeBackend(text="你好")
    vad = ScriptedVAD([True] * 4)
    proc = server.UtteranceProcessor(backend, vad, make_cfg())

    for v in range(1, 5):                     # fill the window → commit
        proc.feed(chunk_of(v))
    proc.feed(chunk_of(9))                    # silence ×2 → flush
    proc.feed(chunk_of(9))

    assert wait_for(lambda: len(backend.calls) >= 2)
    assert wait_for(lambda: proc._n_dup_dropped == 1)
    finals = [m for m in pushed if m.get("type") == "final"]
    assert len(finals) == 1                   # only the commit was emitted
    # The dropped flush still clears any lingering partial.
    assert any(m.get("type") == "clear_partial" for m in pushed)


def test_flush_after_commit_with_new_text_is_emitted(pushed):
    # Same timing as above, but the tail flush transcribes to different
    # text (not a suffix of the committed caption) — genuine new speech.
    backend = SeqBackend(["你好世界", "再见"])
    vad = ScriptedVAD([True] * 4)
    proc = server.UtteranceProcessor(backend, vad, make_cfg())

    for v in range(1, 5):
        proc.feed(chunk_of(v))
    proc.feed(chunk_of(9))
    proc.feed(chunk_of(9))

    assert wait_for(
        lambda: sum(1 for m in pushed if m.get("type") == "final") == 2)
    assert proc._n_dup_dropped == 0


def test_long_flush_after_commit_is_not_deduped(pushed):
    # A flush whose audio exceeds _DUP_MAX_AUDIO_S is real speech even if
    # the model returns the same text (e.g. the speaker actually repeated
    # the sentence): 0.5 s tail + 7 silence chunks = 2.25 s > 2.0 s.
    backend = FakeBackend(text="你好")
    vad = ScriptedVAD([True] * 4)
    proc = server.UtteranceProcessor(
        backend, vad, make_cfg(silence_chunks=7))

    for v in range(1, 5):                     # fill the window → commit
        proc.feed(chunk_of(v))
    for _ in range(7):                        # long silence → 2.25 s flush
        proc.feed(chunk_of(9))

    assert wait_for(
        lambda: sum(1 for m in pushed if m.get("type") == "final") == 2)
    assert proc._n_dup_dropped == 0


def test_flush_to_flush_repetition_is_never_deduped(pushed):
    # Two short utterances with identical text and no commit in between —
    # a speaker genuinely repeating themselves. Both captions must show.
    backend = FakeBackend(text="好")
    vad = ScriptedVAD([True, True, False, False, True, True])
    proc = server.UtteranceProcessor(
        backend, vad, make_cfg(rolling_window_s=10.0))

    for v in (1, 2, 9, 9):                    # utterance 1 → flush
        proc.feed(chunk_of(v))
    assert wait_for(
        lambda: sum(1 for m in pushed if m.get("type") == "final") == 1)
    for v in (3, 4, 9, 9):                    # utterance 2, same text
        proc.feed(chunk_of(v))

    assert wait_for(
        lambda: sum(1 for m in pushed if m.get("type") == "final") == 2)
    assert proc._n_dup_dropped == 0


# ──────────────────────────────────────────────────────────────
# #17 — t2s config flag
# ──────────────────────────────────────────────────────────────

def test_t2s_false_disables_opencc():
    proc = server.UtteranceProcessor(
        FakeBackend(), ScriptedVAD([]), make_cfg(t2s=False))
    assert proc._opencc is None
    assert proc._to_simplified("漢字") == "漢字"


def test_t2s_true_enables_opencc_for_zh():
    pytest.importorskip("opencc")
    proc = server.UtteranceProcessor(
        FakeBackend(), ScriptedVAD([]), make_cfg(t2s=True))
    assert proc._opencc is not None
    assert proc._to_simplified("漢字") == "汉字"


def test_set_language_respects_t2s_flag():
    pytest.importorskip("opencc")
    proc = server.UtteranceProcessor(
        FakeBackend(), ScriptedVAD([]), make_cfg(t2s=False, language="en"))
    proc.set_language("yue")
    assert proc._opencc is None               # t2s off → no conversion

    proc2 = server.UtteranceProcessor(
        FakeBackend(), ScriptedVAD([]), make_cfg(t2s=True, language="en"))
    assert proc2._opencc is None              # en → no conversion
    proc2.set_language("yue")
    assert proc2._opencc is not None
