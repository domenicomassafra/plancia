"""Browser profile API routes, backed by the existing guarded Profile Atlas adapter."""
from __future__ import annotations
import asyncio
import hashlib
import uuid
from typing import Any, Callable, Dict
from fastapi import APIRouter, HTTPException
from pydantic import Field
from backend.browser_profiles import (
    canonical_profile_category, canonical_profile_label, delete_profile,
    delete_profile_group, invalidate_profile_inventory_cache, profile_group_delete_plan,
    profile_group_rename_plan, profile_identity, profile_inventory_fleet,
    profile_name_sync_plan, rename_profile_group, sync_profile_names,
)
from .request_models import StrictRequest
from .store import Store

class ProfileTarget(StrictRequest):
    host_id: str = Field(min_length=1, max_length=40)
    browser: str = Field(min_length=1, max_length=20)
    email: str = Field(default="", max_length=200)
    directory: str = Field(min_length=1, max_length=200)


class ProfileDeleteRequest(ProfileTarget):
    confirmed: bool


class ProfileGroupDeleteRequest(StrictRequest):
    browser: str = Field(min_length=1, max_length=20)
    email: str = Field(min_length=3, max_length=200)
    confirmed: bool
    plan_fingerprint: str = Field(
        min_length=64, max_length=64, pattern="^[0-9a-f]{64}$"
    )


class ProfileGroupDeletePreviewRequest(StrictRequest):
    browser: str = Field(min_length=1, max_length=20)
    email: str = Field(min_length=3, max_length=200)


class ProfileMetadataRequest(ProfileTarget):
    alias: str = Field(default="", max_length=80)
    category: str = Field(default="", max_length=40)


class ProfileRenameRequest(StrictRequest):
    browser: str = Field(min_length=1, max_length=20)
    email: str = Field(min_length=3, max_length=200)
    category: str = Field(min_length=1, max_length=40)
    target_hosts: list[str] | None = Field(default=None, max_length=4)
    plan_fingerprint: str | None = Field(default=None, max_length=64)


class ProfileGroupRenamePreviewRequest(StrictRequest):
    browser: str = Field(min_length=1, max_length=20)
    email: str = Field(min_length=3, max_length=200)
    category: str = Field(min_length=1, max_length=40)
    target_hosts: list[str] | None = Field(default=None, max_length=4)


class ProfileNameSyncRequest(StrictRequest):
    confirmed: bool
    plan_fingerprint: str = Field(
        min_length=64, max_length=64, pattern="^[0-9a-f]{64}$"
    )


def _clean_profile_text(value: str, field: str) -> str:
    cleaned = value.strip()
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError(f"{field} contains invalid characters")
    return cleaned


