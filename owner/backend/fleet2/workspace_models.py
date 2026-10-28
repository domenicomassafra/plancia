"""Typed Action Item mutation request envelopes."""
from typing import Any, Dict
from pydantic import Field
from .request_models import StrictRequest

class ActionItemPatchRequest(StrictRequest):
    row_id: str = Field(min_length=1, max_length=80)
    changes: Dict[str, Any] = Field(min_length=1, max_length=12)
    preimage_hash: str = Field(default="", max_length=80)


class WorkspaceActionItemEditPreviewRequest(ActionItemPatchRequest):
    actor: str = Field(min_length=1, max_length=80)
    surface: str = Field(min_length=1, max_length=80)
    caller_type: str = Field(default="operator_ui", pattern="^(agent|operator_ui)$")
    agent_runtime: str = Field(default="", max_length=80)
    overlay_id: str | None = Field(default=None, max_length=80)
    idempotency_key: str = Field(min_length=1, max_length=200)
    provenance: Dict[str, Any] = Field(default_factory=dict, max_length=20)


class WorkspaceActionItemEditApplyRequest(WorkspaceActionItemEditPreviewRequest):
    operation_id: str = Field(min_length=1, max_length=80)
    packet_id: str = Field(min_length=1, max_length=80)
    confirmation: str = Field(default="", max_length=64)


class WorkspaceActionItemEditRollbackRequest(StrictRequest):
    operation_id: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=80)
    surface: str = Field(min_length=1, max_length=80)
    caller_type: str = Field(default="operator_ui", pattern="^(agent|operator_ui)$")
    agent_runtime: str = Field(default="", max_length=80)
    overlay_id: str | None = Field(default=None, max_length=80)
    confirmation: str = Field(default="", max_length=64)
