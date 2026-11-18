from repo_diff_pipeline.diff_parser import DiffParseError, parse_diff
from repo_diff_pipeline.models import ChangeType

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
index 4693ad3..67f5227 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,4 @@
 def add(a, b):
     return a + b
+
+print(add(1, 2))
"""


def test_parse_empty_diff_returns_no_files():
    result = parse_diff("")
    assert result.files == []


def test_parse_simple_diff_counts_additions():
    result = parse_diff(SAMPLE_DIFF)
    assert len(result.files) == 1
    f = result.files[0]
    assert f.path == "app.py"
    assert f.change_type == ChangeType.MODIFIED
    assert f.additions == 2
    assert f.deletions == 0


def test_parse_added_file():
    diff = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+print('hi')\n"
        "+print('bye')\n"
    )
    result = parse_diff(diff)
    assert result.files[0].change_type == ChangeType.ADDED
    assert result.files[0].additions == 2


def test_oversized_file_diff_raises():
    lines = [
        "diff --git a/big.py b/big.py",
        "--- a/big.py",
        "+++ b/big.py",
        "@@ -1,1 +1,20010 @@",
    ]
    lines += [f"+line{i}" for i in range(20005)]
    diff_text = "\n".join(lines)
    try:
        parse_diff(diff_text)
        assert False, "expected DiffParseError"
    except DiffParseError:
        pass


# --- additional coverage --------------------------------------------------------

import pytest

from repo_diff_pipeline import diff_parser
from repo_diff_pipeline.models import ChangeType


def test_garbage_hunk_raises_parse_error():
    with pytest.raises(diff_parser.DiffParseError):
        diff_parser.parse_diff('diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n')


def test_too_many_files_rejected(monkeypatch):
    monkeypatch.setattr(diff_parser, 'MAX_FILES', 1)
    text = ''.join(
        f"diff --git a/{n}.py b/{n}.py\n--- a/{n}.py\n+++ b/{n}.py\n@@ -1 +1 @@\n-a\n+b\n" for n in 'xy'
    )
    with pytest.raises(diff_parser.DiffParseError, match='exceeding limit'):
        diff_parser.parse_diff(text)


def test_rename_detected():
    diff = diff_parser.parse_diff(
        'diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n'
    )
    assert diff.files[0].change_type is ChangeType.RENAMED and diff.files[0].path == 'new.py'


@pytest.mark.xfail(strict=True, reason="BUG: parse_diff uses lstrip('a/'), which strips a character "
                   "set, so 'apple.py' becomes 'pple.py'. Should be removeprefix('a/').")
def test_rename_preserves_old_path_starting_with_a():
    diff = diff_parser.parse_diff(
        'diff --git a/apple.py b/fruit.py\nsimilarity index 100%\nrename from apple.py\nrename to fruit.py\n'
    )
    assert diff.files[0].old_path == 'apple.py'


def test_per_file_changed_line_limit_is_enforced(monkeypatch):
    # Complements test_oversized_file_diff_raises, which does not reach the per-file counter.
    monkeypatch.setattr(diff_parser, 'MAX_HUNK_LINES_PER_FILE', 2)
    with pytest.raises(diff_parser.DiffParseError, match='exceeds'):
        diff_parser.parse_diff(
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,3 @@\n+1\n+2\n+3\n"
        )
