"""Fleet inventory and guarded profile actions across the owner's four PCs."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

from . import AGENT_PROTOCOL_VERSION, __version__
from .browser_processes import _browser_process_environment
from .config import BrowserConfig, browser_configs
from .profiles import PROFILE_DIRECTORY_RE, Profile, _load_browser_profiles, _read_json

LOCAL_HOSTS = {
    "mac-studio": "Mac Studio",
    "macbook-air": "MacBook Air",
    "matebook": "MateBook",
    "minipc": "MiniPC",
}
LOCAL_HOST_IDS = frozenset(LOCAL_HOSTS)
VALID_BROWSERS = {"chrome", "edge"}
DELETABLE_PROFILE_DIRECTORY_RE = re.compile(r"^Profile(?: \d+)?$")

def normalize_email(value: object) -> str:
    """Return the canonical account identity used for cross-host matching."""
    return value.strip().casefold() if isinstance(value, str) else ""


def current_host_id() -> str:
    """Resolve this machine inside the fixed owner fleet."""
    override = os.environ.get("CHROME_PROFILES_HOST_ID", "").strip().casefold()
    if override in LOCAL_HOST_IDS:
        return override

    hostname = socket.gethostname().casefold()
    if "macbook-air" in hostname or "macbookair" in hostname:
        return "macbook-air"
    if "mac-studio" in hostname or "macstudio" in hostname:
        return "mac-studio"
    if "matebook" in hostname:
        return "matebook"
    if hostname == "udodo" or "minipc" in hostname:
        return "minipc"
    if sys.platform == "win32":
        return "matebook"
    if sys.platform == "darwin":
        return "mac-studio"
    return "minipc"


def _host(host_id: str) -> dict:
    return {"id": host_id, "label": LOCAL_HOSTS.get(host_id, host_id)}


def _profiles_for_configs(configs: Iterable[BrowserConfig]) -> list[Profile]:
    profiles: list[Profile] = []
    for browser in configs:
        profiles.extend(_load_browser_profiles(browser))
    return profiles


def _profile_record(profile: Profile) -> dict[str, object]:
    email = normalize_email(profile.email)
    return {
        "browser": profile.browser.key,
        "email": email,
        "label": profile.label,
        "policy_label": profile.policy_label,
        "name_policy_ok": profile.name_policy_ok,
        "directory": profile.directory,
        "path": str(profile.path),
        "search": profile.search_text,
    }


def _delete_record(profile: Profile) -> dict[str, str]:
    """Return delete evidence without leaking the absolute profile path."""

    return {
        "browser": profile.browser.key,
        "email": normalize_email(profile.email),
        "label": profile.label,
        "directory": profile.directory,
    }


def _active_browser_processes() -> list[str]:
    """Detect active browsers without closing or otherwise touching them."""

    if sys.platform == "darwin":
        commands = (("Google Chrome", ["pgrep", "-x", "Google Chrome"]), ("Microsoft Edge", ["pgrep", "-x", "Microsoft Edge"]))
    elif sys.platform == "win32":
        commands = (("chrome.exe", ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"]), ("msedge.exe", ["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/NH"]))
    else:
        commands = tuple((process, ["pgrep", "-x", process]) for process in ("google-chrome", "chrome", "microsoft-edge", "msedge"))

    active: list[str] = []
    for label, command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            # An unavailable detector must not make deletion appear safer.
            return ["activity-check-unavailable"]
        if sys.platform == "win32":
            if completed.returncode == 0 and label.casefold() in completed.stdout.casefold():
                active.append(label)
        elif completed.returncode == 0:
            active.append(label)
    return active


def _browser_key_for_process(process: str) -> str | None:
    normalized = process.casefold()
    if "edge" in normalized:
        return "edge"
    if "chrome" in normalized:
        return "chrome"
    return None


def _close_browser_processes(browser_keys: set[str]) -> None:
    """Close only the browser families needed by the pending deletion."""

    if sys.platform == "darwin":
        apps = {"chrome": "Google Chrome", "edge": "Microsoft Edge"}
        for browser_key in sorted(browser_keys):
            app = apps[browser_key]
            subprocess.run(
                ["osascript", "-e", f'tell application "{app}" to quit'],
                capture_output=True,
                timeout=5,
            )
        time.sleep(2)
        for browser_key in sorted(browser_keys):
            subprocess.run(
                ["pkill", "-x", apps[browser_key]], capture_output=True, timeout=5
            )
    elif sys.platform == "win32":
        executables = {"chrome": "chrome.exe", "edge": "msedge.exe"}
        for browser_key in sorted(browser_keys):
            subprocess.run(
                ["taskkill", "/F", "/IM", executables[browser_key]],
                capture_output=True,
                timeout=5,
            )
    else:
        processes = {
            "chrome": ("google-chrome", "chrome"),
            "edge": ("microsoft-edge", "msedge"),
        }
        for browser_key in sorted(browser_keys):
            for process in processes[browser_key]:
                subprocess.run(
                    ["pkill", "-x", process], capture_output=True, timeout=5
                )
    time.sleep(1)



def open_local_targets(raw_targets: Iterable[dict]) -> dict:
    """Open exact browser/profile-directory targets from a fresh inventory."""
    exact_targets = {
        (
            str(item.get("browser", "")).strip().casefold(),
            normalize_email(item.get("email")),
            str(item.get("directory", "")).strip(),
        )
        for item in raw_targets
        if isinstance(item, dict)
    }
    exact_targets = {
        target
        for target in exact_targets
        if target[0] in VALID_BROWSERS and PROFILE_DIRECTORY_RE.fullmatch(target[2])
    }
    legacy_targets = {
        (
            str(item.get("browser", "")).strip().casefold(),
            normalize_email(item.get("email")),
        )
        for item in raw_targets
        if isinstance(item, dict) and not str(item.get("directory", "")).strip()
    }
    legacy_targets = {
        target for target in legacy_targets if target[0] in VALID_BROWSERS and target[1]
    }
    if not exact_targets and not legacy_targets:
        return {"ok": False, "error": "Nessun profilo valido.", "opened": []}

    configs = {browser.key: browser for browser in browser_configs()}
    profiles = [
        profile
        for profile in _profiles_for_configs(configs.values())
        if (
            (
                profile.browser.key,
                normalize_email(profile.email),
                profile.directory,
            )
            in exact_targets
            or (profile.browser.key, normalize_email(profile.email)) in legacy_targets
        )
    ]
    matched_exact = {
        (profile.browser.key, normalize_email(profile.email), profile.directory)
        for profile in profiles
        if (
            profile.browser.key,
            normalize_email(profile.email),
            profile.directory,
        )
        in exact_targets
    }
    matched_legacy = {
        (profile.browser.key, normalize_email(profile.email))
        for profile in profiles
        if (profile.browser.key, normalize_email(profile.email)) in legacy_targets
    }
    if matched_exact != exact_targets or matched_legacy != legacy_targets:
        return {
            "ok": False,
            "error": "Inventario cambiato o identita non trovata.",
            "opened": [],
        }

    exes = {key: browser.detect_exe() for key, browser in configs.items()}
    if any(not exes.get(profile.browser.key) for profile in profiles):
        return {"ok": False, "error": "Browser non disponibile.", "opened": []}

    opened: list[dict[str, str]] = []
    launched: list[Profile] = []
    for profile in profiles:
        try:
            subprocess.Popen(
                [
                    str(exes[profile.browser.key]),
                    *profile.browser.exe_args(profile.directory),
                    "--new-window",
                ],
                close_fds=True,
                env=_browser_process_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=sys.platform != "win32",
            )
        except OSError:
            return {
                "ok": False,
                "error": "Avvio browser fallito.",
                "error_code": "browser_launch_failed",
                "opened": opened,
                "opened_count": len(opened),
            }
        launched.append(profile)
        opened.append(
            {
                "browser": profile.browser.key,
                "email": normalize_email(profile.email),
                "directory": profile.directory,
            }
        )

    # One bounded settle covers all requested launches. Chromium launchers may
    # return non-zero after delegating to an already-running browser, so their
    # wrapper exit code is not a reliable action result. Verify that the native
    # browser family is actually present instead; this also catches a bad SSH
    # desktop environment where Popen succeeds but Chrome immediately exits.
    if launched:
        time.sleep(0.5)
    active_browsers = _active_browser_processes() if launched else []
    if "activity-check-unavailable" in active_browsers:
        return {
            "ok": False,
            "error": "Avvio eseguito ma verifica browser non disponibile.",
            "error_code": "browser_launch_unverified",
            "opened": [],
            "opened_count": 0,
        }
    active_keys = {
        key
        for process_name in active_browsers
        if (key := _browser_key_for_process(process_name)) is not None
    }
    failed = [profile for profile in launched if profile.browser.key not in active_keys]
    if failed:
        return {
            "ok": False,
            "error": "Il browser ha rifiutato l'avvio.",
            "error_code": "browser_launch_failed",
            "opened": [
                item
                for item in opened
                if not any(
                    item["browser"] == profile.browser.key
                    and item["directory"] == profile.directory
                    for profile in failed
                )
            ],
            "opened_count": len(opened) - len(failed),
            "failed_count": len(failed),
        }
    return {"ok": True, "opened": opened, "opened_count": len(opened)}


def local_inventory(host_id: str | None = None) -> dict:
    host_id = host_id or current_host_id()
    host = _host(host_id)
    profiles = [_profile_record(profile) for profile in _profiles_for_configs(browser_configs())]
    return {
        "schema_version": AGENT_PROTOCOL_VERSION,
        "agent_version": __version__,
        "runtime": sys.platform,
        "host": {"id": host["id"], "label": host["label"]},
        "online": True,
        "profiles": profiles,
    }


def _target_identity(raw: dict) -> tuple[str, str, str]:
    return (
        str(raw.get("browser", "")).casefold(),
        normalize_email(raw.get("email")),
        str(raw.get("directory", "")),
    )


def _target_key(raw: dict) -> tuple[str, str, str, str]:
    path = str(raw.get("path", ""))
    try:
        path = str(Path(path).resolve(strict=False))
    except OSError:
        pass
    return (
        str(raw.get("browser", "")).casefold(),
        normalize_email(raw.get("email")),
        str(raw.get("directory", "")),
        path,
    )


def _write_json_atomic(path: Path, data: dict) -> None:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            shutil.copymode(path, temp_path)
        except OSError:
            pass
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _backup_local_state(path: Path) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.{stamp}.delete.bak")
    shutil.copy2(path, backup)
    return backup


def _prune_delete_backups(path: Path, keep: int = 3) -> None:
    backups = sorted(path.parent.glob(f"{path.name}.*.delete.bak"), reverse=True)
    for backup in backups[keep:]:
        backup.unlink(missing_ok=True)


def delete_local_targets(raw_targets: Iterable[dict]) -> dict:
    """Delete exact previewed targets after a full local rescan and safety check."""
    targets = [target for target in raw_targets if isinstance(target, dict)]
    if len(targets) != 1:
        return {"ok": False, "error": "Serve un solo target esatto.", "deleted": []}

    configs = {browser.key: browser for browser in browser_configs()}
    current_profiles = _profiles_for_configs(configs.values())
    current_by_identity = {
        _target_identity(_profile_record(profile)): profile for profile in current_profiles
    }
    expected_identities = {_target_identity(target) for target in targets}
    if len(expected_identities) != len(targets):
        return {"ok": False, "error": "Il target contiene duplicati.", "deleted": []}
    if not expected_identities.issubset(current_by_identity):
        return {
            "ok": False,
            "error": "Inventario cambiato o profilo non trovato: esegui una nuova scansione.",
            "deleted": [],
        }

    selected_profiles = [current_by_identity[key] for key in sorted(expected_identities)]
    for target in targets:
        supplied_path = str(target.get("path", "")).strip()
        if supplied_path:
            profile = current_by_identity[_target_identity(target)]
            if _target_key(target) != _target_key(_profile_record(profile)):
                return {
                    "ok": False,
                    "error": "Inventario cambiato o percorso non corrispondente.",
                    "deleted": [],
                }
    for profile in selected_profiles:
        browser = configs.get(profile.browser.key)
        if browser is None or not DELETABLE_PROFILE_DIRECTORY_RE.fullmatch(profile.directory):
            return {"ok": False, "error": "Profilo fuori dal perimetro consentito.", "deleted": []}
        try:
            if profile.path.is_symlink() or profile.path.resolve(strict=True).parent != browser.user_data_dir.resolve(strict=True):
                return {"ok": False, "error": "Percorso profilo non sicuro.", "deleted": []}
        except OSError:
            return {"ok": False, "error": "Percorso profilo non risolvibile.", "deleted": []}

    target_browsers = {profile.browser.key for profile in selected_profiles}
    active_browsers = _active_browser_processes()
    if "activity-check-unavailable" in active_browsers:
        return {
            "ok": False,
            "error": "Impossibile verificare se il browser e attivo.",
            "error_code": "browser_activity_check_unavailable",
            "active_browsers": active_browsers,
            "deleted": [],
        }
    active_targets = [
        process
        for process in active_browsers
        if _browser_key_for_process(process) in target_browsers
    ]
    if active_targets:
        try:
            _close_browser_processes(target_browsers)
        except (OSError, subprocess.TimeoutExpired):
            pass
        active_targets = [
            process
            for process in _active_browser_processes()
            if process == "activity-check-unavailable"
            or _browser_key_for_process(process) in target_browsers
        ]
        if active_targets:
            return {
                "ok": False,
                "error": "Chrome o Edge non si e chiuso: il profilo non e stato toccato.",
                "error_code": "browser_close_failed",
                "active_browsers": active_targets,
                "deleted": [],
            }
    deleted: list[dict[str, str]] = []
    backups: dict[str, str] = {}

    for browser_key in sorted({profile.browser.key for profile in selected_profiles}):
        browser = configs[browser_key]
        local_state_path = browser.user_data_dir / "Local State"
        local_state = _read_json(local_state_path)
        profile_state = local_state.get("profile")
        info_cache = profile_state.get("info_cache") if isinstance(profile_state, dict) else None
        if not isinstance(info_cache, dict):
            return {
                "ok": False,
                "error": f"{browser.name}: Local State non valido.",
                "deleted": deleted,
                "backups": backups,
            }

        backup = _backup_local_state(local_state_path)
        backups[browser_key] = str(backup)
        browser_profiles = [p for p in selected_profiles if p.browser.key == browser_key]
        for profile in browser_profiles:
            entry = info_cache.pop(profile.directory, None)
            if entry is None:
                return {
                    "ok": False,
                    "error": f"{browser.name}/{profile.directory}: entry scomparsa.",
                    "deleted": deleted,
                    "backups": backups,
                }
            _write_json_atomic(local_state_path, local_state)
            try:
                shutil.rmtree(profile.path)
            except OSError as exc:
                info_cache[profile.directory] = entry
                _write_json_atomic(local_state_path, local_state)
                return {
                    "ok": False,
                    "error": f"{browser.name}/{profile.directory}: {exc}",
                    "deleted": deleted,
                    "backups": backups,
                }
            deleted.append(_delete_record(profile))
        _prune_delete_backups(local_state_path)

    remaining = _profiles_for_configs(configs.values())
    remaining_identities = {
        (profile.browser.key, normalize_email(profile.email), profile.directory)
        for profile in remaining
    }
    still_present = sorted(expected_identities & remaining_identities)
    if still_present:
        return {
            "ok": False,
            "error": "Verifica post-cancellazione fallita.",
            "deleted": deleted,
            "backups": backups,
            "still_present": still_present,
        }
    return {
        "ok": True,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "safety": {
            "browser_inactive": True,
            "browser_auto_closed": bool(active_browsers),
            "single_target": True,
            "path_verified": True,
        },
        "recovery_class": "manual_local_state_backup",
        "post_read": {"absence_proven": True},
    }
