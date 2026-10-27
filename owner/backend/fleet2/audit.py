"""Minimal, public audit projection shared by the fleet API routes."""

from __future__ import annotations

from typing import Any, Dict

PUBLIC_RECEIPT_PAYLOAD_KEYS = frozenset(
    {
        "source_key",
        "registry_id",
        "rollback_registry_id",
        "receipt_sha",
        "request_digest",
        "packet_id",
        "operation_id",
        "operation_type",
    }
)


def public_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Project a receipt for the browser without sending private operation bodies."""
    payload = receipt.get("payload") or {}
    public_payload = {
        key: value
        for key, value in payload.items()
        if key in PUBLIC_RECEIPT_PAYLOAD_KEYS and isinstance(value, (str, int, float, bool))
    }
    detail = str(receipt.get("detail") or "")
    public_detail = (
        "Conflitto o drift rilevato"
        if any(token in detail.casefold() for token in ("drift", "conflitt", "preimage", "non integra", "cambiat"))
        else None
    )
    return {
        "id": receipt.get("id"),
        "host_id": receipt.get("host_id"),
        "action": receipt.get("action"),
        "started_at": receipt.get("started_at"),
        "finished_at": receipt.get("finished_at"),
        "outcome": receipt.get("outcome"),
        "detail": public_detail,
        "payload": public_payload,
    }
