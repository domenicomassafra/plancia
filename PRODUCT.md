# Plancia — prodotto e requisiti

Plancia è il punto di controllo unico dell'owner, **web-first** su desktop e iPhone. La base scelta il 20 ottobre 2026 è la superficie Signal Deck, con donor specializzati utilizzati tramite API/plugin/embed dove il comportamento è stato provato. Il backend operativo e FleetProbe fanno ora parte di `owner/`; la policy generale e la configurazione privata restano in ControlCenter. Le decisioni e l'origine sono documentate in [#22](https://github.com/domenicomassafra/plancia/issues/22), [#27](https://github.com/domenicomassafra/plancia/issues/27), [ControlCenter #919](https://github.com/domenicomassafra/controlcenter/issues/919) e `web/PROVENANCE.md`.

## Esperienza richiesta

Una sola navigazione e identità owner, in browser responsive/PWA. Panoramica, Flotta, Agenti, Account, Workspace e Audit devono appartenere allo stesso prodotto, mantenendo il contesto tra schermate. La preferenza definitiva è **bianco, nero, grigi**, tipografia e gerarchie funzionali sobrie: stato reale, freshness e provenance prima della decorazione. Stati offline, sconosciuto, pending e stale restano distinti.

**Flotta:** cinque identità runtime (Studio, MiniPC, Air, Omarchy, Windows dual boot), diagnostica verificabile, storico 14 giorni e azioni con preview/apply, autorizzazione, receipt, idempotenza e rollback dove supportato. Un host silenzioso non si presenta automaticamente come spento.

**Agenti:** sessioni originali Claude Code, Codex, Pi, OpenCode, Hermes e CoS quando le integrazioni sono provate; client web accessibile anche da mobile, senza simulare l'attach creando harness duplicati. Muxin e Orca restano strumenti donor con confine esplicito; la presenza di una UI chat non certifica tutti gli harness.

**Workspace:** Obsidian sul vault canonico con albero comodo, ricerca, lettura Markdown ricca, wikilink e frontmatter, editing tramite writer canonico con CAS/conflitti. Notion parte dai database Actions: viste e proprietà modificabili via API/Registry esistenti, preimage/preview/apply/readback, nessun database secondario o replica vault. I limiti dei writer reali vanno resi visibili.

**Account e audit:** profili browser per host+browser+account con identificazione certa; operazioni governate e receipt durevoli. Dati personali mai nel bundle frontend.

## Stato e criteri di rilascio

La UI React/Vite è in `plancia/web/`; FastAPI e FleetProbe in `plancia/owner/`. Il servizio sul MiniPC serve frontend e backend dello stesso commit Plancia, nella stessa origine autenticata. Configurazione privata e SQLite esistenti continuano a essere letti dai percorsi dell'host. La pubblicazione mantiene il controllo dei digest, preimage e rollback. L'integrazione storica è nella #20; il cutover backend è nella #919, con procedura in `DESIGN.md`.

Un rilascio si accetta con URL della stessa origine, auth/CSRF funzionanti, navigazione desktop/mobile provata, API senza dati duplicati, prove governate dei writer e rollback della release. Il collaudo dell'editor usa una fixture sintetica con readback; Notion richiede preview e post-fetch senza modifiche accidentali alle Actions personali. Test fisici iPhone e Windows rimangono nella [#25](https://github.com/domenicomassafra/plancia/issues/25); la checklist runtime è controlcenter#484.
