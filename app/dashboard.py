from __future__ import annotations

from pathlib import Path

from fastapi.responses import HTMLResponse


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def render(name: str) -> HTMLResponse:
    return HTMLResponse((TEMPLATE_DIR / name).read_text(encoding="utf-8"))
