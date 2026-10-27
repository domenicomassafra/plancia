# Contributing to Plancia

Plancia distribuisce la UI web in `web/`. Leggi [AGENTS.md](AGENTS.md), [PRODUCT.md](PRODUCT.md), [DESIGN.md](DESIGN.md) e [web/PROVENANCE.md](web/PROVENANCE.md) prima di proporre una modifica.

L'API, la gestione delle sessioni, i writer e i dati operativi sono mantenuti nel backend owner; le modifiche al client non devono introdurre credenziali, dati personali o nuove API pubbliche senza il contratto lato server. Per il codice e le patch di Muxin usare il [repository upstream](https://github.com/LumaryLabsLLC/muxin).

Sul Mac Studio, in `web/`, esegui `npm ci`, `npm run build` e `npm test` tramite il launcher di risorse indicato in AGENTS.md. Le PR devono includere il motivo della modifica, il comando e l'esito dei test e il rischio per il consumer autenticato. Il merge e la release seguono la [#20](https://github.com/domenicomassafra/plancia/issues/20).
