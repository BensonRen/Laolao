"""
Backend-agnostic beam search for Whisper decoding.

Why this is its own module
--------------------------
Laolao decodes Whisper on three very different runtimes, and each one exposes a
*different* decoder-call shape:

  * ``QnnWhisperBackend``   Qualcomm's precompiled QNN graph is **batch-1 with
                            static shapes**, so the beams cannot be folded into
                            one call — each live beam costs its own NPU
                            invocation per step.
  * ``OnnxWhisperBackend``  The onnx-community export keeps a **dynamic batch
                            axis**, so the batch dimension *is* the beam
                            dimension and one call advances every beam.
  * ``MLXBackend`` /
    ``FasterWhisperBackend`` decode inside someone else's library and take a
                            ``beam_size`` argument instead.

Only the first two need us to run the search ourselves, and the part that is
genuinely easy to get wrong is identical for both: length normalisation,
candidate selection, when a finished hypothesis retires a beam, and when it is
safe to stop early. That bookkeeping lives here, once. A backend supplies three
small adapters describing how *its* runtime advances and reorders beams.

The scoring helpers are exported too, because the greedy paths use the same
logit masking and top-k.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

import numpy as np

# Beam search defaults. beam_size=1 is plain greedy and skips this module.
DEFAULT_BEAM_SIZE = 4
DEFAULT_LENGTH_PENALTY = 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# Scoring helpers
# ═══════════════════════════════════════════════════════════════════════════════

def log_softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable log-softmax over the last axis.

    Suppressed tokens arrive as ``-inf``; ``exp(-inf) == 0`` keeps them out of the
    normaliser and they stay ``-inf`` in the result, which is exactly what beam
    search wants — such a token can never be selected, at any score.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        m = np.max(logits, axis=-1, keepdims=True)
        m = np.where(np.isfinite(m), m, 0.0)
        shifted = logits - m
        denom = np.log(np.sum(np.exp(shifted), axis=-1, keepdims=True))
    # A fully suppressed row would give log(0) = -inf and then -inf - -inf = NaN,
    # which propagates into every beam score and silently kills the decode. Leave
    # such a row at -inf: no token is selectable, which is the honest answer.
        return np.where(np.isfinite(denom), shifted - denom, shifted)


def length_normalised(score: float, n_tokens: int, penalty: float) -> float:
    """Score a hypothesis independently of how long it is.

    A raw beam score is a sum of negative log-probabilities, so it always falls as
    a hypothesis grows.  Without this, beam search systematically prefers the
    shortest hypothesis and truncates sentences — the exact failure this change
    exists to avoid.  ``penalty`` is the Google-NMT/HF exponent: 1.0 is a plain
    per-token mean, >1 favours longer output, 0 disables normalisation.
    """
    return score / (max(n_tokens, 1) ** penalty)


def top_k_indices(scores: np.ndarray, k: int) -> np.ndarray:
    """Indices of the ``k`` largest entries of a 1-D array, best first.

    ``argpartition`` is O(V) against ``argsort``'s O(V log V), and V is 51 866 here
    on every decode step of every beam, so the distinction is not academic.
    """
    k = int(min(max(k, 1), scores.shape[-1]))
    part = np.argpartition(-scores, k - 1)[:k]
    return part[np.argsort(-scores[part])]


# ═══════════════════════════════════════════════════════════════════════════════
# The adapter a backend supplies
# ═══════════════════════════════════════════════════════════════════════════════

class BeamAdapter(Protocol):
    """How one runtime advances and reshuffles beams.

    ``state`` is opaque to the search and holds *all* live beams at once, which is
    what lets the batched and the one-call-per-beam runtimes share this code: the
    QNN adapter keeps a list of per-beam caches, the ONNX adapter keeps a dict of
    arrays whose leading axis is the beam.
    """

    def expand(self, n: int) -> Any:
        """Return a state holding ``n`` copies of the primed prompt state."""

    def step(self, last_tokens: Sequence[int], state: Any, index: int) -> tuple[np.ndarray, Any]:
        """Advance every beam one position.

        Returns ``(scores, new_state)`` where ``scores`` is ``[n, V]`` of
        log-probabilities, already masked for tokens that must not be emitted.
        """

    def reorder(self, state: Any, parents: Sequence[int]) -> Any:
        """Select/duplicate beams by parent index, in the given order."""


# ═══════════════════════════════════════════════════════════════════════════════
# The search
# ═══════════════════════════════════════════════════════════════════════════════

def beam_search(
    first_scores: np.ndarray,
    adapter: BeamAdapter,
    *,
    eot: int,
    beam_size: int,
    max_new_tokens: int,
    length_penalty: float = DEFAULT_LENGTH_PENALTY,
    start_index: int = 0,
    max_index: int | None = None,
) -> list[int]:
    """Length-normalised beam search over an already-primed decoder.

    ``first_scores`` is the ``[V]`` log-probability vector for the first *content*
    token, i.e. produced by running the forced prompt (``<|sot|> <|lang|>
    <|transcribe|> <|notimestamps|>``) once.  Priming once rather than per beam
    matters: every beam shares that prefix, so decoding it ``beam_size`` times
    would buy nothing.

    ``start_index`` is the decoder position the *next* token occupies, and
    ``max_index`` an optional hard ceiling imposed by the graph (the QNN export
    has a fixed-length cache and simply cannot address positions beyond it).

    Returns the best hypothesis' token ids, excluding the prompt and the EOT.
    """
    beam_size = max(1, int(beam_size))
    lp = length_penalty

    tokens: list[list[int]] = []
    beam_scores: list[float] = []
    finished: list[tuple[float, list[int]]] = []

    for tok in top_k_indices(first_scores, beam_size):
        tok = int(tok)
        score = float(first_scores[tok])
        if not np.isfinite(score):
            continue
        if tok == eot:
            # Whisper is entitled to decide the audio held no speech at all.
            finished.append((length_normalised(score, 0, lp), []))
        else:
            tokens.append([tok])
            beam_scores.append(score)

    if not tokens:
        return _best(finished, [], [], lp, hit_ceiling=False)

    state = adapter.expand(len(tokens))
    vocab = int(first_scores.shape[-1])
    hit_ceiling = True

    last = start_index + max_new_tokens - 1
    if max_index is not None:
        last = min(last, max_index)

    for index in range(start_index + 1, last + 1):
        if not tokens:
            hit_ceiling = False
            break

        step_scores, new_state = adapter.step([t[-1] for t in tokens], state, index)
        total = np.asarray(beam_scores, dtype=np.float32)[:, None] + step_scores
        flat = total.reshape(-1)

        new_tokens: list[list[int]] = []
        new_scores: list[float] = []
        parents: list[int] = []

        # 2*beam_size candidates: enough that EOT hypotheses retiring off the top
        # can never starve us of the continuations needed to refill every beam.
        for pos in top_k_indices(flat, min(2 * beam_size, flat.size)):
            score = float(flat[pos])
            if not np.isfinite(score):
                continue
            parent, tok = divmod(int(pos), vocab)
            if tok == eot:
                finished.append(
                    (length_normalised(score, len(tokens[parent]), lp), tokens[parent])
                )
            elif len(new_tokens) < beam_size:
                new_tokens.append(tokens[parent] + [tok])
                new_scores.append(score)
                parents.append(parent)
            if len(new_tokens) >= beam_size and len(finished) >= beam_size:
                break

        tokens, beam_scores = new_tokens, new_scores
        if tokens:
            state = adapter.reorder(new_state, parents)

        # Stop once no live beam can still beat what is already finished. Scores
        # only fall as a beam grows, so a beam's current normalised score is an
        # upper bound on where it can end up.
        if tokens and len(finished) >= beam_size:
            best_live = max(
                length_normalised(s, len(t), lp) for s, t in zip(beam_scores, tokens)
            )
            if best_live <= max(f[0] for f in finished):
                hit_ceiling = False
                break
    else:
        hit_ceiling = True

    return _best(finished, beam_scores, tokens, lp, hit_ceiling=hit_ceiling)


def _best(
    finished: list[tuple[float, list[int]]],
    beam_scores: Sequence[float],
    tokens: Sequence[list[int]],
    lp: float,
    *,
    hit_ceiling: bool,
) -> list[int]:
    """Pick the highest length-normalised hypothesis.

    Beams still running when the token ceiling was reached compete on equal terms
    with finished ones. Skipping that step loses long utterances outright: a
    run-on sentence that never emits EOT would otherwise hand back whatever
    low-probability EOT hypothesis happened to be recorded on the way, which can
    be empty — a caption that vanishes rather than being merely truncated.

    When the search *stopped early*, though, it did so having proven that no live
    beam could beat the finished ones, so those are excluded.
    """
    pool = list(finished)
    if hit_ceiling or not pool:
        pool += [
            (length_normalised(s, len(t), lp), t) for s, t in zip(beam_scores, tokens)
        ]
    if not pool:
        return []
    return max(pool, key=lambda f: f[0])[1]
