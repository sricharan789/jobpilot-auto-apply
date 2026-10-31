from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=4)
def _extract(path: str, mtime: float) -> str:  # mtime busts the cache when you upload a new resume
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(p)).pages).strip()
    if suffix == ".docx":
        import docx
        return "\n".join(par.text for par in docx.Document(str(p)).paragraphs).strip()
    return p.read_text(encoding="utf-8", errors="ignore").strip()


def resume_text(path: str) -> str:
    p = Path(path)
    if not path or not p.exists():
        raise FileNotFoundError(f"Resume not found at '{path}'. Upload it on the dashboard's Settings page.")
    return _extract(str(p), p.stat().st_mtime)
