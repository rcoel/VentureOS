"""LLM prompt files (loaded as strings by nodes)."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Load a prompt file by stem (e.g. 'extraction_github')."""
    path = _DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text()