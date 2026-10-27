# Plancia — contratto di prodotto e repository

Il repository canonico è `domenicomassafra/plancia`. Valgono anche `~/.dstack/contracts/shared-root/AGENTS.md` e le issue del progetto. Il nome locale storico della checkout (`muxin`) **non** determina il proprietario. Questa repo pubblica distribuisce la UI owner Plancia da `web/`; i sorgenti desktop, agent e share-server del donor Muxin sono stati ritirati nella #60. La storia e le attribuzioni Apache-2.0 del donor restano disponibili.

## Confini di architettura

- `web/` è la superficie React/Vite, derivata con provenienza verificabile da `domenicomassafra/controlcenter:dashboard/web/`. Leggi `PRODUCT.md`, `DESIGN.md` e `web/PROVENANCE.md` prima di intervenire. `web/src/lib/api.ts` usa l'API esistente nella *stessa origine*: sessione autenticata, cookie di CSRF, websocket e receipt governate. Non aggiungere token o credenziali al browser, localStorage o variabili Vite.
- Il donor Muxin ritirato non è un componente da ripristinare. Non patchare il suo core, protocolli, GUI GPUI, CLI o MCP senza l'autorizzazione specifica dell'owner; usa adapter separati con versioni e licenze documentate.
- Backend FastAPI, API owner, FleetProbe, storico 14 giorni, flotta, policy, scheduler, Notion/Obsidian writer, Profile Atlas, OmniRoute e audit restano **esclusivamente** in `domenicomassafra/controlcenter`. Nessuna copia di backend, DB, vault, sessione agentica o policy qui. La UI consulta queste fonti solo tramite API dello stesso origin, senza duplicare i writer.
- Plancia è l'unica fonte del frontend pubblicato. ControlCenter conserva `dashboard/frontend.json` con il commit Plancia e il digest del checker, verifica l'artefatto compilato sul Mac Studio e serve UI/API nella stessa release immutabile. `dashboard/web/` è stato eliminato da ControlCenter dopo la migrazione dei consumer. Per pubblicare seguire `DESIGN.md` e riportare la prova runtime in #20.

## Ownership dei cambi concorrenti

- #27: confine `AGENTS.md`, `PRODUCT.md`, `DESIGN.md`, `web/README.md`, `web/PROVENANCE.md`, contratto iniziale `web/src/lib/api.ts`, import della base.
- #6: unico writer del layout, navigazione e CSS globali: `web/src/App.tsx`, `web/src/main.tsx`, `web/src/index.css` e componenti comuni. Stile fissato dall'owner: bianco, nero e grigi.
- #12: Notion database-first in `web/src/views/WorkspaceView.tsx` e moduli/controlli Notion locali. #13: Obsidian in `web/src/views/WorkspaceObsidianView.tsx` e moduli locali.
- #18: PWA, manifest e mobile, concordando con #6 ogni intervento sulla shell globale.
- #20: unico writer di merge su `plancia/main`, installazione e verifica consumer. Ogni feature segue la PR base #27 integrata; file condivisi `web/src/lib/types.ts`, `package.json`, `web/src/lib/api.ts` richiedono accordo con il proprietario prima di scritture parallele.

## Verifiche e consegna

Agenti, `npm ci`, build, test e Playwright si eseguono sul Mac Studio, unico host di lavoro, in un worktree per issue. Build e test passano da `python3 ~/.local/share/controlcenter/resource-guard/workload_launcher.py build -- <comando>`, con ammissione limitata e priorità bassa. Solo un affanno misurato dello Studio giustifica worker aggiuntivi sul MiniPC; non si avviano di default su altri host. Il MiniPC (`ssh minipc`) ospita servizi e runtime installati e si usa per verifiche che richiedono Linux o systemd. Dalla directory `web/` eseguire `npm ci`, `npm run build` e `npm test` tramite il launcher; completare con `git diff --check`, controllo di privacy/provenance e lettura del diff. Per la distribuzione web non esistono build o suite Rust; conservarne storia e attribuzioni legali.

La PR non equivale a cutover. Solo dopo merge, installazione reale e prova dell'URL autenticato si può rivendicare un'implementazione consegnata; riportare SHA, comandi ed esiti in #27 e #20. Il `main` si integra soltanto tramite #20, senza push concorrenti. Non versionare immagini identitarie non approvate, segreti, account, log personali, dati Notion/Obsidian o profili. Provenance donor preesistente: `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `upstream = LumaryLabsLLC/muxin` (Apache-2.0), da conservare invariati.
