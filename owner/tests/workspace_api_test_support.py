import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.auth import (
    ALLOWED_ORIGINS_ENV,
    CSRF_COOKIE,
    CSRF_HEADER,
    OWNER_LOGIN_ENV,
    TAILSCALE_LOGIN_HEADER,
)
from backend.fleet2 import workspace
from backend.fleet2.api import _public_receipt

# Canonical Actions binding used by the mocked operation packets below.
ACTIONS_ID = "00000000-0000-4000-8000-000000000001"

OWNER = "owner@example.test"
ORIGIN = "https://fleet.example.test"


def _database():
    return {
        "source": {
            "state": "healthy",
            "available": True,
            "reason": None,
            "fetched_at": 1,
            "freshness_s": 0,
            "stale_after_s": 120,
            "kind": "notion",
            "workspace": "Domenico",
        },
        "registry": {
            "data_source_id": workspace._registry_data_source_id(),
            "required_for_write": True,
        },
        "schema": [
            {
                "id": "title",
                "name": "Action Item",
                "type": "title",
                "editable": True,
                "options": [],
            },
            {
                "id": "status",
                "name": "Status",
                "type": "status",
                "editable": True,
                "options": ["Done"],
            },
        ],
        "rows": [
            {
                "id": "row-1",
                "title": "Test",
                "url": None,
                "last_edited_time": "now",
                "properties": {"Action Item": "Test", "Status": "Not started"},
                "property_types": {"Action Item": "title", "Status": "status"},
            }
        ],
        "has_more": False,
        "next_cursor": None,
        "fetched_at": 1,
    }


def _daily():
    return {
        **_database(),
        "date": "2026-07-26",
        "row": None,
        "values": {
            "bedtime": "",
            "wake_time": "",
            "mood": None,
            "morning_journal": "",
            "evening_journal": "",
            "hours_slept": None,
            "sleep_quality": None,
        },
        "write_ready": True,
        "manual_gate": None,
    }


__all__ = (
    "os", "sys", "uuid", "Path", "patch", "TestClient",
    "ALLOWED_ORIGINS_ENV", "CSRF_COOKIE", "CSRF_HEADER",
    "OWNER_LOGIN_ENV", "TAILSCALE_LOGIN_HEADER", "workspace",
    "_public_receipt", "ACTIONS_ID", "OWNER", "ORIGIN",
    "_database", "_daily",
)
