"""Bounded Life OS adapters for the Signal Deck workspace.

The dashboard is a projection surface, not a second source of truth. Notion is
queried through the owner-proven ``ntn`` route and Obsidian is exposed as a
metadata-only, allowlisted index. Mutation packets are previewable, but live
application is Registry-gated and fails closed when identity, schema, preimage,
post-fetch, or rollback evidence does not match.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from . import workspace_sources

logger = logging.getLogger("fleet2.workspace")

ACTIONS_SOURCE_KEY = "actions"
ACTIONS_SOURCE_TITLE = "Actions"
ACTIONS_SOURCE_TITLE_PROPERTY = "Action"
ACTIONS_SOURCE_EDIT_SURFACE = "records"
OWNER_WORKSPACE = "Domenico"
MAX_ROWS = 50
MAX_NODES = 180
MAX_SEARCH_RESULTS = 40
MAX_FRONTMATTER_BYTES = 8192
MAX_LINK_SCAN_BYTES = 256_000
FRESHNESS_SECONDS = 120

ALLOWED_ROOTS = workspace_sources.OBSIDIAN_ALLOWED_ROOTS
EXCLUDED_NAMES = {
    ".git",
    ".obsidian",
    ".trash",
    "09-archive",
    "06-life",
    "01-input",
    "tmp",
    "_local",
}
EDITABLE_TYPES = {
    "title",
    "rich_text",
    "number",
    "checkbox",
    "select",
    "status",
    "date",
    "email",
    "url",
    "phone_number",
    "multi_select",
}
PROTECTED_RICH_TEXT_TOKENS = frozenset({"body", "description"})
LINK_RE = re.compile(r"!?(?:\[([^\]]+)\]\([^)]*?\)|\[\[([^\]]+)\]\])")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
