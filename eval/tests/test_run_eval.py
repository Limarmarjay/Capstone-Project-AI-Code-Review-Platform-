"""Unit tests for eval/run_eval.py's multi-seed aggregation logic:
fixture_passed, aggregate_across_seeds, _parse_seeds.

No Ollama, no network, no file I/O -- every test builds fake per-seed
result dicts by hand (via aggregate(), which is itself pure/offline) or
calls _parse_seeds directly.

Run:  python -m pytest eval/tests/test_run_eval.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from eval.run_eval import aggregate, aggregate_across_seeds, fixture_passed, _parse_seeds


def _fixture_result(
    case_id,
    *,
    must_catch_total=1,
    may_catch_total=0,
    matched_must_catch=None,
    missed_must_catch=None,
    matched_may_catch=None,
    missed_may_catch=None,
    must_not_flag_hits=None,
    unexpected=None,
    llm_status="success",
    latency_seconds=1.0,
):
    return {
        "case_id": case_id,
        "llm_status": llm_status,
        "latency_seconds": latency_seconds,
        "overall_score": 100,
        "must_catch_total": must_catch_total,
        "may_catch_total": may_catch_total,
        "matched_must_catch": matched_must_catch or [],
        "missed_must_catch": missed_must_catch or [],
        "matched_may_catch": matched_may_catch or [],
        "missed_may_catch": missed_may_catch or [],
        "must_not_flag_hits": must_not_flag_hits or [],
        "unexpected": unexpected or [],
    }


def _passing_fixture(case_id):
    return _fixture_result(
        case_id, matched_must_catch=[{"id": "mc1", "line": 1, "category": "bug", "link_correct": True}]
    )


def _failing_fixture(case_id):
    return _fixture_result(case_id, missed_must_catch=["mc1"])


def _clean_fixture(case_id):
    """must_catch_total=0, may_catch_total=0, nothing flagged -- a true
    clean-diff case. Passes trivially, and (if it's the only fixture in
    a seed's run) makes that seed's precision/recall both None, since
    both denominators are 0."""
    return _fixture_result(case_id, must_catch_total=0)


def _seed_entry(seed, fixtures):
    return {"seed": seed, "fixtures": fixtures, "summary": aggregate(fixtures)}


# ---- fixture_passed --------------------------------------------------------

def test_fixture_passed_true_when_fully_correct():
    assert fixture_passed(_passing_fixture("f")) is True


def test_fixture_passed_false_on_missed_must_catch():
    f = _fixture_result("f", missed_must_catch=["mc1"])
    assert fixture_passed(f) is False


def test_fixture_passed_false_on_unexpected_finding():
    f = _fixture_result("f", must_catch_total=0, unexpected=[{"line": 1, "category": "bug", "title": "x"}])
    assert fixture_passed(f) is False


def test_fixture_passed_false_on_must_not_flag_hit():
    f = _fixture_result(
        "f", must_catch_total=0, must_not_flag_hits=[{"line": 1, "category": "bug", "title": "x"}]
    )
    assert fixture_passed(f) is False


# ---- a fixture that's flaky across seeds: 3/5 = 60% ------------------------

def test_flaky_fixture_reports_partial_pass_rate_and_is_unstable():
    # 3 seeds pass, 2 fail -- plus an always-passing and an always-failing
    # fixture in the same seeds, to confirm those are NOT reported unstable.
    pattern = [True, False, True, True, False]  # 3/5 pass
    per_seed = [
        _seed_entry(seed, [
            _passing_fixture("always-pass"),
            _failing_fixture("always-fail"),
            _passing_fixture("flaky") if ok else _failing_fixture("flaky"),
        ])
        for seed, ok in enumerate(pattern)
    ]

    stability = aggregate_across_seeds(per_seed)
    rates = stability["per_fixture_pass_rate"]

    assert rates["flaky"] == {"passes": 3, "total": 5, "rate": 0.6}
    assert rates["always-pass"] == {"passes": 5, "total": 5, "rate": 1.0}
    assert rates["always-fail"] == {"passes": 0, "total": 5, "rate": 0.0}
    assert stability["unstable_fixtures"] == ["flaky"]


# ---- identical results across seeds: mean == min == max -------------------

def test_identical_seeds_give_mean_equal_to_min_equal_to_max():
    fixtures = [_passing_fixture("a"), _failing_fixture("b")]
    per_seed = [_seed_entry(seed, fixtures) for seed in (42, 1, 2)]

    stability = aggregate_across_seeds(per_seed)
    expected = aggregate(fixtures)

    for metric in ("precision", "recall"):
        assert stability[metric]["mean"] == expected[metric]
        assert stability[metric]["min"] == expected[metric]
        assert stability[metric]["max"] == expected[metric]


# ---- differing precision/recall across seeds: mean/min/max ----------------

def test_differing_seeds_compute_mean_min_max_correctly():
    # Seed A: 1/1 must_catch matched, no false positives -> precision=1.0, recall=1.0
    seed_a = [_passing_fixture("x")]
    # Seed B: 1/1 must_catch matched, plus one unexpected -> precision=0.5, recall=1.0
    seed_b = [
        _fixture_result(
            "x",
            matched_must_catch=[{"id": "mc1", "line": 1, "category": "bug", "link_correct": True}],
            unexpected=[{"line": 5, "category": "bug", "title": "extra"}],
        )
    ]
    # Seed C: must_catch missed entirely -> precision=None (denominator 0), recall=0.0
    seed_c = [_failing_fixture("x")]

    per_seed = [
        _seed_entry(1, seed_a),
        _seed_entry(2, seed_b),
        _seed_entry(3, seed_c),
    ]
    stability = aggregate_across_seeds(per_seed)

    # Sanity-check the premise: seed_c's precision really is None.
    assert per_seed[2]["summary"]["precision"] is None

    # precision: seed_c contributes None and is excluded from the stats,
    # so only seed_a's 1.0 and seed_b's 0.5 count.
    assert stability["precision"] == {"mean": 0.75, "min": 0.5, "max": 1.0}
    # recall: 1.0, 1.0, 0.0 -- all three count (recall's denominator,
    # must_catch_total, is never 0 here).
    assert stability["recall"]["min"] == 0.0
    assert stability["recall"]["max"] == 1.0
    assert abs(stability["recall"]["mean"] - (2 / 3)) < 1e-9


# ---- a single seed behaves like the non-seeds path -------------------------

def test_single_seed_matches_plain_aggregate_and_marks_nothing_unstable():
    fixtures = [_passing_fixture("a"), _failing_fixture("b"), _passing_fixture("c")]
    expected = aggregate(fixtures)

    stability = aggregate_across_seeds([_seed_entry(42, fixtures)])

    for metric in ("precision", "recall"):
        assert stability[metric] == {"mean": expected[metric], "min": expected[metric], "max": expected[metric]}
    # With one seed, every fixture is either 100% or 0% -- never in between.
    assert stability["unstable_fixtures"] == []


# ---- all-None precision/recall across every seed --------------------------

def test_all_none_precision_and_recall_gives_none_stats():
    # A clean fixture (must_catch_total=0, may_catch_total=0, nothing
    # flagged) has both precision and recall denominators at 0 in every
    # seed, so aggregate() reports None for both every time.
    per_seed = [_seed_entry(seed, [_clean_fixture("clean")]) for seed in (1, 2, 3)]

    for r in per_seed:
        assert r["summary"]["precision"] is None
        assert r["summary"]["recall"] is None

    stability = aggregate_across_seeds(per_seed)
    assert stability["precision"] == {"mean": None, "min": None, "max": None}
    assert stability["recall"] == {"mean": None, "min": None, "max": None}
    # Every seed passes trivially (nothing to miss or flag) -- stable, not unstable.
    assert stability["per_fixture_pass_rate"]["clean"] == {"passes": 3, "total": 3, "rate": 1.0}
    assert stability["unstable_fixtures"] == []


# ---- mismatched fixture lists across seeds ---------------------------------

def test_mismatched_fixture_lists_count_each_fixture_out_of_seeds_it_ran_in():
    # 'a' runs in all 3 seeds (2 passes, 1 fail); 'b' only in seeds 1 and 3
    # (1 pass, 1 fail); 'c' only in seeds 2 and 3 (2 passes). None of this
    # should crash, and each fixture's rate must be computed against the
    # seeds it actually appeared in, not against len(per_seed) == 3.
    per_seed = [
        _seed_entry(1, [_passing_fixture("a"), _passing_fixture("b")]),
        _seed_entry(2, [_passing_fixture("a"), _passing_fixture("c")]),
        _seed_entry(3, [_failing_fixture("a"), _failing_fixture("b"), _passing_fixture("c")]),
    ]

    stability = aggregate_across_seeds(per_seed)
    rates = stability["per_fixture_pass_rate"]

    assert rates["a"] == {"passes": 2, "total": 3, "rate": pytest.approx(2 / 3)}
    assert rates["b"] == {"passes": 1, "total": 2, "rate": 0.5}
    assert rates["c"] == {"passes": 2, "total": 2, "rate": 1.0}

    # 'a' and 'b' are each flaky within the seeds they ran in; 'c' is
    # consistently correct in the seeds it ran in, so it's not unstable.
    assert set(stability["unstable_fixtures"]) == {"a", "b"}


# ---- _parse_seeds -----------------------------------------------------------

def test_parse_seeds_handles_whitespace_and_multiple_values():
    assert _parse_seeds("42,1, 2") == [42, 1, 2]


def test_parse_seeds_exits_with_clear_message_on_invalid_value():
    with pytest.raises(SystemExit) as exc_info:
        _parse_seeds("42,abc")
    message = str(exc_info.value)
    assert "--seeds" in message
    assert "42,abc" in message
