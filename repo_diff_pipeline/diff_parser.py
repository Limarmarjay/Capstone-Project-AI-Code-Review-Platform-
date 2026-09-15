"""Parse unified diff text into structured FileDiff/Hunk objects."""
from __future__ import annotations

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from .models import ChangeType, DiffLine, FileDiff, Hunk, ParsedDiff

# Guard against pathological diffs (e.g. huge generated files, minified
# bundles, or adversarial submissions) blowing up memory/CPU downstream.
MAX_FILES = 500
MAX_HUNK_LINES_PER_FILE = 20_000


class DiffParseError(Exception):
    pass


def _classify_change(patched_file) -> ChangeType:
    if patched_file.is_added_file:
        return ChangeType.ADDED
    if patched_file.is_removed_file:
        return ChangeType.DELETED
    if patched_file.is_rename:
        return ChangeType.RENAMED
    return ChangeType.MODIFIED


def parse_diff(diff_text: str) -> ParsedDiff:
    """
    Parse unified diff text (as produced by `git diff`) into a ParsedDiff.
    Raises DiffParseError on malformed input or if size limits are exceeded,
    rather than letting a hostile/corrupt diff consume unbounded resources.
    """
    if not diff_text.strip():
        return ParsedDiff(files=[])

    try:
        patch_set = PatchSet(diff_text)
    except UnidiffParseError as e:
        raise DiffParseError(f"Could not parse diff: {e}") from e

    if len(patch_set) > MAX_FILES:
        raise DiffParseError(
            f"Diff touches {len(patch_set)} files, exceeding limit of {MAX_FILES}."
        )

    files: list[FileDiff] = []
    for patched_file in patch_set:
        path = patched_file.path
        change_type = _classify_change(patched_file)
        old_path = (
            patched_file.source_file.lstrip("a/")
            if change_type == ChangeType.RENAMED
            else None
        )

        file_diff = FileDiff(
            path=path,
            change_type=change_type,
            old_path=old_path,
            is_binary=patched_file.is_binary_file,
        )

        line_count = 0
        for hunk in patched_file:
            parsed_hunk = Hunk(
                source_start=hunk.source_start,
                source_length=hunk.source_length,
                target_start=hunk.target_start,
                target_length=hunk.target_length,
            )
            for line in hunk:
                line_count += 1
                if line_count > MAX_HUNK_LINES_PER_FILE:
                    raise DiffParseError(
                        f"File '{path}' exceeds {MAX_HUNK_LINES_PER_FILE} changed "
                        "lines; refusing to process (possible generated/minified "
                        "file or oversized submission)."
                    )
                if line.is_added:
                    line_type = "add"
                elif line.is_removed:
                    line_type = "del"
                else:
                    line_type = "context"
                parsed_hunk.lines.append(
                    DiffLine(
                        line_type=line_type,
                        content=line.value.rstrip("\n"),
                        source_line_no=line.source_line_no,
                        target_line_no=line.target_line_no,
                    )
                )
            file_diff.hunks.append(parsed_hunk)

        files.append(file_diff)

    return ParsedDiff(files=files)
