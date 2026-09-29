import copy
import re


REDACTION_MARKER = "<REDACTED_SECRET>"

SENSITIVE_NAME_PATTERN = r"(?:password|passwd|pwd|api[_-]?key|token|secret)\w*"

SECRET_ASSIGNMENT_PATTERN = re.compile(
    rf"({SENSITIVE_NAME_PATTERN}\s*==?\s*[rbuf]{{0,2}})(\"\"\"|'''|\"|')(.*?)(\2)",
    re.IGNORECASE | re.DOTALL,
)

HARDCODED_PASSWORD_MESSAGE_PATTERN = re.compile(
    r"(?i)(hardcoded\s+password:\s*)(['\"])(.*?)(\2)"
)

SECRET_DICT_PATTERN = re.compile(
    rf'(["\']{SENSITIVE_NAME_PATTERN}["\']\s*:\s*)(["\'])(.*?)(\2)',
    re.IGNORECASE,
)


def _mask(match):
    newlines = "\n" * match.group(3).count("\n")

    return (
        f"{match.group(1)}"
        f"{match.group(2)}"
        f"{REDACTION_MARKER}"
        f"{newlines}"
        f"{match.group(4)}"
    )


def redact_text(text: str) -> str:
    redacted = SECRET_ASSIGNMENT_PATTERN.sub(
        _mask, 
        text,
    )

    redacted = HARDCODED_PASSWORD_MESSAGE_PATTERN.sub(
        _mask,
        redacted,
    )

    redacted = SECRET_DICT_PATTERN.sub(
        _mask,
        redacted,
    )

    return redacted


def redact_findings(results: dict) -> dict:
    redacted_results = copy.deepcopy(results)

    for finding in redacted_results.get("findings", []):
        for key, value in finding.items():
            if isinstance(value, str):
                finding[key] = redact_text(value)

    return redacted_results
