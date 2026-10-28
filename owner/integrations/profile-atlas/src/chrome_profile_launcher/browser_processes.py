"""Host-specific browser process names and desktop session environment."""
from __future__ import annotations
import os
import subprocess
import sys

LINUX_SESSION_ENV_KEYS = frozenset(
    {"XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}
)

def _browser_process_targets(browser_keys: set[str] | None = None) -> tuple[str, ...]:
    """Browser main-process names to probe, per platform and target set."""

    selected = browser_keys or {"chrome", "edge"}
    if sys.platform == "darwin":
        mapping = {
            "chrome": ("Google Chrome",),
            "edge": ("Microsoft Edge",),
        }
    elif sys.platform == "win32":
        mapping = {
            "chrome": ("chrome.exe",),
            "edge": ("msedge.exe",),
        }
    else:
        mapping = {
            "chrome": ("google-chrome", "chrome"),
            "edge": ("microsoft-edge", "msedge"),
        }
    return tuple(
        process
        for key in ("chrome", "edge")
        if key in selected
        for process in mapping[key]
    )


def _browser_process_environment() -> dict[str, str]:
    env = dict(os.environ)
    if sys.platform.startswith("linux"):
        # SSH sessions normally do not inherit the live desktop's Wayland
        # socket.  In particular Hyprland may be on wayland-1 while a guessed
        # wayland-0 exists only on other hosts.  The user systemd manager owns
        # the desktop environment imported at login, so prefer its bounded set
        # of display/session variables and retain conservative fallbacks only
        # when that read is unavailable.
        try:
            completed = subprocess.run(
                ["systemctl", "--user", "show-environment"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0:
            for line in completed.stdout.splitlines():
                key, separator, value = line.partition("=")
                if separator and key in LINUX_SESSION_ENV_KEYS and value:
                    env[key] = value
        runtime_dir = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DISPLAY", ":0")
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    return env
