"""Vercel entrypoint: exposes the Flask app as a serverless function.

Vercel routes /api/* here (see vercel.json). The repo root is on sys.path so the
sibling packages (repo_diff_pipeline, llm_review, ...) import as they do locally.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / 'backend'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import create_app  # noqa: E402

app = create_app()
