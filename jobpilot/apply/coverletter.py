from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from ..config import Profile

log = logging.getLogger(__name__)


def verify_pdf_text(path: Path, must_contain: list[str]) -> tuple[bool, str]:
    """ATS check: confirm the PDF's text layer extracts and the key details survive as literal text.
    An ATS reads the embedded text, not the rendered page, so a PDF that looks fine can still
    extract as garbage. Returns (ok, message)."""
    try:
        from pypdf import PdfReader
        text = "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
    except Exception as e:  # noqa: BLE001
        return False, f"could not read back the PDF ({e})"
    if len(text.strip()) < 100:
        return False, "cover letter PDF extracted almost no text (an ATS would see it as blank)"
    low = text.lower()
    missing = [m for m in must_contain if m and m.lower() not in low]
    if missing:
        return False, "these details did not survive into the PDF text layer: " + ", ".join(missing)
    return True, "PDF text layer verified"


def write_cover_letter_pdf(out_dir: Path, job: dict[str, Any], profile: Profile) -> Path:
    c = profile.candidate
    name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
    safe = re.sub(r"[^\w-]+", "_", f"{name}_Cover_Letter_{job['company']}")[:90]
    path = out_dir / f"{safe}.pdf"
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    body.fontSize, body.leading = 11, 15
    story = [Paragraph(f"<b>{name}</b>", styles["Heading3"]),
             Paragraph(" · ".join(str(x) for x in (c.get("email"), c.get("phone"), c.get("linkedin")) if x), body),
             Spacer(1, 18)]
    for para in (job.get("cover_letter") or "").split("\n\n"):
        if para.strip():
            story += [Paragraph(para.strip().replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>"), body),
                      Spacer(1, 8)]
    SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=60, rightMargin=60, topMargin=60).build(story)
    return path
