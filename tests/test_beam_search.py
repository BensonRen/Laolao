"""Unit tests for the shared beam-search decoder.

These drive ``backends.beam_search`` against a *scripted* toy language model, so
they need no Whisper weights, no onnxruntime and no NPU — which means they run in
CI on every platform, including the one where the real backend cannot even be
imported.

The toy model is the point: beam search only earns its cost on inputs where the
locally-best token is not on the globally-best path, and a real model gives you no
way to construct that case on purpose.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backends.beam_search import (  # noqa: E402
    beam_search,
    length_normalised,
    log_softmax,
    top_k_indices,
)

EOT = 0


class ScriptedAdapter:
    """A beam adapter over a hand-written next-token table.

    ``table`` maps a tuple of already-generated tokens to a dict of
    ``{token: probability}``.  Anything unlisted gets a flat, very low
    probability, so a hypothesis that wanders off-script dies on its own.
    """

    def __init__(self, table: dict[tuple[int, ...], dict[int, float]], vocab: int) -> None:
        self.table = table
        self.vocab = vocab
        self.calls = 0
        self.step_calls = 0

    # The state is just each beam's token history — enough to look up the table.
    def expand(self, n: int) -> list[tuple[int, ...]]:
        return [()] * n

    def step(self, last_tokens, state, index):
        self.step_calls += 1
        rows, new_state = [], []
        for token, history in zip(last_tokens, state):
            hist = tuple(history) + (int(token),)
            self.calls += 1
            rows.append(self._row(hist))
            new_state.append(hist)
        return np.stack(rows), new_state

    def reorder(self, state, parents):
        return [state[p] for p in parents]

    def _row(self, history: tuple[int, ...]) -> np.ndarray:
        probs = np.full(self.vocab, 1e-9, dtype=np.float32)
        for token, p in self.table.get(history, {}).items():
            probs[token] = p
        return log_softmax(np.log(probs))


def first_scores(probs: dict[int, float], vocab: int) -> np.ndarray:
    row = np.full(vocab, 1e-9, dtype=np.float32)
    for token, p in probs.items():
        row[token] = p
    return log_softmax(np.log(row))


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def test_log_softmax_normalises_to_one() -> None:
    out = np.exp(log_softmax(np.array([1.0, 2.0, 3.0], dtype=np.float32)))
    assert out.sum() == pytest.approx(1.0, abs=1e-6)


def test_log_softmax_keeps_suppressed_tokens_impossible() -> None:
    """A masked token must stay at -inf, not merely become unlikely."""
    logits = np.array([1.0, -np.inf, 3.0], dtype=np.float32)
    out = log_softmax(logits)
    assert out[1] == -np.inf
    assert np.exp(out[[0, 2]]).sum() == pytest.approx(1.0, abs=1e-6)


def test_log_softmax_survives_an_all_masked_row() -> None:
    """Every token suppressed must not produce NaN and crash the decode."""
    out = log_softmax(np.array([-np.inf, -np.inf], dtype=np.float32))
    assert not np.isnan(out).any()


def test_length_normalisation_prefers_the_longer_equal_quality_hypothesis() -> None:
    """Two tokens at -1.0 each must beat one token at -1.5."""
    assert length_normalised(-2.0, 2, 1.0) > length_normalised(-1.5, 1, 1.0)


def test_top_k_indices_returns_best_first() -> None:
    scores = np.array([0.1, 0.9, 0.5, 0.7], dtype=np.float32)
    assert list(top_k_indices(scores, 3)) == [1, 3, 2]


def test_top_k_indices_clamps_to_vocabulary_size() -> None:
    assert len(top_k_indices(np.array([0.2, 0.8], dtype=np.float32), 10)) == 2


# ---------------------------------------------------------------------------
# The search itself
# ---------------------------------------------------------------------------

def _run(adapter, first, beams, **kw):
    return beam_search(
        first, adapter, eot=EOT, beam_size=beams,
        max_new_tokens=kw.pop("max_new_tokens", 10), **kw,
    )


def test_beam_search_finds_the_path_greedy_decoding_misses() -> None:
    """The whole reason this code exists.

    Token 1 looks better than token 2 at the first position (0.6 vs 0.4), but it
    leads into a genuinely uncertain continuation (a 50/50 split), while token 2
    leads somewhere the model is sure of. Greedy takes the early bait; beam search
    keeps both alive long enough to see that path 2 scores better overall.

    Note the split has to be *within* a row: only the relative weights inside one
    step matter, since each row is renormalised. An earlier version of this
    fixture gave the dead end a single 0.1 continuation, which renormalises to
    1.0 and made both paths score identically.
    """
    vocab = 8
    table = {
        (1,): {3: 0.5, 6: 0.5},
        (1, 3): {EOT: 1.0},
        (1, 6): {EOT: 1.0},
        (2,): {4: 1.0},
        (2, 4): {EOT: 1.0},
    }
    first = first_scores({1: 0.6, 2: 0.4}, vocab)

    greedy = _run(ScriptedAdapter(table, vocab), first, 1)
    beamed = _run(ScriptedAdapter(table, vocab), first, 4)

    assert greedy == [1, 3]
    assert beamed == [2, 4]


def test_beam_search_matches_greedy_when_greedy_is_already_right() -> None:
    """Beam search must not "improve" a decode that had no ambiguity."""
    vocab = 8
    table = {(1,): {2: 0.99}, (1, 2): {EOT: 1.0}}
    first = first_scores({1: 0.9, 5: 0.1}, vocab)
    assert _run(ScriptedAdapter(table, vocab), first, 4) == [1, 2]


def test_beam_size_one_is_greedy() -> None:
    vocab = 8
    table = {(1,): {3: 0.5, 6: 0.5}, (1, 3): {EOT: 1.0}, (2,): {4: 1.0}, (2, 4): {EOT: 1.0}}
    first = first_scores({1: 0.6, 2: 0.4}, vocab)
    assert _run(ScriptedAdapter(table, vocab), first, 1) == [1, 3]


def test_immediate_eot_yields_empty_transcript() -> None:
    """Silence: Whisper must be allowed to say the audio held nothing."""
    vocab = 8
    first = first_scores({EOT: 0.99, 1: 0.01}, vocab)
    assert _run(ScriptedAdapter({}, vocab), first, 4) == []


def test_hitting_the_token_ceiling_still_returns_text() -> None:
    """A run-on utterance must return its truncated text, never nothing."""
    vocab = 8
    table = {tuple([1] * n): {1: 0.99} for n in range(1, 12)}
    first = first_scores({1: 0.99}, vocab)
    out = _run(ScriptedAdapter(table, vocab), first, 3, max_new_tokens=6)
    assert out == [1] * 6


def test_max_index_ceiling_is_respected() -> None:
    """The QNN graph's fixed cache length is a hard limit, not a suggestion."""
    vocab = 8
    table = {tuple([1] * n): {1: 0.99} for n in range(1, 12)}
    first = first_scores({1: 0.99}, vocab)
    out = _run(ScriptedAdapter(table, vocab), first, 2,
               max_new_tokens=50, start_index=3, max_index=6)
    assert len(out) == 4          # positions 4,5,6 stepped, plus the first token


