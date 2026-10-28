"""Exact local profile rename helpers used by the Signal Deck agent protocol."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import browser_configs
from .browser_processes import _browser_process_targets
from .profiles import (
    MANAGED_LABEL_RE,
    PROFILE_DIRECTORY_RE,
    _load_browser_profiles,
    _read_json,
    canonical_managed_label,
)


def _browsers_running(browser_keys: set[str] | None = None) -> list[str]:
    """Return browser main processes still running.

    A failed/ambiguous probe counts as running on purpose: writing
    ``Local State`` / ``Preferences`` while a browser is alive races its own
    writes and can tear the files, losing the ``os_crypt`` key and
    ``account_info`` (symptom: profiles log out of Google).  Fail-closed.
    """

    running: list[str] = []
    for target in _browser_process_targets(browser_keys):
        if sys.platform == "win32":
            try:
                completed = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {target}", "/NH"],
                    capture_output=True, text=True, timeout=3,
                )
                if completed.returncode == 0 and target.casefold() in completed.stdout.casefold():
                    running.append(target)
                elif completed.returncode != 0:
                    running.append(f"{target} (probe-failed)")
            except (OSError, subprocess.TimeoutExpired):
                running.append(f"{target} (probe-failed)")
        else:
            try:
                completed = subprocess.run(
                    ["pgrep", "-x", target], capture_output=True, timeout=3
                )
                if completed.returncode == 0:
                    running.append(target)
                elif completed.returncode not in (0, 1):
                    running.append(f"{target} (probe-failed)")
            except (OSError, subprocess.TimeoutExpired):
                running.append(f"{target} (probe-failed)")
    return running


def _close_browsers(
    browser_keys: set[str] | None = None, wait_seconds: float = 20.0
) -> list[str]:
    """Close only the targeted browsers, then verify they really exited."""
    lines: list[str] = []
    process_targets = _browser_process_targets(browser_keys)
    if sys.platform == "darwin":
        for app in process_targets:
            try:
                subprocess.run(
                    ["osascript", "-e", f'tell application "{app}" to quit'],
                    capture_output=True, timeout=5,
                )
                lines.append(f"  Chiuso {app} (macOS)")
            except (OSError, subprocess.TimeoutExpired):
                lines.append(f"  Impossibile chiudere {app} automaticamente")
    elif sys.platform == "win32":
        for exe in process_targets:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", exe],
                    capture_output=True, timeout=5,
                )
                lines.append(f"  Chiuso {exe} (Windows)")
            except (OSError, subprocess.TimeoutExpired):
                lines.append(f"  Impossibile chiudere {exe} automaticamente")
    else:  # Linux
        for proc in process_targets:
            try:
                subprocess.run(
                    ["pkill", "-x", proc],
                    capture_output=True, timeout=5,
                )
                lines.append(f"  Chiuso {proc} (Linux)")
            except (OSError, subprocess.TimeoutExpired):
                lines.append(f"  Impossibile chiudere {proc} automaticamente")

    # Verify real exit before any write: writing Local State / Preferences
    # while a browser is alive races Chrome's own saves and can tear the
    # files.  A torn ``Local State`` loses the ``os_crypt`` key, so Chrome
    # can no longer decrypt Cookies / Login Data and every site appears
    # logged out; a torn ``Preferences`` loses ``account_info``.
    deadline = time.monotonic() + max(0.0, wait_seconds)
    still_running = _browsers_running(browser_keys)
    while still_running and time.monotonic() < deadline:
        time.sleep(1)
        still_running = _browsers_running(browser_keys)
    if still_running:
        lines.append(
            "  ATTENZIONE: browser ancora attivi dopo la chiusura: "
            + ", ".join(still_running)
            + ". Nessuna scrittura verra eseguita."
        )
    return lines


def _canonical_managed_label(desired: str, email: str) -> str:
    """Return the canonical label for an already-canonical desired value, else ``""``.

    Apply-side validation must use the same rule as the read side
    (:attr:`Profile.policy_label`).  Accepting a tag the reader rejects or
    normalizes (over-long, or lowercase that the reader uppercases) would
    write a label that can never satisfy ``name_policy_ok`` and would be
    re-targeted forever.
    """

    match = MANAGED_LABEL_RE.fullmatch(desired)
    if match is None:
        return ""
    try:
        canonical = canonical_managed_label(match.group(1), email)
    except ValueError:
        return ""
    return canonical if canonical == desired else ""


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


def _account_email_count(prefs: dict) -> int:
    """Count signed-in account emails in a Preferences payload."""
    accounts = prefs.get("account_info")
    if not isinstance(accounts, list):
        return 0
    return sum(
        1
        for account in accounts
        if isinstance(account, dict)
        and isinstance(account.get("email"), str)
        and account["email"]
    )


def _write_local_state_guarded(
    path: Path, data: dict, *, had_os_crypt: bool, backup: Path | None
) -> None:
    """Atomic Local State write that proves auth material survived.

    After the write the file must still parse and, when it carried an
    ``os_crypt`` key before, must still carry it.  Losing that key makes
    Chrome unable to decrypt Cookies / Login Data (total logout).  On
    violation the pre-write backup is restored and OSError raised.
    """

    _write_json_atomic(path, data)
    check = _read_json(path)
    if not check or (had_os_crypt and "os_crypt" not in check):
        if backup is not None and backup.is_file():
            shutil.copy2(backup, path)
        raise OSError(
            f"{path.name}: verifica post-scrittura fallita (os_crypt non preservato); "
            "backup ripristinato."
        )


def _write_preferences_guarded(
    path: Path, data: dict, *, account_emails_before: int, backup: Path | None
) -> None:
    """Atomic Preferences write that proves signed-in accounts survived.

    ``account_info`` is Chrome's per-profile sign-in state; a torn or
    truncated Preferences file loses it and the profile renders signed out.
    On violation the pre-write backup is restored and OSError raised.
    """

    _write_json_atomic(path, data)
    check = _read_json(path)
    if not check or _account_email_count(check) != account_emails_before:
        if backup is not None and backup.is_file():
            shutil.copy2(backup, path)
        raise OSError(
            f"{path.name}: verifica post-scrittura fallita (account_info non preservato); "
            "backup ripristinato."
        )


def _bounded_backup(path: Path, category: str, keep: int = 3) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.{stamp}.{category}.bak")
    shutil.copy2(path, backup)
    backups = sorted(path.parent.glob(f"{path.name}.*.{category}.bak"), reverse=True)
    for old_backup in backups[keep:]:
        old_backup.unlink(missing_ok=True)
    return backup


def _rename_record(profile, desired_label: str) -> dict[str, str]:
    return {
        "browser": profile.browser.key,
        "email": profile.email.strip().casefold(),
        "directory": profile.directory,
        "path": str(profile.path),
        # Stale checks follow the local label this agent actually owns.
        # Chromium may independently refresh account names, changing the
        # native visible label without changing our local name.
        "current_label": profile.label,
        "desired_label": desired_label,
    }


def _rename_key(raw: dict) -> tuple[str, str, str, str, str, str]:
    return (
        str(raw.get("browser", "")).casefold(),
        str(raw.get("email", "")).strip().casefold(),
        str(raw.get("directory", "")),
        str(Path(str(raw.get("path", ""))).resolve(strict=False)),
        str(raw.get("current_label", "")),
        str(raw.get("desired_label", "")),
    )


def _needs_rename(profile, desired_label: str) -> bool:
    return (
        profile.name != desired_label
        or profile.shortcut_name != desired_label
        or profile.is_using_default_name
    )


def preview_local_rename_targets(raw_targets) -> dict:
    """Resolve exact local rename targets without exposing them to the UI.

    The preview is deliberately host-local: the caller may pass only a
    browser/email/directory identity plus the canonical label, while this
    function adds the current local path and label required by the apply-side
    stale check.  Those private paths never leave the service boundary.
    """

    requests = [target for target in raw_targets if isinstance(target, dict)]
    if not requests:
        return {"ok": False, "error": "Nessun profilo da rinominare.", "targets": []}

    desired_by_identity: dict[tuple[str, str, str], str] = {}
    for target in requests:
        browser = str(target.get("browser", "")).strip().casefold()
        email = str(target.get("email", "")).strip().casefold()
        directory = str(target.get("directory", "")).strip()
        desired = str(target.get("desired_label", "")).strip()
        if (
            browser not in {"chrome", "edge"}
            or not email
            or not PROFILE_DIRECTORY_RE.fullmatch(directory)
            or not _canonical_managed_label(desired, email)
        ):
            return {"ok": False, "error": "Target rinomina non valido.", "targets": []}
        identity = (browser, email, directory)
        if identity in desired_by_identity:
            return {"ok": False, "error": "Target rinomina duplicato.", "targets": []}
        desired_by_identity[identity] = desired

    configs = {browser.key: browser for browser in browser_configs()}
    profiles_by_identity: dict[tuple[str, str, str], list] = {}
    for profile in [profile for browser in configs.values() for profile in _load_browser_profiles(browser)]:
        identity = (profile.browser.key, profile.email.strip().casefold(), profile.directory)
        profiles_by_identity.setdefault(identity, []).append(profile)

    previewed: list[dict[str, str]] = []
    for identity, desired in desired_by_identity.items():
        profiles = profiles_by_identity.get(identity, [])
        if len(profiles) != 1:
            return {
                "ok": False,
                "error": "Inventario cambiato o identita non trovata.",
                "targets": [],
            }
        profile = profiles[0]
        browser = configs.get(profile.browser.key)
        if browser is None or profile.path.resolve(strict=False).parent != browser.user_data_dir.resolve(strict=False):
            return {"ok": False, "error": "Percorso profilo non sicuro.", "targets": []}
        if _needs_rename(profile, desired):
            previewed.append(_rename_record(profile, desired))

    return {"ok": True, "targets": previewed, "rename_count": len(previewed)}


def rename_local_targets(raw_targets) -> dict:
    """Apply previewed email labels after an exact local rescan."""
    targets = [target for target in raw_targets if isinstance(target, dict)]
    if not targets:
        return {"ok": False, "error": "Nessun profilo da rinominare.", "renamed": []}

    desired_by_account: dict[tuple[str, str], str] = {}
    for target in targets:
        browser = str(target.get("browser", "")).strip().casefold()
        email = str(target.get("email", "")).strip().casefold()
        desired = str(target.get("desired_label", "")).strip()
        if (
            browser not in {"chrome", "edge"}
            or not email
            or not _canonical_managed_label(desired, email)
        ):
            return {"ok": False, "error": "Target rinomina non valido.", "renamed": []}
        account_key = (browser, email)
        if account_key in desired_by_account and desired_by_account[account_key] != desired:
            return {"ok": False, "error": "Etichette in conflitto per lo stesso account browser.", "renamed": []}
        desired_by_account[account_key] = desired

    configs = {browser.key: browser for browser in browser_configs()}
    profiles = [profile for browser in configs.values() for profile in _load_browser_profiles(browser)]
    current_targets = [
        _rename_record(
            profile,
            desired_by_account[(profile.browser.key, profile.email.strip().casefold())],
        )
        for profile in profiles
        if (profile.browser.key, profile.email.strip().casefold()) in desired_by_account
        and _needs_rename(
            profile,
            desired_by_account[(profile.browser.key, profile.email.strip().casefold())],
        )
    ]
    expected_keys = {_rename_key(target) for target in targets}
    current_keys = {_rename_key(target) for target in current_targets}
    if len(expected_keys) != len(targets) or expected_keys != current_keys:
        return {
            "ok": False,
            "error": "Inventario cambiato o target incompleto: esegui una nuova anteprima.",
            "renamed": [],
        }

    profiles_by_key = {
        _rename_key(
            _rename_record(
                profile,
                desired_by_account[(profile.browser.key, profile.email.strip().casefold())],
            )
        ): profile
        for profile in profiles
        if (profile.browser.key, profile.email.strip().casefold()) in desired_by_account
        and _needs_rename(
            profile,
            desired_by_account[(profile.browser.key, profile.email.strip().casefold())],
        )
    }
    selected_profiles = [profiles_by_key[key] for key in sorted(expected_keys)]
    for profile in selected_profiles:
        browser = configs.get(profile.browser.key)
        if browser is None or not PROFILE_DIRECTORY_RE.fullmatch(profile.directory):
            return {"ok": False, "error": "Profilo fuori dal perimetro consentito.", "renamed": []}
        if profile.path.resolve(strict=False).parent != browser.user_data_dir.resolve(strict=False):
            return {"ok": False, "error": "Percorso profilo non sicuro.", "renamed": []}

    target_browsers = {profile.browser.key for profile in selected_profiles}
    close_log = _close_browsers(target_browsers)
    active_targets = _browsers_running(target_browsers)
    if active_targets:
        return {
            "ok": False,
            "error": (
                "Browser target ancora attivi: nessuna rinomina applicata "
                "per evitare danni a sessione e cookie."
            ),
            "renamed": [],
            "renamed_count": 0,
            "backups": [],
            "close_log": close_log,
            "active_browsers": active_targets,
        }
    renamed: list[dict[str, str]] = []
    backups: list[str] = []
    try:
        for browser_key in sorted({profile.browser.key for profile in selected_profiles}):
            browser = configs[browser_key]
            local_state_path = browser.user_data_dir / "Local State"
            local_state = _read_json(local_state_path)
            had_os_crypt = "os_crypt" in local_state
            profile_state = local_state.get("profile")
            info_cache = profile_state.get("info_cache") if isinstance(profile_state, dict) else None
            if not isinstance(info_cache, dict):
                raise OSError(f"{browser.name}: Local State non valido")
            backups.append(str(_bounded_backup(local_state_path, "rename")))
            local_state_backup = Path(backups[-1])

            for profile in [item for item in selected_profiles if item.browser.key == browser_key]:
                desired = desired_by_account[(profile.browser.key, profile.email.strip().casefold())]
                entry = info_cache.get(profile.directory)
                if not isinstance(entry, dict):
                    raise OSError(f"{browser.name}/{profile.directory}: entry scomparsa")
                entry["name"] = desired
                entry["shortcut_name"] = desired
                # GAIA/Microsoft display names are provider-owned. Chromium may
                # refresh them while the account is signed in, so rewriting
                # those cache fields only creates temporary picker output and
                # cannot be a durable naming policy. Keep them untouched.
                entry["is_using_default_name"] = False

                prefs_path = profile.path / "Preferences"
                if prefs_path.is_file():
                    prefs = _read_json(prefs_path)
                    profile_prefs = prefs.get("profile")
                    if isinstance(profile_prefs, dict):
                        backups.append(str(_bounded_backup(prefs_path, "rename")))
                        prefs_backup = Path(backups[-1])
                        profile_prefs["name"] = desired
                        _write_preferences_guarded(
                            prefs_path,
                            prefs,
                            account_emails_before=_account_email_count(prefs),
                            backup=prefs_backup,
                        )
                renamed.append(_rename_record(profile, desired))
            _write_local_state_guarded(
                local_state_path,
                local_state,
                had_os_crypt=had_os_crypt,
                backup=local_state_backup,
            )
    except OSError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "renamed": renamed,
            "renamed_count": len(renamed),
            "backups": backups,
        }

    refreshed = [profile for browser in configs.values() for profile in _load_browser_profiles(browser)]
    remaining = [
        profile
        for profile in refreshed
        if (profile.browser.key, profile.email.strip().casefold()) in desired_by_account
        and _needs_rename(
            profile,
            desired_by_account[(profile.browser.key, profile.email.strip().casefold())],
        )
    ]
    return {
        "ok": not remaining,
        "error": "Verifica post-rinomina fallita." if remaining else "",
        "renamed": renamed,
        "renamed_count": len(renamed),
        "backups": backups,
        "close_log": close_log,
    }
