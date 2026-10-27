"""Bounded owner previews and idempotent receipts; vault writes remain MCP-owned."""

import asyncio
import difflib
import hashlib
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import workspace_obsidian as notes
from . import workspace_obsidian_writer as writer

router = APIRouter(prefix="/operations/note.edit")


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=notes.MAX_EDITOR_CONTENT_BYTES)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=120)


class Apply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packet_id: uuid.UUID
    confirmation: str = Field(max_length=20)


def can_edit(path: str) -> bool:
    parts = path.split("/")
    return (writer.enabled() and parts[0] in {"04-areas", "05-knowledge"}
            and len(parts) > 1 and parts[-1].lower() not in {"readme.md", "index.md"}
            and not parts[-1].lower().endswith("-hub.md") and ".sync-conflict-" not in path)


def validate(payload: Preview) -> dict:
    if not can_edit(payload.path):
        raise HTTPException(403, "Modifica non consentita dalla policy del writer canonico")
    if len(payload.content.encode("utf-8")) > notes.MAX_EDITOR_CONTENT_BYTES or "\x00" in payload.content:
        raise HTTPException(422, "Contenuto troppo grande o non testuale")
    try:
        current = notes.obsidian_full_note(payload.path)
        target = notes._vault_root() / payload.path
        if target.stat().st_nlink != 1 or list(target.parent.glob(f"{target.stem}.sync-conflict-*.md")):
            raise HTTPException(409, "Conflitto Syncthing: riconciliare la nota prima di salvare")
    except (OSError, ValueError, OverflowError, RuntimeError) as exc:
        raise HTTPException(409, "Nota cambiata o non consentita: aggiornare la lettura") from exc
    if current["sha256"] != payload.expected_sha256:
        raise HTTPException(409, "Nota cambiata: conserva il testo e aggiorna la lettura")
    return current


@router.post("/preview")
async def preview(body: Preview, request: Request):
    current = await asyncio.to_thread(validate, body)
    packets = getattr(request.app.state, "obsidian_packets", {})
    now = time.time()
    packets = {key: item for key, item in packets.items() if item["expires_at"] > now}
    if len(packets) >= 100:
        raise HTTPException(429, "Troppe anteprime: attendi la scadenza")
    digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    for packet in packets.values():
        if packet["body"].idempotency_key == body.idempotency_key:
            if packet["digest"] != digest:
                raise HTTPException(409, "Chiave anteprima già usata per un'altra modifica")
            return packet["public"]
    packet_id = str(uuid.uuid4())
    diff = "".join(difflib.unified_diff(current["content"].splitlines(True), body.content.splitlines(True),
                                      fromfile="Prima", tofile="Dopo"))
    public = {"packet_id": packet_id, "path": body.path, "expires_at": now + 600,
              "before_sha256": body.expected_sha256,
              "after_sha256": hashlib.sha256(body.content.encode()).hexdigest(),
              "diff": diff[:32000], "diff_truncated": len(diff) > 32000, "mutation_count": 0}
    packets[packet_id] = {"body": body, "digest": digest, "expires_at": now + 600, "public": public}
    request.app.state.obsidian_packets = packets
    return public


@router.post("/apply")
async def apply(body: Apply, request: Request):
    if body.confirmation != "CONFERMA":
        raise HTTPException(422, "Scrivi CONFERMA per salvare la modifica in anteprima")
    packet_id = str(body.packet_id)
    store = request.app.state.store
    receipt_id = f"obsidian:{packet_id}"
    previous = await asyncio.to_thread(store.receipt, receipt_id)
    if previous:
        if previous["outcome"] != "succeeded":
            raise HTTPException(409, "Operazione già presa in carico: verifica la ricevuta in Audit")
        return {"receipt_id": receipt_id, "sha256": previous["payload"]["after_sha256"], "replayed": True}
    packet = getattr(request.app.state, "obsidian_packets", {}).get(packet_id)
    if not packet or packet["expires_at"] <= time.time():
        raise HTTPException(409, "Anteprima scaduta: prepara una nuova anteprima")
    payload = packet["body"]
    await asyncio.to_thread(validate, payload)
    claimed = await asyncio.to_thread(store.claim_receipt, receipt_id, "control-plane", "obsidian.note.edit",
                                      {"packet_id": packet_id, "after_sha256": packet["public"]["after_sha256"]})
    if not claimed:
        raise HTTPException(409, "Operazione già presa in carico: verifica Audit")
    try:
        result = await asyncio.to_thread(writer.write, payload.path, payload.content, payload.expected_sha256)
        readback = await asyncio.to_thread(notes.obsidian_full_note, payload.path)
        if readback["sha256"] != packet["public"]["after_sha256"] or result["sha256"] != readback["sha256"]:
            raise ValueError("postimage mismatch")
    except ValueError as exc:
        await asyncio.to_thread(store.close_receipt, receipt_id, "failed", "Conflitto o policy del writer")
        raise HTTPException(409, "Conflitto o rifiuto del writer: conserva il testo, aggiorna e verifica Audit") from exc
    except Exception as exc:
        await asyncio.to_thread(store.close_receipt, receipt_id, "interrupted", "Writer non verificato")
        raise HTTPException(503, "Esito non verificato: controlla la nota e Audit prima di riprovare") from exc
    await asyncio.to_thread(store.close_receipt, receipt_id, "succeeded", "Postimage verificata dal vault canonico")
    return {"receipt_id": receipt_id, "sha256": readback["sha256"], "replayed": False}
