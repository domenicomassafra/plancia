# Plancia #103 — inventario native-first

Verifica: 3 novembre 2026. Baseline: `3fb21280efabf1dde0ac70786092ce26a7effe9d`.
L'inventario copre tutte le famiglie di codice operativo tracciato sotto
`.github/`, `owner/` e `web/`, raggruppando moduli che svolgono un unico compito.
Test, manifest statici, immagini, licenze e documenti sono considerati risorse
dei rispettivi componenti e non processi/configurazioni autonomi. `GAP`
significa che la funzione nativa non replica l'operazione owner autenticata
richiesta, non che il prodotto upstream sia privo di funzionalità.

## Fonti native verificate

- Orca 1.4.243 su Studio, 1.4.240 MiniPC, 1.4.245 MateBook, 1.4.247 Air:
  `orca --help`, `orca agent launch --help`, `orca account list --help`,
  `orca account add --help`, `orca serve --help`, verificati sugli host.
  [Account Codex nativo](https://www.onorca.dev/docs/agents/codex),
  [selettore account](https://www.onorca.dev/docs/agents/codex-hot-swap),
  [CLI](https://github.com/stablyai/orca/blob/main/docs/site/content/docs/cli/reference.mdx).
  Orca non espone ancora `--account` per un lancio Codex per-worktree
  ([issue upstream #7782](https://github.com/stablyai/orca/issues/7782));
  il selettore di runtime dell'app preserva l'identità delle sessioni esistenti.
- Hermes su Studio 0.22.6+488 e su MiniPC 0.22.6+481:
  `hermes --help` e `hermes webapp --help`; dashboard/webapp, profili,
  sessioni, cron e gateway sono nativi
  ([documentazione desktop](https://hermes-agent.nousresearch.com/docs/user-guide/desktop)).
  Non sono presenti installer Hermes in questo repository.
- Tailscale 1.104.4 Studio: `tailscale status --help` e
  [Tailscale Serve](https://tailscale.com/kb/1312/serve);
  systemd, launchd, Docker Compose, GitHub Actions, pytest, npm/Vite, SQLite
  e browser WebSocket usati con le rispettive interfacce ufficiali.
  Documentazione e implementazione del repository esaminate il 3 novembre 2026.

## Inventario operativo

| Elemento | Percorso sorgente | Strumento / funzione nativa | Classe e decisione |
| --- | --- | --- | --- |
| Vecchia pagina Agenti duplicata | `owner/backend/agents/{index.html,client.js}`, `agents_api.py` | Sezione Plancia `/#agents` con dashboard Hermes e API Orca native | **REPLACE**: eliminati 2 asset, `/agents` fa redirect protetto a `/#agents` |
| Lancio Codex e selezione account scritti a mano | `owner/backend/{codex_sessions,codex_run}.py`, `server.py` | Orca Account switcher + agent combobox / `orca agent launch --agent codex --model …` | **REPLACE**: rimossi launcher e API owner proprietari. Il cambio account avviene nell'app Orca ufficiale, sulle sessioni nuove |
| Test e fixture del frontend/launcher ritirati | `owner/tests/test_agents_live_ui.cjs`, `test_agents_api.py` | Suite Python API owner e `web/tests/orca-chat-contract.test.ts` | **REPLACE**: rimosse fixture non più chiamate, verificato redirect owner+anonimo |
| Vista Hermes integrata | `web/src/views/{AgentsSection,HermesDashboardView}.tsx`, `web/src/lib/hermes-dashboard.ts` | Webapp/dashboard Hermes nativo, `iframe` su origin autorizzato | **NATIVE**: mantenere, fail closed senza origin isolato |
| Lettura sessioni Hermes redatte e input autorizzato | `owner/backend/{agents_hermes,hermes_native_input}.py`, `web/src/views/AgentsSignoriView.tsx`, `web/src/lib/{agents-remoting,hermes-input-flow}.ts` | Gateway Hermes `session.resume`, `prompt.submit`, `sessions export` | **GAP**: proiezione redatta e auth owner per l'origine Plancia; #48 gestisce la capacità live, che non si può dichiarare attiva qui |
| Elenco e input terminali Orca | `owner/backend/{agents_api,agents_live}.py`, `web/src/lib/orca-chat.ts` | `orca terminal list/read/send`, `orca search` | **NATIVE+GAP**: la sorgente e i terminali sono ufficiali; resta il confine same-origin/autorizzazioni del portale |
| Vista conversazioni Orca e timer schermo | `web/src/views/OrcaChatPanel.tsx` | Client Orca ufficiale, CLI `terminal read --screen` | **GAP**: Orca non offre al portale un componente Web embeddabile con la stessa sessione owner; ritardo 2,5 s usa timer browser nativo e si disattiva fuori vista |
| Remoting desktop | `owner/backend/{desktops,desktop_guacd}.py`, `web/src/views/{RemoteDesktopView,GuacamoleSession}.tsx` | Apache Guacamole JS/client e `guacd` | **NATIVE+GAP**: client e protocollo donor; autenticazione, allowlist e destinazioni appartengono a Plancia |
| Runtime guacd | `owner/deploy/guacd.compose.yaml` | Docker Compose / guacd | **NATIVE**: nessun server sostitutivo |
| Stato e sessione owner | `owner/backend/{auth,runtime_config,server}.py` | FastAPI/Starlette, Tailscale Serve, cookie/CSRF | **GAP**: Tailscale protegge trasporto e dispositivo, l'API richiede sessione owner/CSRF stessa origine |
| Fleet discovery | `owner/backend/fleet2/{poller,registry,health}.py` | `tailscale status --json`, HTTP ufficiale FleetProbe | **NATIVE+GAP**: Tailscale dà stato overlay, non età campioni multi-host, health e failover autenticato |
| Fleet stato e audit | `owner/backend/fleet2/{store,audit}.py` | SQLite e API FastAPI | **GAP**: cronologia e audit del prodotto, nessun database duplicato |
| Azioni e bridge Fleet | `owner/backend/fleet2/{actions,agent_bridge,api,request_models}.py`, `owner/fleetctl.py` | SSH, systemd/launchctl e API native host | **GAP**: preview/CAS/receipts e gate owner operano attraverso host diversi; non lanciano agenti paralleli |
| Profile Atlas API | `owner/backend/browser_profiles/*.py`, `owner/backend/fleet2/profiles_api.py` | Gestione profili nativa Chrome/Edge e processi host | **GAP**: dedup identità, rename multi-host e piani protetti non sono un'API nativa cross-host |
| Profile Atlas donor/CLI | `owner/integrations/profile-atlas/src/chrome_profile_launcher/*.py`, `pyproject.toml` | Profili browser originali, CLI Profile Atlas | **GAP**: app owner MIT riusata; nessuna copia delle credenziali dei profili in Plancia |
| Workspace Notion | `owner/backend/fleet2/workspace_notion_read.py`, `workspace_projects_projection.py`, `web/src/views/Notion*.tsx` | Notion API/database/relations | **NATIVE+GAP**: dati persistono in Notion; il portale richiede proiezione e gestione permessi owner |
| Workspace Obsidian | `owner/backend/fleet2/workspace_obsidian*.py`, `web/src/views/{WorkspaceObsidianView,obsidian/*}.tsx` | Markdown/Obsidian originale | **NATIVE+GAP**: editor/proiezione senza creare un secondo archivio di note; applicazione con CAS owner |
| Workspace vista, fonti, preview e rollback | `owner/backend/fleet2/workspace*.py`, `web/src/views/{WorkspaceView,WorkspaceSections,NotionWorkspace,NotionEditPanel}.tsx` | Notion e Obsidian API native | **GAP**: coordinamento di sorgenti diverse e conferma delle azioni, senza sostituire la fonte canonica |
| SilverBullet HTTPS bridge | `owner/backend/workspace_silverbullet.py`, `owner/deploy/controlcenter-plancia-silverbullet.service` | SilverBullet UI WebSocket nativa, systemd | **GAP**: proxy limitato per origine owner/ACL, protegge percorsi riservati |
| OmniRoute status | `owner/backend/omniroute_status.py`, `web/src/components/{OmniRoutePanel,omniroute-reader}.tsx` | API/CLI OmniRoute originali | **NATIVE+GAP**: proiezione senza modelli/proxy aggiuntivi |
| SSH e limitatore processi | `owner/backend/{ssh_transport,native_probe}.py` | OpenSSH, `asyncio.Semaphore` | **NATIVE+GAP**: wrapper solo per allowlist, tempi e sicurezza delle richieste owner |
| FleetProbe multi-OS | `owner/agent/fleetprobe*.py` | Statistiche native macOS/Linux/Windows, HTTP | **GAP**: non esiste un singolo provider OS per temperatura, CPU, memoria, schema e token compatibili su tutte le macchine |
| Scheduling campioni e notifiche CoS | `owner/agent/fleetprobe.py`, `cos_orca_observer.py`, `cos_orca_routes.py` | Thread/eventi nativi, CoS read-only API, `orca orchestration send` | **GAP**: transizione proprietaria fra CoS/GitHub e run Orca, non nuova ammissione; DStack #356 è proprietario del sender canonico |
| Installatori host agent | `owner/agent/install/*` | `systemctl --user`, `launchctl`, PowerShell | **NATIVE+GAP**: le unità sono native; gli installer bindano token, host-id e readback |
| Backend unit e runtime immutabile | `owner/deploy/{plancia-backend.service,prepare-release-runtime.sh}`, `owner/requirements.txt` | systemd, Python venv/pip, checksum SHA, link atomico | **NATIVE+GAP**: serve a legare dipendenze, build e rollback al medesimo SHA; non è un manager alternativo |
| Migrazione conservativa dei dati | `owner/tools/migrate_runtime_state.py` | File system/SQLite | **GAP**: migrazione offline con hash e rollback dei file legacy; nessuna cancellazione dati unici |
| Fleet WebSocket con fallback | `web/src/lib/api.ts`, `owner/backend/fleet2/api.py` | `WebSocket`, `fetch`, timer browser | **NATIVE**: usa trasporto browser standard; fallback HTTP in caso di disconnessione |
| Refresh views, conferme e debounce | `web/src/{App.tsx,views/ProfilesView.tsx,views/obsidian/ObsidianEditor.tsx,components/HostActions.tsx}` | Eventi React + `setInterval/setTimeout` nativi | **GAP**: freshness e conferme UI diverse dalle sessioni donor; nessun polling del donor duplicato |
| Viste principali e stato UI | `web/src/{views/{OverviewView,FleetView,ActivityView,ProfilesView},components/*,lib/{overview-state,format,profile-groups,types}.ts}` | React/Vite | **NATIVE+GAP**: prodotto Plancia, non un clone del runtime Hermes/Orca |
| Toolchain frontend | `web/{package.json,package-lock.json,vite.config.*,tsconfig*.json,postcss.config.*,tailwind.config.*}` | npm, Vite, TypeScript e PostCSS | **NATIVE**: usare i runner dichiarati, nessuno startup wrapper |
| CI e automazioni GitHub | `.github/workflows/*.yml` | GitHub Actions, actions/add-to-project, `gh api graphql` | **NATIVE+GAP**: richiesti check backend/web invariati e su reopen ripristino Status/Prodotto nel Project owner; auto-add nativo da solo non ripristina tutti i campi |
| Template GitHub | `.github/ISSUE_TEMPLATE/*` | GitHub Issue Forms | **NATIVE**: nessuno script parallelo |
| Test e verifiche | `owner/tests/*`, `web/tests/*`, `owner/integrations/profile-atlas/tests/*` | pytest, Node Test Runner, browser test | **NATIVE**: test dei contratti mantenuti; fixture del frontend ritirato eliminata |

## Azioni e confini del cambio

La sola sostituzione di questa tranche riguarda la pagina parallela Agenti,
il lanciatore Codex incorporato e i test esclusivi di quel frontend.
La navigazione `/agents` resta compatibile tramite redirect, autenticata
dal middleware esistente. I Codex già avviati e i loro account restano di Orca.
I receipt storici nel runtime owner sono preservati; non vengono cancellati.
Gli altri elementi GAP hanno consumer verificati o vincoli di sicurezza e
non sono eliminabili senza una sostituzione nativa equivalente e una prova reale.
