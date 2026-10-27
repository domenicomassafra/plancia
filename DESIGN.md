# Plancia — architettura e confini di integrazione

## Fonte singola pubblicata

```text
Repository pubblico domenicomassafra/plancia
  web/ — React/Vite, shell/PWA e viste owner
    src/views/        Panoramica, Flotta, Agenti, Account, Workspace, Audit
    src/components/   componenti di dominio
    src/lib/api.ts    client browser same-origin, bootstrap sessione, CSRF
    src/lib/types.ts contratti dati ricevuti dal backend canonico
        |
        | HTTPS stessa origine + /api, /api/v1, /api/stream
        v
ControlCenter (repository privato, proprietario esclusivo dei writer)
  dashboard/backend/  auth/sessione, FastAPI, API, receipt, FleetProbe
  fleet/, policies, profile-atlas, workspace writer canonici
        |
        +--> Notion Actions attraverso Registry/transaction
        +--> vault Obsidian tramite writer canonico/CAS
        +--> agenti e host tramite bridge/donor autorizzati
```

Le attribuzioni storiche del donor Muxin ritirato sono conservate separatamente in `LICENSE`, `NOTICE` e `THIRD_PARTY_NOTICES.md`; non fanno parte del runtime web.

**Cutover #20:** la release immutabile sul MiniPC combina l'artefatto frontend di Plancia con il backend ControlCenter. `ControlCenter/dashboard/frontend.json` vincola commit Plancia e digest del suo checker; il preflight verifica fonte e digest di ogni asset contro l'attestazione prodotta sul Mac Studio. I vecchi sorgenti `ControlCenter/dashboard/web/` e i relativi build nei workflow sono stati rimossi con [ControlCenter #511](https://github.com/domenicomassafra/controlcenter/pull/511). Release precedenti e ricevute di deploy restano disponibili per il rollback nativo; non sono fonti UI da modificare.

## Contratto browser/API conservato

Il client invoca `GET /api/v1/session` con credenziali `same-origin` per il bootstrap; richieste non GET includono l'header `X-CSRF-Token` dal cookie `__Host-controlcenter_csrf`, il backend mantiene l'identità owner e verifica sessione/Origin. Le chiamate restano relative alla stessa origine: `/api/fleet`, `/api/stream` (websocket), `/api/receipts`, `/api/profiles`, `/api/workspace`, `/api/workspace/notion`, `/api/workspace/obsidian`, `/api/workspace/operations/action-item.edit/*`, `/api/omniroute`. La sezione Agenti integra il consumer `/agents` e le API `/api/v1/agents/*` e `/api/v1/hermes/*`; capacità di lettura, attach e invio rimangono distinte secondo il backend. Non ridefinire body, idempotency, audit, error semantics o CORS dalla UI per aggirare il server.

La configurazione Vite preesistente proxies `/api` a `127.0.0.1:8000` **solo nel dev locale**; in produzione è la release owner same-origin a servire bundle e API tramite auth. Il bundle statico da solo non è servizio, e qualsiasi viewer anonimo deve ricevere la risposta protetta prevista dal backend. Non memorizzare cookie/sessioni/API key nel client e non usare backend di test come writer paralleli.

## Layout condiviso e responsabilità

`web/` mantiene nomi e import relativi dell'app Signal Deck per un'importazione verificabile. #27 registra origine e API; #6 modifica esclusivamente shell/CSS globale e nome/branding visibili, bianco/nero/grigio; #12 e #13 possiedono rispettivamente i moduli Notion e Obsidian; #18 concorda manifest/PWA con #6; #20 possiede integrazione/deploy. Ogni nuovo donor è un pacchetto o servizio versionato separato con licenza, API e test del confine; niente patch o fusione del suo core.

## Pubblicazione e verifica

1. Integrare via PR e check su `plancia/main`, rileggere SHA remoto; sul Mac Studio eseguire build/test e il checker di `web/scripts/` sull'artefatto esatto. Conservare attestazione asset e commit sorgente.
2. Aggiornare il pin in ControlCenter tramite PR. Comporre una release immutabile con backend da commit verificato, bundle Plancia, checker e attestazione; il preflight rifiuta fonte, checker o asset alterati. Build e suite restano sul Mac Studio.
3. Usare il materializzatore nativo ControlCenter, con preimage di symlink/unit e assenza di azioni in corso. Promuovere, verificare auth anonima e owner, provare il ritorno alla release precedente e ripromuovere il candidato. Conservare la ricevuta canonica del deploy.
4. Provare le sei sezioni, storico, policy, preview e Audit sul consumer HTTPS; per l'editor usare solo una fixture sintetica nel vault canonico, con CAS, ricevuta e readback. Per Notion verificare preview e post-fetch senza modificare Actions personali. Registrare risultati e limiti in #20 e le prove MiniPC in controlcenter#484.
5. Le verifiche fisiche iPhone e Windows appartengono a #25. Riportare capacità non configurate o non provate come tali; non certificare attach, desktop remoto o parità collector dalla sola presenza della UI.

**Vincolo pubblicazione:** `plancia` è pubblico, `controlcenter` privato. Ogni import va trattato come disclosure pubblica della sola UI: nessun file `.env`, binario personalizzato, screenshot dei dispositivi, percorso privato, identità account o fixture reali. La copia iniziale sostituisce due e-mail fixture con identità `example.test` ed esclude gli asset PNG dei dispositivi, mentre lascia la grafica React/SVG del prodotto.
