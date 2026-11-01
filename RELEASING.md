# Releasing Plancia web

Plancia produce un bundle React/Vite in `web/dist/`. Non produce DMG, AppImage, pacchetti nativi Muxin, agenti o server di condivisione.

1. Revisionare e integrare la PR Plancia secondo le regole di [AGENTS.md](AGENTS.md), con la suite della workflow `plancia-web.yml` verde.
2. Sul Mac Studio, compilare `web/` con `npm ci` e `npm run build` tramite il resource guard ed eseguire `npm test`.
3. Eseguire `node web/scripts/check-release.mjs --dist web/dist --source-sha <SHA-del-sorgente>` e associare il digest dell'artefatto al commit esatto, come descritto in [DESIGN.md](DESIGN.md).
4. L'autore dell'issue corrente verifica il pin del frontend, prepara la release immutabile col backend canonico, esegue autenticazione same-origin, API, CSRF, navigazione e rollback sul consumer raggiungibile, secondo il contratto condiviso v2 richiamato in [AGENTS.md](AGENTS.md).
5. Pubblicare lo SHA integrato e l'esito runtime nell'issue corrente. Il cutover storico è documentato nella [#20](https://github.com/domenicomassafra/plancia/issues/20); le prove fisiche restano nella [#25](https://github.com/domenicomassafra/plancia/issues/25).

La distribuzione donor Muxin rimane sul suo [upstream](https://github.com/LumaryLabsLLC/muxin); le attribuzioni storiche restano in `LICENSE`, `NOTICE` e `THIRD_PARTY_NOTICES.md`.
