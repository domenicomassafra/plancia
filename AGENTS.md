# Plancia — contratto di prodotto e repository

Il repository canonico è `domenicomassafra/plancia`. Valgono anche `~/.dstack/contracts/shared-root/AGENTS.md` e le issue del progetto. Il nome locale storico della checkout (`muxin`) **non** determina il proprietario. Questa repo pubblica contiene UI `web/`, API e FleetProbe owner `owner/` ; il donor Muxin e stato ritirato nella #60 e ne restano storia e attribuzioni Apache-2.0.

## Confini di architettura

- `web/` è la superficie React/Vite, derivata con provenienza verificabile da `domenicomassafra/controlcenter:dashboard/web/`. Leggi `PRODUCT.md`, `DESIGN.md` e `web/PROVENANCE.md` prima di intervenire. `web/src/lib/api.ts` usa l'API esistente nella *stessa origine*: sessione autenticata, cookie di CSRF, websocket e receipt governate. Non aggiungere token o credenziali al browser, localStorage o variabili Vite.
- Il donor Muxin ritirato non deve essere ripristinato; storia e licenze restano preservate.
- `owner/` contiene FastAPI, autenticazione, FleetProbe, storico, writer API e adapter owner trasferiti da `domenicomassafra/controlcenter:dashboard/` nella #919. ControlCenter mantiene la policy generale Fleet/OmniRoute, il censimento dell'host e `configs/plancia/` con inventario privato e comandi per macchina. Database, credenziali, vault, sessioni e dati Notion non entrano nel Git pubblico.
- `web/` e `owner/` sono entrambi fonte dell'artefatto Plancia: il server serve il bundle autenticato della stessa release immutabile. Il checkout ControlCenter non ospita più un backend/dashboard alternativo. Per pubblicare seguire `DESIGN.md`; merger indipendente e reviewer #919 provano runtime e rollback dopo i merge coordinati.

## Ownership dei cambi concorrenti

- #27: confine `AGENTS.md`, `PRODUCT.md`, `DESIGN.md`, `web/README.md`, `web/PROVENANCE.md`, contratto iniziale `web/src/lib/api.ts`, import della base.
- #6: unico writer del layout, navigazione e CSS globali: `web/src/App.tsx`, `web/src/main.tsx`, `web/src/index.css` e componenti comuni. Stile fissato dall'owner: bianco, nero e grigi.
- #12: Notion database-first in `web/src/views/WorkspaceView.tsx` e moduli/controlli Notion locali. #13: Obsidian in `web/src/views/WorkspaceObsidianView.tsx` e moduli locali.
- #18: PWA, manifest e mobile, concordando con #6 ogni intervento sulla shell globale.
- #20: integrazione del frontend e release pregresse; #919: trasferimento backend e FleetProbe con PR dipendente in ControlCenter. Dopo #919 il reviewer indipendente integra i due repository e prova installazione, auth e rollback. File condivisi `web/src/lib/types.ts`, `package.json`, `web/src/lib/api.ts` restano governati dai rispettivi writer.

## Verifiche e consegna

Agenti, `npm ci`, build, test e Playwright si eseguono sul Mac Studio, unico host di lavoro, in un worktree per issue. Build e test passano da `python3 ~/.local/share/controlcenter/resource-guard/workload_launcher.py build -- <comando>`, con ammissione limitata e priorità bassa. Il MiniPC (`ssh minipc`) ospita servizi e runtime installati e si usa per verifiche che richiedono Linux o systemd. Da `owner/` eseguire la suite Python con `uv run --no-project --with-requirements requirements-dev.txt python -m pytest`; da `web/` eseguire `npm ci`, `npm run build` e `npm test`, tutti tramite il launcher. Completare con `git diff --check`, controllo privacy/provenance e lettura del diff. I sorgenti Rust del donor ritirato non vengono ripristinati.

La PR non equivale a cutover. Solo dopo merge, installazione reale, rollback e prova URL autenticato si può chiudere #919. Non versionare immagini identitarie non approvate, segreti, account, log personali, dati Notion/Obsidian o profili. Provenance donor preesistente: `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `upstream = LumaryLabsLLC/muxin` (Apache-2.0), da conservare invariati.
