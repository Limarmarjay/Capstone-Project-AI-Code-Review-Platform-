"""Tests for build_user_prompt's line-number formatting (prompt.py).

Run:  python -m pytest llm_review/tests/test_prompt.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from llm_review.prompt import _format_hunk_with_line_numbers, build_user_prompt
from llm_review.types import Hunk, ReviewFile


def _hunk(diff, *, start_line=1, end_line=None, added=None, removed=None, context=None, hunk_id="h1"):
    return Hunk(
        hunk_id=hunk_id,
        start_line=start_line,
        end_line=end_line if end_line is not None else start_line,
        diff=diff,
        added_lines=added or [],
        removed_lines=removed or [],
        context_lines=context or [],
    )


# ---- the motivating case: added line two lines after the hunk header -----

def test_added_line_gets_its_actual_new_file_line_number():
    # Two context lines before the added lines -- the scenario that
    # previously produced a wrong "line" value: a model counting from
    # the hunk header's start_line (12) instead of the actual position
    # would land on 12, not the added line's real number, 14.
    diff = (
        "@@ -12,3 +12,5 @@\n"
        " line a\n"
        " line b\n"
        "+line c\n"
        "+line d\n"
        " line e"
    )
    h = _hunk(diff, start_line=12, end_line=16, added=[14, 15], context=[12, 13, 16])
    formatted = _format_hunk_with_line_numbers(h)

    lines = formatted.split("\n")
    assert lines[0] == "@@ -12,3 +12,5 @@"
    assert lines[1] == "   12 |  line a"
    assert lines[2] == "   13 |  line b"
    assert lines[3] == "   14 | +line c"
    assert lines[4] == "   15 | +line d"
    assert lines[5] == "   16 |  line e"


# ---- removed lines: unnumbered, marker kept ------------------------------

def test_removed_lines_are_unnumbered_with_marker():
    diff = (
        "@@ -5,3 +5,1 @@\n"
        " keep this\n"
        "-delete this\n"
        "-delete this too"
    )
    h = _hunk(diff, start_line=5, end_line=5, context=[5], removed=[6, 7])
    formatted = _format_hunk_with_line_numbers(h)
    lines = formatted.split("\n")

    assert lines[1] == "    5 |  keep this"
    assert lines[2] == "      | -delete this"
    assert lines[3] == "      | -delete this too"


# ---- pure-deletion hunk: no added/context lines at all -------------------

def test_pure_deletion_hunk_has_no_numbered_lines():
    diff = (
        "@@ -8,2 +7,0 @@\n"
        "-line one\n"
        "-line two"
    )
    h = _hunk(diff, start_line=7, end_line=7, removed=[8, 9])
    formatted = _format_hunk_with_line_numbers(h)
    lines = formatted.split("\n")

    assert lines[1] == "      | -line one"
    assert lines[2] == "      | -line two"


# ---- "\ No newline at end of file" ----------------------------------------

def test_no_newline_marker_passes_through_without_consuming_a_line_number():
    diff = (
        "@@ -1,1 +1,2 @@\n"
        " first\n"
        "+second\n"
        "\\ No newline at end of file"
    )
    h = _hunk(diff, start_line=1, end_line=2, added=[2], context=[1])
    formatted = _format_hunk_with_line_numbers(h)
    lines = formatted.split("\n")

    assert lines[1] == "    1 |  first"
    assert lines[2] == "    2 | +second"
    assert lines[3] == "\\ No newline at end of file"


# ---- fallback paths -------------------------------------------------------

def test_falls_back_to_raw_diff_when_computed_lines_mismatch():
    diff = "@@ -1,1 +1,1 @@\n+added"
    # Deliberately wrong added_lines so validation fails.
    h = _hunk(diff, start_line=1, end_line=1, added=[99])
    assert _format_hunk_with_line_numbers(h) == diff


def test_falls_back_to_raw_diff_when_header_missing():
    diff = " context with no header"
    h = _hunk(diff, start_line=1, end_line=1, context=[1])
    assert _format_hunk_with_line_numbers(h) == diff


def test_falls_back_to_raw_diff_on_unrecognized_marker():
    diff = "@@ -1,1 +1,1 @@\n*weird marker"
    h = _hunk(diff, start_line=1, end_line=1)
    assert _format_hunk_with_line_numbers(h) == diff


# ---- multiple hunks in one file -------------------------------------------

def test_multiple_hunks_are_each_numbered_from_their_own_start_line():
    file = ReviewFile(
        file_path="f.py",
        language="python",
        hunks=[
            _hunk(
                "@@ -1,1 +1,1 @@\n+top",
                start_line=1, end_line=1, added=[1], hunk_id="h1",
            ),
            _hunk(
                "@@ -50,1 +50,1 @@\n+bottom",
                start_line=50, end_line=50, added=[50], hunk_id="h2",
            ),
        ],
        static_findings=[],
    )
    prompt = build_user_prompt(file)
    assert "    1 | +top" in prompt
    assert "   50 | +bottom" in prompt


# ---- redaction stays intact through formatting -----------------------------

def test_redacted_content_is_preserved_through_formatting():
    diff = (
        "@@ -1,1 +1,2 @@\n"
        " context\n"
        "+    api_key = \"<REDACTED_SECRET>\""
    )
    h = _hunk(diff, start_line=1, end_line=2, added=[2], context=[1])
    formatted = _format_hunk_with_line_numbers(h)

    assert "<REDACTED_SECRET>" in formatted
    # The marker's presence is the thing under test, not any particular
    # plaintext secret -- there's no raw secret value here to assert the
    # absence of (this fixture's diff never contained one; redaction
    # already happened upstream, in pipeline._redacted_copy, before
    # build_user_prompt ever sees the text). This just confirms the
    # formatter doesn't corrupt or drop the marker while reformatting.
    assert formatted.count("<REDACTED_SECRET>") == diff.count("<REDACTED_SECRET>")
