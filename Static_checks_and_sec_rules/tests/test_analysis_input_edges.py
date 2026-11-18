"""Edge cases for analysis_input not exercised elsewhere."""
import pytest

from Static_checks_and_sec_rules.services import analysis_input as ai
from Static_checks_and_sec_rules.services.bandit_scanner import ScanError
from repo_diff_pipeline.models import ChangeType, DiffLine, FileDiff, Hunk


def _file(lines, *, change=ChangeType.ADDED, path='a.py'):
    return FileDiff(path=path, change_type=change, hunks=[Hunk(0, 0, 1, len(lines), lines)] if lines else [])


def test_reconstruct_empty_added_file_is_empty_string():
    assert ai._reconstruct_added_python_file(_file([])) == ''


def test_reconstruct_orders_lines_and_adds_trailing_newline():
    lines = [DiffLine('add', 'b', None, 2), DiffLine('add', 'a', None, 1)]
    assert ai._reconstruct_added_python_file(_file(lines)) == 'a\nb\n'


@pytest.mark.parametrize('bad', [
    DiffLine('context', 'x', 1, 1),     # non-add line in an "added" file
    DiffLine('add', 'x', None, None),   # add line with no target line number
])
def test_reconstruct_rejects_non_add_or_unnumbered_lines(bad):
    with pytest.raises(ScanError):
        ai._reconstruct_added_python_file(_file([bad]))


def test_reconstruct_rejects_gaps():
    with pytest.raises(ScanError):
        ai._reconstruct_added_python_file(_file([DiffLine('add', 'x', None, 2)]))


def test_is_scannable_python():
    assert ai._is_scannable_python(_file([]))
    assert not ai._is_scannable_python(_file([], change=ChangeType.DELETED))
    assert not ai._is_scannable_python(_file([], path='a.js'))
    binary = _file([])
    binary.is_binary = True
    assert not ai._is_scannable_python(binary)


def test_hunk_tracks_added_removed_and_context_lines_and_redacts():
    hunk = Hunk(10, 3, 10, 3, [
        DiffLine('context', 'ctx', 10, 10),
        DiffLine('del', 'old', 11, None),
        DiffLine('add', "password = 'CANARY_7f3a9e_value'", None, 11),
    ])
    out = ai.parsed_hunk_to_review_hunk(hunk, 'h1')
    assert (out['added_lines'], out['removed_lines'], out['context_lines']) == ([11], [11], [10])
    assert (out['start_line'], out['end_line']) == (10, 12)
    assert 'CANARY_7f3a9e_value' not in out['diff']
    assert out['diff'].startswith('@@ -10,3 +10,3 @@')


def test_zero_length_hunk_end_line_does_not_underflow():
    out = ai.parsed_hunk_to_review_hunk(Hunk(0, 0, 0, 0, []), 'h1')
    assert out['end_line'] == 0


def test_non_python_file_gets_extension_as_language_and_no_scan():
    review = ai.build_review_file(_file([DiffLine('add', 'x', None, 1)], path='style.css'), None)
    assert review['language'] == 'css' and review['static_findings'] == []


def test_static_analysis_can_be_disabled():
    review = ai.build_review_file(_file([DiffLine('add', 'import os', None, 1)]), None, run_static_analysis=False)
    assert review['static_findings'] == [] and review['language'] == 'python'


def test_modified_file_without_repo_is_not_scanned_and_noted():
    modified = _file([DiffLine('add', 'x = 1', None, 1)], change=ChangeType.MODIFIED)
    _, issues = ai.build_analysis_input(modified and ai.ParsedDiff(files=[modified]), None,
                                        submission_id='s', source_type='patch', repository='r')
    assert 'full file not available' in issues['a.py']
