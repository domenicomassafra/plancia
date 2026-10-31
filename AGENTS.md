# Plancia — product and repository contract

The canonical repository is `domenicomassafra/plancia`. The shared contract at `~/.dstack/contracts/shared-root/AGENTS.md` and project issues also apply. The former local checkout name (`muxin`) does not establish ownership. This public repository contains the `web/` UI and the `owner/` API and FleetProbe. The Muxin donor was retired in #60; its history and Apache-2.0 attributions remain.

## Architecture boundaries

- `web/` is the React/Vite UI, with traceable provenance from `domenicomassafra/controlcenter:dashboard/web/`. Read `PRODUCT.md`, `DESIGN.md`, and `web/PROVENANCE.md` before making changes. `web/src/lib/api.ts` uses the existing *same-origin* API with authenticated sessions, CSRF cookies, WebSockets, and governed receipts. Never add browser tokens or credentials to localStorage or Vite environment variables.
- Do not restore the retired Muxin donor; preserve its history and licenses.
- `owner/` contains FastAPI, authentication, FleetProbe, history, writer APIs, and owner adapters moved from `domenicomassafra/controlcenter:dashboard/` in #919. ControlCenter retains general Fleet/OmniRoute policy, host inventory, and `configs/plancia/` with private inventory and host commands. Databases, credentials, vaults, sessions, and Notion data must never enter public Git.
- `web/` and `owner/` are both sources for Plancia's immutable release; the server delivers the authenticated bundle from that same release. ControlCenter no longer hosts a competing backend/dashboard checkout. Follow `DESIGN.md` for release preparation; the accountable issue author verifies installation, authenticated same-origin use, and rollback after the coordinator schedules integration.

## Concurrent change ownership

- #27 historically established the initial `AGENTS.md`, `PRODUCT.md`, `DESIGN.md`, `web/README.md`, `web/PROVENANCE.md`, `web/src/lib/api.ts` contract and UI import. Later changes belong to their current issue author.
- #6 historically owned the shared shell, navigation, global CSS (`web/src/App.tsx`, `web/src/main.tsx`, `web/src/index.css`), and common components. The approved palette is white, black, and gray.
- #12 established the Notion database-first UI in `web/src/views/WorkspaceView.tsx` and local Notion modules; #13 established Obsidian in `web/src/views/WorkspaceObsidianView.tsx` and local modules.
- #18 established PWA, manifest, and mobile support in coordination with #6 on shared shell changes.
- #20 documents the historical frontend integration and releases; #919 documents the backend and FleetProbe migration. Neither grants a permanent merge monopoly. The accountable author of each current issue owns its fixes, review response, CI, guarded merge, installation, real consumer acceptance, and GitHub CLOSED readback. Coordinate concurrent edits to `web/src/lib/types.ts`, `package.json`, and `web/src/lib/api.ts` with their active writers.

## Verification and delivery

Run agent work, `npm ci`, builds, tests, and Playwright on Mac Studio, in an issue-specific worktree. Use `python3 ~/.local/share/controlcenter/resource-guard/workload_launcher.py worker -- <command>` for builds and tests. MiniPC (`ssh minipc`) hosts installed services and runtimes and is used for Linux/systemd verification. Under `owner/`, run the Python suite with `uv run --no-project --with-requirements requirements-dev.txt python -m pytest`; under `web/`, run `npm ci`, `npm run build`, and `npm test`, all through the launcher. Also run `git diff --check`, inspect privacy/provenance and the diff. Do not restore retired donor Rust sources.

A PR is source-ready only when its required CI is green. Resolve actual conflicts and failures; do not update a clean, mergeable head just to chase `main`. The author merges with `--match-head-commit` and verifies remote `main`. Close the current issue only after installation where used, real authenticated URL consumer proof, rollback where required, and every original acceptance criterion; record the evidence in that issue, with #20/#27/#919 retained as historical references. Do not commit unapproved device images, secrets, accounts, personal logs, Notion/Obsidian data, or profiles. Preserve donor provenance: `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `upstream = LumaryLabsLLC/muxin` (Apache-2.0).
