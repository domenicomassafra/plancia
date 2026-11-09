# Plancia — architettura e confini di integrazione

## Fonte singola pubblicata

```text
Repository pubblico domenicomassafra/plancia
  web/ — React/Vite, shell/PWA e viste owner
    src/views/        Panoramica, Flotta, Agenti, Account, Workspace, Audit
    src/components/   componenti di dominio
    src/lib/api.ts    client browser same-origin, bootstrap sessione, CSRF
    src/lib/types.ts contratti dati ricevuti dal backend Plancia
  Donor Muxin ritirato (#60): storia e attribuzioni Apache-2.0 conservate
Repository privato domenicomassafra/controlcenter
  dashboard/ — backend FastAPI, auth, FleetProbe, release e regressioni
        |
        | un solo origin HTTPS autenticato; /api, /api/v1, /api/stream
        v
Runtime MiniPC
  ~/.local/share/plancia/current/{web,owner} (sorgenti pin Plancia + ControlCenter)
  ~/.local/state/controlcenter/dashboard-state/  storico, audit e ricevute
  ~/.config/controlcenter/secrets/            segreti e identità owner
        |
        +--> Notion Actions attraverso Registry/transaction
        +--> vault Obsidian tramite writer canonico/CAS
        +--> agenti e host tramite bridge/donor autorizzati
        +--> ControlCenter privato: configs/plancia/ per inventory e comandi
             fleet/policy/ per unità, orchestrazione e veridicità delle fonti
```

**Consolidamento 9 novembre 2026:** il codice `owner/` importato in #919 è tornato a `ControlCenter/dashboard/`. La release immutabile assembla il backend da un commit ControlCenter e `web/dist/` da un commit Plancia, senza due processi backend. `web/scripts/check-release.mjs` e i quattro file di attestazione generati sul Mac Studio (procedura in ControlCenter `dashboard/README.md`) vincolano SHA frontend, checker e digest di ogni asset/PWA. `dashboard/deploy/prepare-release-runtime.sh --check`, eseguito nella release sotto `owner/`, rifiuta bundle privi di attestazione o differenti. Configurazioni e storage rimangono privati. Il frontend era già stato estratto con [ControlCenter #511](https://github.com/domenicomassafra/controlcenter/pull/511).

## Contratto browser/API conservato

Il client invoca `GET /api/v1/session` con credenziali `same-origin` per il bootstrap; richieste non GET includono l'header `X-CSRF-Token` dal cookie `__Host-controlcenter_csrf`, il backend mantiene l'identità owner e verifica sessione/Origin. Le chiamate restano relative alla stessa origine: `/api/fleet`, `/api/stream` (websocket), `/api/receipts`, `/api/profiles`, `/api/workspace`, `/api/workspace/notion`, `/api/workspace/obsidian`, `/api/workspace/operations/action-item.edit/*`, `/api/omniroute`. La sezione Agenti integra il consumer `/agents` e le API `/api/v1/agents/*` e `/api/v1/hermes/*`; capacità di lettura, attach e invio rimangono distinte secondo il backend. Non ridefinire body, idempotency, audit, error semantics o CORS dalla UI per aggirare il server.

La configurazione Vite preesistente proxies `/api` a `127.0.0.1:8000` **solo nel dev locale**; in produzione è la release owner same-origin a servire bundle e API tramite auth. Il bundle statico da solo non è servizio, e qualsiasi viewer anonimo deve ricevere la risposta protetta prevista dal backend. Non memorizzare cookie/sessioni/API key nel client e non usare backend di test come writer paralleli. La sezione canonica Agenti è `/#agents`: il vecchio percorso `/agents` vi reindirizza dopo l'autenticazione owner, senza una seconda pagina con sessioni/launcher propri.

## Layout condiviso e responsabilità

`web/` mantiene nomi e import relativi dell'app Signal Deck per un'importazione verificabile. #27 registra origine e API; #6 modifica esclusivamente shell/CSS globale e nome/branding visibili, bianco/nero/grigio; #12 e #13 possiedono rispettivamente i moduli Notion e Obsidian; #18 concorda manifest/PWA con #6. #20 records historical integration/deployment; subsequent changes belong to the accountable author of each current issue under `AGENTS.md`. Ogni nuovo donor è un pacchetto o servizio versionato separato con licenza, API e test del confine; niente patch o fusione del suo core.

## Pubblicazione e verifica

The following steps record the historical #919 cutover. For subsequent releases, review, merge, installation, verification and issue closure follow the shared v2 contract at `~/.dstack/contracts/shared-root/AGENTS.md`; the current issue defines scope and acceptance criteria.

1. Rileggere entrambi gli SHA remoti; sul Mac Studio eseguire test backend in ControlCenter `dashboard/` e build/test in Plancia `web/`, poi validare `web/scripts/check-release.mjs` per lo SHA esatto e gli asset del bundle.
2. Dal checkout ControlCenter privato usare `tools/install-plancia-private-config.sh --check` e poi `--apply minipc` per installare inventario e comandi locali in `~/.config/plancia/` sul MiniPC. Assemblare una release immutabile `~/.local/share/plancia/releases/<SHA-ControlCenter>-<SHA-Plancia>/{owner,web}`: `owner/` viene da ControlCenter `dashboard/`, `web/dist/` da Plancia. La coppia dei commit impedisce collisioni se cambia solo la UI. Eseguire `owner/deploy/prepare-release-runtime.sh` sul candidato e poi il check prima di selezionare `current`; mantenere `PLANCIA_FLEET_CONFIG` e `PLANCIA_PROFILE_COMMANDS_FILE` sui percorsi privati installati.
3. Evitare due owner writer: prima di attivare `plancia-backend.service`, fermare `controlcenter-fleet-dashboard.service` dopo aver conservato il vecchio selector. Verificare auth anonima 401, auth owner 200 e la stessa origine HTTPS; in caso di errore ripristinare il servizio e il selector precedenti. Leggere i servizi realmente attivi durante il cutover.
4. Per i FleetProbe usare `dashboard/agent/install/` da ControlCenter sulla revisione pin, mantenendo token, identità runtime e unità piattaforma esistenti; leggere i processi e l'identità dopo il riavvio. Mac Studio/MiniPC/Omarchy/Air sono distinti; offline → controlcenter#484.
5. Provare le sei sezioni, storico preesistente, policy, preview e Audit sul consumer HTTPS; Notion richiede preview/post-fetch e Obsidian solo fixture canonica con CAS. Riportare risultati, limiti e chiusura in ControlCenter #919. Le verifiche fisiche iPhone e Windows appartengono a #25: riportare le capacità non provate come tali.

**Vincolo pubblicazione:** `plancia` è pubblico, `controlcenter` privato. Nessun file `.env`, binario personale, screenshot identificativo, IP della tailnet, indirizzo account o fixture reale deve entrare in Git. `dashboard/fleet.json` contiene soltanto indirizzi RFC 5737 di esempio; Profile Atlas legge `PLANCIA_PROFILE_COMMANDS_FILE` fuori dal Git pubblico. Nessun database o segreto viene migrato nel repository.
