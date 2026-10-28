"""Authenticated, read-only full-note endpoint for the canonical Obsidian vault."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from . import workspace_obsidian
from .workspace_obsidian_edits import can_edit, router as edits_router

router = APIRouter(prefix="/api/workspace/obsidian")
router.include_router(edits_router, prefix="")


@router.get("/note")
async def workspace_obsidian_note(
    path: str = Query(..., min_length=1, max_length=500),
) -> dict[str, Any]:
    try:
        note = await asyncio.to_thread(workspace_obsidian.obsidian_full_note, path)
        note["editable"] = can_edit(path)
        if note["editable"]:
            note["reason"] = None
        return note
    except OSError as exc:
        raise HTTPException(status_code=404, detail="nota non disponibile") from exc
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail="nota troppo grande") from exc
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail="nota non leggibile come Markdown UTF-8") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="nota cambiata durante la lettura") from exc
