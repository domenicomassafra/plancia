"""Cross-platform browser configuration: paths, exe detection, data directories."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _data_dir() -> Path:
    """Per-platform user data directory for the launcher's own config files."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = os.environ.get("XDG_DATA_HOME", str(Path.home() / "Library" / "Application Support"))
    else:
        base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / "chrome-profile-launcher"


DATA_DIR: Path = _data_dir()


@dataclass(frozen=True)
class BrowserConfig:
    key: str           # "chrome", "edge", "brave", "chromium"
    name: str          # "Chrome", "Edge", "Brave", "Chromium"
    exe_names: tuple[str, ...]     # process names: ("chrome.exe",) / ("Google Chrome",)
    profile_arg: str               # cli flag: "--profile-directory" or "--user-data-dir"
    user_data_dir: Path            # absolute path to User Data on this machine

    def detect_exe(self) -> str | None:
        """Return the best browser executable path, or None."""
        env_var = f"{self.key.upper()}_PATH"
        if env_path := os.environ.get(env_var):
            if Path(env_path).exists():
                return env_path

        candidates = _exe_candidates(self)
        for cand in candidates:
            if cand.exists():
                return str(cand)
        return None

    def exe_args(self, profile_directory: str) -> list[str]:
        """Command-line args to open a profile directory."""
        if self.profile_arg == "--user-data-dir":
            return [self.profile_arg, str(self.user_data_dir / profile_directory)]
        return [self.profile_arg + "=" + profile_directory]


def _exe_candidates(browser: BrowserConfig) -> list[Path]:
    """OS-specific paths where the browser executable likely lives."""
    if sys.platform == "win32":
        program_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
        program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"))
        local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))

        if browser.key == "chrome":
            return [
                program_files / "Google" / "Chrome" / "Application" / "chrome.exe",
                program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe",
                local_app_data / "Google" / "Chrome" / "Application" / "chrome.exe",
            ]
        elif browser.key == "edge":
            return [
                program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                program_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ]
        return []

    elif sys.platform == "darwin":
        if browser.key == "chrome":
            return [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications" / "Google Chrome.app" / "Contents/MacOS" / "Google Chrome",
            ]
        elif browser.key == "edge":
            return [
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path.home() / "Applications" / "Microsoft Edge.app" / "Contents/MacOS" / "Microsoft Edge",
            ]
        elif browser.key == "brave":
            return [
                Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            ]
        elif browser.key == "chromium":
            return [
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        return []

    else:  # Linux
        if browser.key == "chrome":
            return [
                Path("/usr/bin/google-chrome-stable"),
                Path("/usr/bin/google-chrome"),
                Path("/opt/google/chrome/google-chrome"),
                Path("/opt/google/chrome/chrome"),
            ]
        elif browser.key == "edge":
            return [
                Path("/usr/bin/microsoft-edge-stable"),
                Path("/usr/bin/microsoft-edge"),
                Path("/opt/microsoft/msedge/msedge"),
            ]
        elif browser.key == "brave":
            return [
                Path("/usr/bin/brave-browser"),
                Path("/opt/brave.com/brave/brave"),
            ]
        elif browser.key == "chromium":
            return [
                Path("/usr/bin/chromium-browser"),
                Path("/usr/bin/chromium"),
                Path("/snap/bin/chromium"),
            ]
        return []


def _chrome_user_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "Google" / "Chrome" / "User Data"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "google-chrome"


def _edge_user_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "Microsoft" / "Edge" / "User Data"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Microsoft Edge"
    else:
        return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "microsoft-edge"


def browser_configs() -> list[BrowserConfig]:
    """All detected browser configs. Edge and Chromium only if User Data exists."""
    configs: list[BrowserConfig] = []

    chrome_ud = _chrome_user_data_dir()
    if chrome_ud.is_dir():
        configs.append(BrowserConfig(
            key="chrome",
            name="Chrome",
            exe_names=("chrome.exe", "Google Chrome", "chrome"),
            profile_arg="--profile-directory",
            user_data_dir=chrome_ud,
        ))

    edge_ud = _edge_user_data_dir()
    if edge_ud.is_dir():
        configs.append(BrowserConfig(
            key="edge",
            name="Edge",
            exe_names=("msedge.exe", "Microsoft Edge", "msedge"),
            profile_arg="--profile-directory",
            user_data_dir=edge_ud,
        ))

    return configs