"""Profile Atlas compatibility interface over responsibility-specific modules."""

import subprocess

from backend.fleet2.registry import load_registry

from .hosts import PROFILE_SCHEMA_VERSION, PROFILE_AGENT_VERSION, SSH_CONFIG_PATH, _BROWSERS, _EMAIL_RE, _CATEGORY_TAG_RE, _DIRECTORY_RE, _DELETABLE_DIRECTORY_RE, PROFILE_INVENTORY_TTL_SECONDS, ProfileHost, _resolve_profile_hosts, PROFILE_HOSTS, _validated_target
from .transport import profile_identity, profile_account_identity, _agent_result, _offline_inventory, _profile_status, _with_inventory_freshness, ProfileInventoryCache
from .inventory import _inventory_cache, _profile_runtime_label, _parse_inventory, inventory_host, _group_accounts, scan_profile_fleet, profile_inventory_fleet, invalidate_profile_inventory_cache
from .identity import _inventory_exact_profile, _inventory_directory_profile, _inventory_identity_profiles, _delete_preimage, _safe_current_targets, _delete_receipt_matches, _agent_supports_safe_delete, _agent_supports_safe_rename, canonical_profile_label, canonical_profile_category, _category_from_profile_label, _profile_metadata_key, _resolve_category_candidate
from .name_plans import profile_name_sync_plan
from .name_sync import sync_profile_names
from .profile_actions import _delete_agent_failure, open_profile, delete_profile
from .group_delete_plans import profile_group_delete_plan
from .group_delete import delete_profile_group
from .group_rename import profile_group_rename_plan, rename_profile_group


def _resolve_profile_hosts():
    """Keep the original adapter's registry injection point available."""
    from .hosts import _resolve_profile_hosts as resolve

    return resolve(registry_loader=load_registry)
