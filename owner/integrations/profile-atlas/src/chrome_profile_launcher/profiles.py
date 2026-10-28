"""Profile discovery: parse Local State, extract account info, fallback email search."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import BrowserConfig, browser_configs

PROFILE_DIRECTORY_RE = re.compile(r"^(Default|Profile(?: \d+)?|Guest Profile|System Profile)$")
# One definition of the manageable bracket tag: the read-side policy parser and
# the canonical label builder must agree, or a written label can never satisfy
# ``name_policy_ok``.
MANAGED_TAG_RE = re.compile(r"\[([^\[\]\r\n]{1,32})\]")
MANAGED_LABEL_RE = re.compile(rf"^{MANAGED_TAG_RE.pattern}\s+(.+)$")


@dataclass(frozen=True)
class Profile:
    browser: BrowserConfig
    directory: str          # "Default", "Profile 42", etc.
    name: str               # display name from Local State / Preferences
    email: str              # account email
    gaia_name: str           # Google/MS account given name
    gaia_given_name: str     # preferred account name used by Chromium menus
    is_using_default_name: bool
    shortcut_name: str       # Chrome shortcut name
    path: Path               # full path to profile directory

    @property
    def label(self) -> str:
        """Stable local profile label owned by Profile Atlas.

        Chromium refreshes GAIA / Microsoft account names from the identity
        provider. Those values are useful when reproducing the browser's
        native picker text, but they are not a writable source of truth for
        Signal Deck. Prefer the local profile name, then the shortcut name,
        and only fall back to the account identity.
        """
        return _first_text(self.name, self.shortcut_name, self.email, self.directory)

    @property
    def account(self) -> str:
        """Combined account display."""
        if self.email and self.gaia_name and self.email.casefold() != self.gaia_name.casefold():
            return f"{self.gaia_name} <{self.email}>"
        return _first_text(self.email, self.gaia_name, "")

    @property
    def visible_label(self) -> str:
        """Reproduce Chromium's native profile-menu name selection."""
        local_name = _first_text(self.name, self.shortcut_name, self.directory)
        gaia_name = _first_text(self.gaia_given_name, self.gaia_name)
        if not gaia_name:
            return local_name
        if gaia_name.casefold() == local_name.casefold():
            return gaia_name
        if not self.is_using_default_name:
            return f"{gaia_name} ({local_name})"
        return gaia_name

    @property
    def policy_label(self) -> str:
        """Canonical owner-managed label for this browser identity.

        Existing bracketed tags are preserved and normalized to uppercase.
        Any identified profile that has never been classified receives the
        fleet fallback [TEST]. Provider/GAIA names are never a policy source.
        """
        email = self.email.strip().casefold()
        if not email:
            return self.label
        for candidate in (self.name, self.shortcut_name):
            match = MANAGED_LABEL_RE.fullmatch(candidate.strip())
            if not match:
                continue
            if _clean_email(match.group(2)) != email:
                continue
            try:
                return canonical_managed_label(match.group(1), email)
            except ValueError:
                continue
        return f"[TEST] {email}"

    @property
    def name_policy_ok(self) -> bool:
        """Whether the locally-owned Chromium profile name satisfies policy.

        Stock Chromium intentionally renders a signed-in profile as
        ``GAIA name (local profile name)`` when the provider name differs from a
        custom local name. GAIA/Microsoft names are provider-owned and may be
        refreshed at any time, so they cannot be a durable local invariant.
        """
        if not self.email:
            return True
        desired = self.policy_label
        return (
            self.name == desired
            and self.shortcut_name == desired
            and not self.is_using_default_name
        )

    @property
    def search_text(self) -> str:
        """Lowercase concatenation for fuzzy filtering."""
        return " ".join([
            self.browser.name, self.directory, self.name, self.email,
            self.shortcut_name,
        ]).casefold()


def _first_text(*values: object) -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _is_email_like(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()))


# --- JSON helpers ---

def _read_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# --- Preferences parser ---

def canonical_managed_label(tag: str, email: str) -> str:
    """Build the only canonical ``[TAG] full-email`` label, or raise.

    This is the single definition of a valid managed label.  The read side
    (:attr:`Profile.policy_label`) and the agent apply side both normalize
    through it so a written label is always one the reader accepts; a tag the
    reader would uppercase or reject can otherwise never satisfy
    ``name_policy_ok`` and would be re-targeted forever.
    """

    normalized_email = _clean_email(email)
    if not normalized_email:
        raise ValueError("a valid email is required for profile naming")
    raw_tag = tag.strip()
    if raw_tag and not raw_tag.startswith("["):
        raw_tag = f"[{raw_tag}]"
    match = MANAGED_TAG_RE.fullmatch(raw_tag)
    if match is None or not match.group(1).strip():
        raise ValueError("category must be a short bracketed tag such as [TEST]")
    return f"[{match.group(1).strip().upper()}] {normalized_email}"


