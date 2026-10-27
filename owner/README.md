# Plancia owner backend and FleetProbe

This directory owns the FastAPI routes, FleetProbe agents, telemetry polling,
governed actions and tests formerly maintained in ControlCenter's `dashboard/`.
The UI is maintained in `../web/` and the authenticated FastAPI application
serves `../web/dist/` from the **same origin**.

## Private installation boundary

`fleet.json` in this repository is an example inventory with documentation-only
addresses. Production **must** set `PLANCIA_FLEET_CONFIG` to the installed,
private host registry. The canonical inventory and host-local profile command
paths are versioned in the private ControlCenter repository under
`configs/plancia/` and are passed through the systemd unit's environment.
`PLANCIA_PROFILE_COMMANDS_FILE` accepts an object mapping host IDs to absolute
Profile Atlas executable paths; this file must remain private. The installed
`FLEETPROBE_TOKEN_FILE`, owner auth environment, SQLite history and receipts stay
on the host in their existing directories. Do not copy them into this repo.

The proposed MiniPC systemd unit is `deploy/plancia-backend.service`, using
`~/.local/share/plancia/current/owner/.venv/` and port 8000 behind the existing
Tailscale Serve proxy. Build and test the frontend on Mac Studio, copy its
verified `web/dist/` alongside `owner/`, then run
`owner/deploy/prepare-release-runtime.sh` on the immutable MiniPC release.
The unit runs with no independent vault or database writer. Retain the existing
`controlcenter-fleet-dashboard` release as rollback material until the new unit
passes authenticated browser, API and FleetProbe checks. After an independent
review and verified cutover, disable the old unit before enabling this one:
only one process may bind port 8000 or poll the production SQLite history.

FleetProbe sources and platform installers now live in `agent/` within this
directory. On each reachable host, install from a pinned Plancia checkout
through the platform-specific script and verify its own runtime identity. An
offline machine remains pending in ControlCenter #484, not silently replaced
by telemetry from another runtime.

Development backend tests (via resource guard):

```sh
cd owner
python3 ~/.local/share/controlcenter/resource-guard/workload_launcher.py build -- uv run --no-project --with-requirements requirements-dev.txt python -m pytest -q -p no:cacheprovider
```

The original backend provenance is preserved by the issue #919 migration
commits in the two owner repositories. Donor Muxin Rust sources in `../src/`,
`../agent/` and `../share-server/` are unchanged. Plancia's Apache-2.0 donor
LICENSE and notices remain intact.
