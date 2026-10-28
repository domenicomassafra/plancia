# Backend and probe source provenance

ControlCenter issue [#919](https://github.com/domenicomassafra/controlcenter/issues/919)
transfers the owner backend from the private `domenicomassafra/controlcenter`
repository into this public Plancia repository. The source reference is
`controlcenter@3a8b82dbd2b55fe5d454cb2fe7c17cd301e733ff` at
`dashboard/`. Source file history and the earlier Signal Deck history remain
in ControlCenter Git; this repository records the import and subsequent fixes
in its own commits.

Imported source categories: `backend/` (FastAPI/auth/Fleet2/workspace),
`agent/` (FleetProbe and cross-platform installers), `deploy/` (runtime checks
and service units), `integrations/profile-atlas/`, `tools/`, and the original
backend regression tests. The production dashboard unit was renamed to
`plancia-backend.service`. The service config is still expected to be installed
with a readback on the target host; a checked-in unit is not deployment proof.

**Intentional exclusions:** `dashboard/fleet.json` contains operational hosts
and tailnet addresses, `dashboard/frontend.json` pins the old cross-repository
frontend build, and old runtime state, credentials, tokens, screenshots and
historical telemetry were never imported. `owner/fleet.json` is a public
example with reserved addresses; `PLANCIA_FLEET_CONFIG` loads the real private
inventory at runtime. Profile Atlas installation commands come from the private
`PLANCIA_PROFILE_COMMANDS_FILE` rather than embedded home-directory paths.
Any production startup without the private inventory is rejected by the unit.

Plancia's `LICENSE`, `NOTICE` and `THIRD_PARTY_NOTICES.md` remain unchanged:
the upstream Muxin donor is Apache-2.0. Profile Atlas is a separate
locally-authored MIT component, as declared in
`integrations/profile-atlas/pyproject.toml`; its MIT license is included in
that subdirectory. No third-party Muxin/Paseo/Guacamole source is newly
copied as part of this migration. Guacamole remains an external client/server
dependency reached through the existing authenticated adapter.
