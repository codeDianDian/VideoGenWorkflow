"""Stage 1: load a script from .md / .txt / .pdf into a single string."""
from __future__ import annotations

from pathlib import Path


def load_script(path: str | Path) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    suffix = p.suffix.lower()
    if suffix in {".md", ".txt"}:
        return p.read_text(encoding="utf-8")
    if suffix == ".pdf":
        import pdfplumber

        chunks: list[str] = []
        with pdfplumber.open(p) as pdf:
            for page in pdf.pages:
                chunks.append(page.extract_text() or "")
        return "\n\n".join(chunks)
    raise ValueError(f"Unsupported script format: {suffix}")
