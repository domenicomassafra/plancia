"""Shared strict HTTP request envelope for the dashboard API."""
from pydantic import BaseModel, ConfigDict

class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
