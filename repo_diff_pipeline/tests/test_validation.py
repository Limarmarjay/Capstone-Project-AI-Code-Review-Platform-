from repo_diff_pipeline.models import ChangeType, FileDiff
from repo_diff_pipeline.validation import validate_input, validate_python_only


def test_empty_input_is_invalid():
    assert validate_input("") is not None
    assert validate_input("   \n  ") is not None


def test_none_input_is_invalid():
    assert validate_input(None) is not None


def test_non_diff_text_is_invalid():
    assert validate_input("just some random text, not a diff") is not None


def test_valid_diff_passes():
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    assert validate_input(diff) is None


def test_oversized_input_is_invalid():
    huge = "diff --git a/x.py b/x.py\n" + ("+line\n" * 2_000_000)
    assert validate_input(huge) is not None


def _file(path: str) -> FileDiff:
    return FileDiff(path=path, change_type=ChangeType.MODIFIED)


def test_all_python_files_pass():
    assert validate_python_only([_file("a.py"), _file("pkg/b.py")]) is None


def test_no_files_passes():
    assert validate_python_only([]) is None


def test_non_python_file_is_rejected():
    reason = validate_python_only([_file("a.py"), _file("b.js")])
    assert reason is not None
    assert "b.js" in reason


def test_mixed_diff_rejected_even_with_python_files_present():
    reason = validate_python_only([_file("a.py"), _file("README.md"), _file("config.yaml")])
    assert reason is not None
    assert "README.md" in reason
    assert "config.yaml" in reason
