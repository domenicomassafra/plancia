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
`configs/plancia/` and are securely staged at `~/.config/plancia/` on MiniPC
by ControlCenter's `tools/install-plancia-private-config.sh` before cutover.
The systemd unit reads that private destination through its environment.
`PLANCIA_PROFILE_COMMANDS_FILE` accepts an object mapping host IDs to absolute
Profile Atlas executable paths; this file must remain private. The installed
`FLEETPROBE_TOKEN_FILE`, owner auth environment, SQLite history and receipts stay
on the host in their existing directories. Do not copy them into this repo.

The proposed MiniPC systemd unit is `deploy/plancia-backend.service`, using
`~/.local/share/plancia/current/owner/.venv/` and port 8000 behind the existing
Tailscale Serve proxy. Build and test the frontend on Mac Studio, copy its
verified `web/dist/` alongside `owner/`, then run
`owner/deploy/prepare-release-runtime.sh` on the immutable MiniPC release.
Dependency environments are keyed by the requirements digest under
`~/.local/share/plancia/owner-venvs`, separate from historical dashboard venvs.

The native release **must** carry the same source/checker/asset binding as the
former ControlCenter composition. On the Mac Studio, after building `web/dist`
from the reviewed exact Plancia commit and copying `owner/` and `web/dist/`
into a temporary candidate directory, bind that candidate using the existing
Plancia web checker (these commands run at the Plancia repository root):

```sh
source_sha=$(git rev-parse HEAD)
candidate=/path/to/staged/plancia/release
cp web/scripts/check-release.mjs "$candidate/owner/frontend-check.mjs"
python3 - "$candidate/owner" "$source_sha" <<'PYTHON'
import hashlib, json, pathlib, sys
owner = pathlib.Path(sys.argv[1])
source = sys.argv[2]
assert len(source) == 40 and all(ch in "0123456789abcdef" for ch in source)
checker = hashlib.sha256((owner / "frontend-check.mjs").read_bytes()).hexdigest()
(owner / "frontend.json").write_text(json.dumps({
    "schema": "controlcenter/frontend-source/v1",
    "repository": "https://github.com/domenicomassafra/plancia",
    "directory": "web",
    "revision": source,
    "checker_sha256": checker,
}) + "\n")
(owner / "FRONTEND_SOURCE_SHA").write_text(source + "\n")
PYTHON
node "$candidate/owner/frontend-check.mjs" --dist "$candidate/web/dist" --source-sha "$source_sha" > "$candidate/owner/FRONTEND_ASSETS.txt"
"$candidate/owner/deploy/prepare-release-runtime.sh" --check
```

Ship the already-attested files with the immutable candidate; do not regenerate
the digest on the runtime host. The preflight fails closed if any of the four
binding files is absent, the source/checker differs or the JS/PWA asset digests
change. The `web/scripts/check-release.mjs` checker rejects missing PWA manifest,
service worker and icon assets. The installed `owner/fleetctl.py` must be run
through its adjacent `owner/.venv/bin/python` with `PLANCIA_FLEET_CONFIG`
pointing to the private installed inventory even outside systemd.
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
