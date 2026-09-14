from repo_diff_pipeline.validation import validate_input


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
