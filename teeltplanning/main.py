"""Webapp-ingang. Fase 0: alleen een lege app met `/health` (zie docs/BOUWPLAN.md §14)."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__

app = FastAPI(title="Teeltplanning", version=__version__, docs_url=None, redoc_url=None)


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "versie": __version__}
