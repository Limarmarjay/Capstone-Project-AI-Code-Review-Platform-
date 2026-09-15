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
