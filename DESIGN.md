# Plancia — architettura e confini di integrazione

## Fonte singola pubblicata

```text
Repository pubblico domenicomassafra/plancia
  web/ — React/Vite, shell/PWA e viste owner
    src/views/        Panoramica, Flotta, Agenti, Account, Workspace, Audit
    src/components/   componenti di dominio
    src/lib/api.ts    client browser same-origin, bootstrap sessione, CSRF
    src/lib/types.ts contratti dati ricevuti dal backend Plancia
  owner/ — backend FastAPI e FleetProbe trasferiti in ControlCenter #919
    backend/         sessione, FastAPI, API, audit, azioni e writer governati
    agent/           FleetProbe e installer multipiattaforma
    deploy/          unità Plancia, preflight e composizione release
    tests/           regressioni di auth, stato, storico, API e collector
  src/, agent/, share-server/ — donor legacy Muxin isolato (Apache-2.0)
        |
        | un solo origin HTTPS autenticato; /api, /api/v1, /api/stream
        v
Runtime MiniPC
  ~/.local/share/plancia/current/{web,owner}
  ~/.local/state/controlcenter/dashboard-state/  storico, audit e ricevute
  ~/.config/controlcenter/secrets/            segreti e identità owner
        |
        +--> Notion Actions attraverso Registry/transaction
        +--> vault Obsidian tramite writer canonico/CAS
        +--> agenti e host tramite bridge/donor autorizzati
        +--> ControlCenter privato: configs/plancia/ per inventory e comandi
             fleet/policy/ per unità, orchestrazione e veridicità delle fonti
```

**Cutover #919:** una singola revisione verificata di Plancia fornisce sia il backend `owner/` sia il bundle `web/dist/`. `web/scripts/check-release.mjs` controlla i file pubblicati e `owner/deploy/prepare-release-runtime.sh --check` verifica che siano realmente presenti prima di avviare il servizio. Le configurazioni private e gli storage non sono copiati nel Git pubblico. Il vecchio backend ControlCenter resta installato soltanto come rollback durante il passaggio, senza doppio processo sulla porta 8000. Il frontend era già stato estratto con [ControlCenter #511](https://github.com/domenicomassafra/controlcenter/pull/511).

## Contratto browser/API conservato

Il client invoca `GET /api/v1/session` con credenziali `same-origin` per il bootstrap; richieste non GET includono l'header `X-CSRF-Token` dal cookie `__Host-controlcenter_csrf`, il backend mantiene l'identità owner e verifica sessione/Origin. Le chiamate restano relative alla stessa origine: `/api/fleet`, `/api/stream` (websocket), `/api/receipts`, `/api/profiles`, `/api/workspace`, `/api/workspace/notion`, `/api/workspace/obsidian`, `/api/workspace/operations/action-item.edit/*`, `/api/omniroute`. La sezione Agenti integra il consumer `/agents` e le API `/api/v1/agents/*` e `/api/v1/hermes/*`; capacità di lettura, attach e invio rimangono distinte secondo il backend. Non ridefinire body, idempotency, audit, error semantics o CORS dalla UI per aggirare il server.

La configurazione Vite preesistente proxies `/api` a `127.0.0.1:8000` **solo nel dev locale**; in produzione è la release owner same-origin a servire bundle e API tramite auth. Il bundle statico da solo non è servizio, e qualsiasi viewer anonimo deve ricevere la risposta protetta prevista dal backend. Non memorizzare cookie/sessioni/API key nel client e non usare backend di test come writer paralleli.

## Layout condiviso e responsabilità

`web/` mantiene nomi e import relativi dell'app Signal Deck per un'importazione verificabile. #27 registra origine e API; #6 modifica esclusivamente shell/CSS globale e nome/branding visibili, bianco/nero/grigio; #12 e #13 possiedono rispettivamente i moduli Notion e Obsidian; #18 concorda manifest/PWA con #6; #20 possiede integrazione/deploy. Ogni nuovo donor è un pacchetto o servizio versionato separato con licenza, API e test del confine; niente patch o fusione del suo core.

## Pubblicazione e verifica

1. Integrare via review indipendente la PR Plancia #61 e la PR ControlCenter #932 (issue #919), rileggendo entrambi gli SHA remoti. Sul Mac Studio eseguire test backend `owner/` e build/test di `web/`, poi validare `web/scripts/check-release.mjs` per lo SHA esatto e gli asset del bundle.
2. Dal checkout ControlCenter privato usare `tools/install-plancia-private-config.sh --check` e poi `--apply minipc` per installare inventario e comandi locali in `~/.config/plancia/` sul MiniPC (file mode 0600, directory 0700). Installare un albero immutabile `~/.local/share/plancia/releases/<SHA-Plancia>/{owner,web}` con `web/dist/` verificato. Eseguire `owner/deploy/prepare-release-runtime.sh` per legare la venv, controllare `--check`, predisporre il symlink `current` e la nuova `plancia-backend.service` con `PLANCIA_FLEET_CONFIG` e `PLANCIA_PROFILE_COMMANDS_FILE` puntati alla destinazione privata installata.
3. Evitare due owner writer: prima di attivare `plancia-backend.service`, fermare `controlcenter-fleet-dashboard.service` dopo aver conservato il vecchio selector. Verificare auth anonima 401, auth owner 200 e la stessa origine HTTPS; in caso di errore ripristinare il servizio e il selector precedenti. Solo il reviewer della PR procede al cutover e legge i servizi realmente attivi.
4. Per i FleetProbe usare `owner/agent/install/` sulla release Plancia pin, mantenendo token, identità runtime e unità piattaforma esistenti; leggere i processi e l'identità dopo il riavvio. Mac Studio/MiniPC/Omarchy/Air sono distinti; offline → controlcenter#484.
5. Provare le sei sezioni, storico preesistente, policy, preview e Audit sul consumer HTTPS; Notion richiede preview/post-fetch e Obsidian solo fixture canonica con CAS. Riportare risultati, limiti e chiusura in ControlCenter #919. Le verifiche fisiche iPhone e Windows appartengono a #25: riportare le capacità non provate come tali.

**Vincolo pubblicazione:** `plancia` è pubblico, `controlcenter` privato. L'import del backend in #919 è una pubblicazione di sorgente: nessun file `.env`, binario personale, screenshot identificativo, IP della tailnet, indirizzo account o fixture reale. `owner/fleet.json` contiene soltanto indirizzi RFC 5737 di esempio e i percorsi Profile Atlas operativi sono caricati da `PLANCIA_PROFILE_COMMANDS_FILE` fuori dal Git pubblico. Nessun database o segreto viene migrato nel repository.
