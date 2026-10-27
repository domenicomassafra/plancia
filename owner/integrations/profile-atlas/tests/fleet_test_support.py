from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chrome_profile_launcher import AGENT_PROTOCOL_VERSION, __version__
from chrome_profile_launcher.cli import main as cli_main
from chrome_profile_launcher.config import BrowserConfig
from chrome_profile_launcher.fleet import (
    _browser_process_environment,
    current_host_id,
    delete_local_targets,
    local_inventory,
    normalize_email,
    open_local_targets,
)
from chrome_profile_launcher.profiles import _load_browser_profiles
from chrome_profile_launcher.sync import (
    _close_browsers,
    preview_local_rename_targets,
    rename_local_targets,
)


def _browser(root: Path, key: str = "chrome") -> BrowserConfig:
    return BrowserConfig(
        key=key,
        name=key.title(),
        exe_names=(),
        profile_arg="--profile-directory",
        user_data_dir=root / key,
    )


def _write_profile(browser: BrowserConfig, directory: str, email: str) -> None:
    profile_dir = browser.user_data_dir / directory
    profile_dir.mkdir(parents=True)
    (profile_dir / "Preferences").write_text(
        json.dumps({"profile": {"name": email}, "account_info": [{"email": email}]}),
        encoding="utf-8",
    )


def _write_local_state(browser: BrowserConfig, profiles: dict[str, str]) -> None:
    browser.user_data_dir.mkdir(parents=True, exist_ok=True)
    info_cache = {
        directory: {"name": email, "user_name": email}
        for directory, email in profiles.items()
    }
    (browser.user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": info_cache}}),
        encoding="utf-8",
    )