def _decorate_profile_fleet(
    fleet: Dict[str, Any],
    metadata: Dict[tuple[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    for inventory in fleet.get("inventories", []):
        host_id = str((inventory.get("host") or {}).get("id", ""))
        for profile in inventory.get("profiles", []):
            annotation = metadata.get(
                (host_id, str(profile.get("profile_key", ""))), {}
            )
            profile["alias"] = str(annotation.get("alias", ""))
            category = str(annotation.get("category", "")).strip()
            profile["category"] = category
            email = str(profile.get("email", "")).strip().casefold()
            if category and email:
                try:
                    desired_label = canonical_profile_label(category, email)
                except ValueError:
                    # Legacy invalid metadata remains visible for repair but
                    # never becomes an invented browser target.
                    continue
                original_policy_label = str(profile.get("policy_label", ""))
                profile["category"] = canonical_profile_category(category, email)
                profile["policy_label"] = desired_label
                profile["name_policy_ok"] = bool(
                    profile.get("name_policy_ok") is True
                    and profile.get("label") == desired_label
                    and original_policy_label == desired_label
                )

    # Accounts are a compact summary. Host-local metadata belongs exclusively
    # to ``inventories`` above, which avoids rebuilding a second copy of every
    # profile only to decorate it and discard it.
    return fleet


def register_profile_routes(router: APIRouter, store: Store,
                            open_profile_fn: Callable[..., Any]) -> None:
    @router.get("/profiles")
    async def profiles(refresh: bool = False) -> Dict[str, Any]:
        fleet, metadata = await asyncio.gather(
            asyncio.to_thread(profile_inventory_fleet, refresh=refresh),
            asyncio.to_thread(store.profile_metadata),
        )
        return _decorate_profile_fleet(fleet, metadata)

    @router.post("/profiles/open")
    async def profiles_open(payload: ProfileTarget) -> Dict[str, Any]:
        try:
            result = await asyncio.to_thread(
                open_profile_fn,
                payload.host_id,
                payload.browser,
                payload.email,
                payload.directory,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        receipt_id = uuid.uuid4().hex[:12]
        email_hash = (
            hashlib.sha256(payload.email.strip().casefold().encode("utf-8")).hexdigest()[:16]
            if payload.email
            else "none"
        )
        store.open_receipt(
            receipt_id,
            payload.host_id,
            "profile-open",
            {
                "browser": payload.browser.casefold(),
                "directory": payload.directory,
                "account_hash": email_hash,
                "opened_count": result.get("opened_count", 0),
            },
        )
        store.close_receipt(
            receipt_id,
            "succeeded" if result.get("ok") else "failed",
            str(result.get("error_code") or result.get("opened_count", "")),
        )
        return {**result, "receipt_id": receipt_id}

    @router.post("/profiles/delete")
    async def profiles_delete(payload: ProfileDeleteRequest) -> Dict[str, Any]:
        try:
            identity = profile_identity(
                payload.host_id,
                payload.browser,
                payload.email,
                payload.directory,
            )
            if identity["directory"] == "Default":
                raise ValueError("the default browser profile cannot be deleted")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not payload.confirmed:
            raise HTTPException(
                status_code=400,
                detail="the explicit delete confirmation is required",
            )
        try:
            result = await asyncio.to_thread(
                delete_profile,
                payload.host_id,
                payload.browser,
                payload.email,
                payload.directory,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        invalidate_profile_inventory_cache()
        receipt_id = uuid.uuid4().hex[:12]
        store.open_receipt(
            receipt_id,
            payload.host_id,
            "profile-delete",
            {
                "target": {
                    "browser": identity["browser"],
                    "email": identity["email"],
                    "directory": identity["directory"],
                },
                "preimage": result.get("preimage"),
                "recovery_class": result.get("recovery_class", "not_started"),
                "post_read": result.get("post_read"),
            },
        )
        store.close_receipt(
            receipt_id,
            "succeeded" if result.get("ok") else "failed",
            str(result.get("error_code") or result.get("deleted_count", "")),
        )
        return {**result, "receipt_id": receipt_id}

    @router.post("/profiles/delete-group/preview")
    async def profiles_delete_group_preview(
        payload: ProfileGroupDeletePreviewRequest,
    ) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(
                profile_group_delete_plan,
                payload.browser,
                payload.email,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/profiles/delete-group")
    async def profiles_delete_group(
        payload: ProfileGroupDeleteRequest,
    ) -> Dict[str, Any]:
        if not payload.confirmed:
            raise HTTPException(
                status_code=400,
                detail="the explicit delete confirmation is required",
            )
        try:
            result = await asyncio.to_thread(
                delete_profile_group,
                payload.browser,
                payload.email,
                expected_plan_fingerprint=payload.plan_fingerprint,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        invalidate_profile_inventory_cache()
        receipt_id = uuid.uuid4().hex[:12]
        store.open_receipt(
            receipt_id,
            "profile-fleet",
            "profile-delete-group",
            {
                "browser": payload.browser.casefold(),
                "email": payload.email.strip().casefold(),
                # Persist the bounded, redacted operation result: early
                # preflight failures otherwise lose the exact host/target
                # information needed for a safe retry or recovery decision.
                "result": result,
                "recovery_class": result.get("recovery_class", "not_started"),
            },
        )
        store.close_receipt(
            receipt_id,
            "succeeded" if result.get("ok") else "failed",
            str(result.get("error_code") or result.get("deleted_count", "")),
        )
        return {**result, "receipt_id": receipt_id}

    @router.put("/profiles/metadata")
    async def profiles_metadata(payload: ProfileMetadataRequest) -> Dict[str, Any]:
        try:
            identity = profile_identity(
                payload.host_id,
                payload.browser,
                payload.email,
                payload.directory,
            )
            alias = _clean_profile_text(payload.alias, "alias")
            category = _clean_profile_text(payload.category, "category")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        metadata = await asyncio.to_thread(
            store.set_profile_metadata,
            payload.host_id,
            identity["profile_key"],
            alias,
            category,
        )
        receipt_id = uuid.uuid4().hex[:12]
        store.open_receipt(
            receipt_id,
            payload.host_id,
            "profile-metadata",
            {
                "browser": identity["browser"],
                "directory": identity["directory"],
                "category": category,
            },
        )
        store.close_receipt(receipt_id, "succeeded", alias or identity["email"])
        return {"ok": True, "receipt_id": receipt_id, **metadata}

    @router.post("/profiles/rename/preview")
    async def profiles_rename_preview(
        payload: ProfileGroupRenamePreviewRequest,
    ) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(
                profile_group_rename_plan,
                payload.browser,
                payload.email,
                payload.category,
                target_hosts=payload.target_hosts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/profiles/rename")
    async def profiles_rename(payload: ProfileRenameRequest) -> Dict[str, Any]:
        try:
            result = await asyncio.to_thread(
                rename_profile_group,
                payload.browser,
                payload.email,
                payload.category,
                target_hosts=payload.target_hosts,
                expected_plan_fingerprint=payload.plan_fingerprint,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        receipt_id = uuid.uuid4().hex[:12]
        store.open_receipt(
            receipt_id,
            "profile-fleet",
            "profile-rename",
            {
                "browser": payload.browser.casefold(),
                "email": payload.email.strip().casefold(),
                "category": payload.category.strip(),
                "desired_label": result.get("desired_label", ""),
                "targets": result.get("targets", []),
                "plan_fingerprint": result.get("plan_fingerprint", ""),
                "recovery_class": result.get("recovery_class", "not_started"),
            },
        )
        store.close_receipt(
            receipt_id,
            "succeeded" if result.get("ok") else "failed",
            str(result.get("error_code") or result.get("status", "")),
        )
        if result.get("ok"):
            category = canonical_profile_category(payload.category, payload.email)
            for target in result.get("targets", []):
                if not isinstance(target, dict):
                    continue
                profile_key = (
                    f"{str(target.get('browser', '')).casefold()}:"
                    f"{str(target.get('email', '')).strip().casefold()}:"
                    f"{str(target.get('directory', ''))}"
                )
                await asyncio.to_thread(
                    store.set_profile_category,
                    str(target.get("host_id", "")),
                    profile_key,
                    category,
                )
        return {**result, "receipt_id": receipt_id}

    @router.get("/profiles/names/preview")
    async def profiles_names_preview() -> Dict[str, Any]:
        metadata = await asyncio.to_thread(store.profile_metadata)
        result = await asyncio.to_thread(profile_name_sync_plan, metadata)
        return {
            key: value
            for key, value in result.items()
            if not key.startswith("_") and key != "category_updates"
        }

    @router.post("/profiles/names/sync")
    async def profiles_names_sync(payload: ProfileNameSyncRequest) -> Dict[str, Any]:
        if not payload.confirmed:
            raise HTTPException(
                status_code=400,
                detail="explicit confirmation is required because browsers may be closed",
            )
        metadata = await asyncio.to_thread(store.profile_metadata)
        result = await asyncio.to_thread(
            sync_profile_names,
            metadata,
            expected_plan_fingerprint=payload.plan_fingerprint,
        )
        for update in result.get("category_updates", []):
            if not isinstance(update, dict):
                continue
            await asyncio.to_thread(
                store.set_profile_category,
                str(update.get("host_id", "")),
                str(update.get("profile_key", "")),
                str(update.get("category", "")),
            )

        receipt_id = uuid.uuid4().hex[:12]
        store.open_receipt(
            receipt_id,
            "profile-fleet",
            "profile-name-sync",
            {
                "plan_fingerprint": result.get("plan_fingerprint", payload.plan_fingerprint),
                "target_count": len(result.get("targets", [])),
                "renamed_count": int(result.get("renamed_count", 0)),
                "unavailable_hosts": result.get("unavailable_hosts", []),
                "recovery_class": result.get("recovery_class", "not_started"),
            },
        )
        store.close_receipt(
            receipt_id,
            "succeeded" if result.get("ok") else "failed",
            str(result.get("error_code") or result.get("status", "")),
        )
        public_result = {
            key: value
            for key, value in result.items()
            if not key.startswith("_") and key != "category_updates"
        }
        return {**public_result, "receipt_id": receipt_id}