def _read_preference_identity(profile_path: Path, primary_email: str = "") -> dict[str, str]:
    """Extract name, email, gaia_name from Preferences JSON.

    Chromium stores a list in ``account_info`` because one profile can be
    signed into several accounts, and its first entry is not guaranteed to be
    the profile's own account (Edge appends secondary Microsoft accounts).
    Prefer the entry matching the profile-level identity from ``Local State``
    so a signed-in profile is not mistaken for an ambiguous one; fall back to
    the historical first-entry behaviour only when nothing matches.
    """
    prefs = _read_json(profile_path / "Preferences")
    profile = prefs.get("profile") if isinstance(prefs.get("profile"), dict) else {}
    raw_accounts = prefs.get("account_info")
    accounts = [item for item in raw_accounts if isinstance(item, dict)] if isinstance(raw_accounts, list) else []
    account: dict = {}
    if primary_email:
        account = next(
            (item for item in accounts if _clean_email(item.get("email")) == primary_email),
            {},
        )
    if not account and accounts:
        account = accounts[0]

    edge_first = _first_text(account.get("edge_account_first_name"))
    edge_last = _first_text(account.get("edge_account_last_name"))
    edge_full = f"{edge_first} {edge_last}".strip() if edge_first or edge_last else ""

    return {
        "name": _first_text(profile.get("name"), edge_full, account.get("full_name"), account.get("given_name")),
        "email": _first_text(account.get("email")),
        "gaia_name": _first_text(edge_full, account.get("full_name"), account.get("given_name")),
    }


EMAIL_VALUE_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
EMAIL_TOKEN_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _clean_email(value: object) -> str:
    if not isinstance(value, str):
        return ""
    email = value.strip().lower()
    return email if EMAIL_VALUE_RE.fullmatch(email) else ""


def _unambiguous_identity_email(*values: object) -> str:
    """Return one account email from Chromium identity fields, or no identity.

    Chromium's ``user_name`` is sometimes an account email and sometimes a
    human-facing label such as ``Domenico (owner@example.com)``.  Treat every
    valid email in its identity metadata as evidence, but only accept it when
    the evidence agrees.  This keeps display-name differences from splitting a
    fleet account while refusing to merge a profile with conflicting accounts.
    """

    emails: set[str] = set()
    for value in values:
        direct = _clean_email(value)
        if direct:
            emails.add(direct)
        if isinstance(value, str):
            emails.update(_clean_email(token) for token in EMAIL_TOKEN_RE.findall(value))
    emails.discard("")
    return next(iter(emails)) if len(emails) == 1 else ""


# --- Main profile loader ---

def _load_browser_profiles(browser: BrowserConfig) -> list[Profile]:
    local_state = _read_json(browser.user_data_dir / "Local State")
    profile_state = local_state.get("profile")
    info_cache = profile_state.get("info_cache") if isinstance(profile_state, dict) else {}
    if not isinstance(info_cache, dict):
        return []

    profiles: list[Profile] = []
    for directory, raw_info in info_cache.items():
        if not isinstance(directory, str) or not isinstance(raw_info, dict):
            continue
        if not PROFILE_DIRECTORY_RE.fullmatch(directory):
            continue
        profile_path = browser.user_data_dir / directory
        if not profile_path.is_dir():
            continue

        pref_id = _read_preference_identity(
            profile_path,
            primary_email=_unambiguous_identity_email(raw_info.get("user_name")),
        )
        email = _unambiguous_identity_email(
            raw_info.get("user_name"),
            pref_id.get("email"),
            raw_info.get("name"),
            raw_info.get("gaia_name"),
            raw_info.get("gaia_given_name"),
            pref_id.get("name"),
            pref_id.get("gaia_name"),
        )

        profiles.append(Profile(
            browser=browser,
            directory=directory,
            name=_first_text(raw_info.get("name"), pref_id.get("name")),
            email=email,
            gaia_name=_first_text(raw_info.get("gaia_name"), pref_id.get("gaia_name")),
            gaia_given_name=_first_text(raw_info.get("gaia_given_name")),
            is_using_default_name=bool(raw_info.get("is_using_default_name", False)),
            shortcut_name=_first_text(raw_info.get("shortcut_name")),
            path=profile_path,
        ))

    return sorted(profiles, key=lambda p: (p.label.casefold(), p.directory.casefold()))


def load_profiles() -> list[Profile]:
    """Load all profiles from all detected browsers."""
    all_profiles: list[Profile] = []
    for browser in browser_configs():
        all_profiles.extend(_load_browser_profiles(browser))
    return sorted(all_profiles, key=lambda p: (p.browser.name, p.label.casefold(), p.directory.casefold()))