def test_one_decoder_call_per_step_regardless_of_candidate_count() -> None:
    """Cost must scale with beams, not with the candidates they generate.

    On the QNN lane every call is an NPU round trip, so a beam search that
    stepped per *candidate* rather than per *beam* would silently cost several
    times the latency budget.
    """
    vocab = 8
    table = {tuple([1] * n): {1: 0.9, 2: 0.05, 3: 0.04} for n in range(1, 8)}
    table.update({(1, 1, 1, 1): {EOT: 1.0}})
    adapter = ScriptedAdapter(table, vocab)
    _run(adapter, first_scores({1: 0.9, 2: 0.1}, vocab), 3, max_new_tokens=4)
    # 3 lockstep steps x at most 3 live beams
    assert adapter.calls <= 3 * 3
    assert adapter.step_calls <= 3


def test_length_penalty_zero_stops_favouring_long_output() -> None:
    """penalty=0 disables normalisation, restoring the short-hypothesis bias."""
    vocab = 8
    table = {
        (1,): {EOT: 0.9, 2: 0.1},
        (1, 2): {EOT: 1.0},
    }
    first = first_scores({1: 0.99}, vocab)
    short = _run(ScriptedAdapter(table, vocab), first, 4, length_penalty=0.0)
    assert short == [1]
