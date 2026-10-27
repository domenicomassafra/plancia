import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.fleet2 import workspace, workspace_sources

# Canonical Actions binding used by the test role fixture and fixtures below.
ACTIONS_ID = "00000000-0000-4000-8000-000000000001"


class WorkspaceTestBase(unittest.TestCase):
    PRIMARY_ROLE = {
        "workspace_id": "primary-id",
        "registry_data_source_id": "registry-id",
        "expected_names": ["Domenico"],
        "profile_env": {"NOTION_PROFILE": "primary"},
        "operational_data_sources": {"actions": ACTIONS_ID},
    }

    def setUp(self):
        role_patch = patch.object(
            workspace_sources,
            "primary_notion_role",
            return_value=self.PRIMARY_ROLE,
        )
        role_patch.start()
        self.addCleanup(role_patch.stop)
