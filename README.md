# Plancia

Plancia è la console web owner per Panoramica, Flotta, Agenti, Account, Workspace e Audit. La distribuzione corrente contiene la UI React/Vite in [web/](web/README.md) e la relativa documentazione; autenticazione, dati, policy e writer sono forniti dalle API dello stesso origin.

## Sviluppo

Sul Mac Studio, in un worktree dedicato:

```sh
cd web
npm ci
npm run build
npm test
node scripts/check-release.mjs --dist dist --source-sha "$(git rev-parse HEAD)"
```

Gli agenti eseguono build e test tramite il resource guard documentato in [AGENTS.md](AGENTS.md). Il bundle statico da solo non espone le API né autentica l'owner. Per rilascio, pin, verifica della stessa origine e rollback consultare [DESIGN.md](DESIGN.md) e [RELEASING.md](RELEASING.md). Il cutover sul consumer è governato dalla [#20](https://github.com/domenicomassafra/plancia/issues/20).

[PRODUCT.md](PRODUCT.md) descrive capacità e limiti delle integrazioni. La fonte dei componenti importati e le modifiche per la pubblicazione sono documentate in [web/PROVENANCE.md](web/PROVENANCE.md).

## Provenienza del donor ritirato

La copia del client desktop, agent e share-server Muxin di [Lumary Labs LLC](https://github.com/LumaryLabsLLC/muxin) è stata ritirata dai sorgenti distribuiti da Plancia nella [#60](https://github.com/domenicomassafra/plancia/issues/60). Per il software Muxin indipendente usare il repository upstream e i suoi rilasci; Plancia non ne compila né distribuisce il binario.

La sua storia Git, la licenza [Apache-2.0](LICENSE), [NOTICE](NOTICE) e [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) restano disponibili per tracciabilità. La licenza del donor non costituisce licenza automatica della UI di proprietà importata da ControlCenter.
