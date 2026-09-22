"""'Input Validation' section: cheap sanity checks before the (more
expensive) diff parsing and AI review steps run."""
from __future__ import annotations

from .models import FileDiff

MAX_RAW_DIFF_BYTES = 5 * 1024 * 1024  # 5 MB


def validate_python_only(files: list[FileDiff]) -> str | None:
    """
    This platform only reviews Python code. Returns an error message if the
    diff touches any non-Python file, or None if every file is a '.py' file.
    """
    non_python = sorted({f.path for f in files if f.extension != "py"})
    if not non_python:
        return None
    shown = non_python[:10]
    remainder = len(non_python) - len(shown)
    listed = ", ".join(shown) + (f", and {remainder} more" if remainder else "")
    return f"Only Python (.py) files are supported. Found non-Python file(s): {listed}"


def validate_input(diff_text: str) -> str | None:
    """
    Returns an error message ('Invalid input' reason) if the submission
    should be rejected, or None if it's good to proceed to diff parsing.
    """
    if diff_text is None:
        return "No diff content received."
    if not diff_text.strip():
        return "Diff content is empty."
    if len(diff_text.encode("utf-8", errors="ignore")) > MAX_RAW_DIFF_BYTES:
        return f"Diff exceeds maximum size of {MAX_RAW_DIFF_BYTES} bytes."
    if "\x00" in diff_text:
        return "Diff content appears to be binary, not a text diff."
    if "diff --git" not in diff_text and "--- " not in diff_text:
        return "Content does not look like a unified diff or patch file."
    return None
