# Releasing Plancia web

Plancia produce un bundle React/Vite in `web/dist/`. Non produce DMG, AppImage, pacchetti nativi Muxin, agenti o server di condivisione.

1. Revisionare e integrare la PR Plancia secondo le regole di [AGENTS.md](AGENTS.md), con la suite della workflow `plancia-web.yml` verde.
2. Sul Mac Studio, compilare `web/` con `npm ci` e `npm run build` tramite il resource guard ed eseguire `npm test`.
3. Eseguire `node web/scripts/check-release.mjs --dist web/dist --source-sha <SHA-del-sorgente>` e associare il digest dell'artefatto al commit esatto, come descritto in [DESIGN.md](DESIGN.md).
4. Il reviewer del cutover [#20](https://github.com/domenicomassafra/plancia/issues/20) verifica il pin del frontend in ControlCenter, prepara la release immutabile col backend canonico, esegue autenticazione same-origin, API, CSRF, navigazione e rollback sul consumer raggiungibile.
5. Pubblicare lo SHA integrato e l'esito runtime nella #20. Le prove fisiche restano nella [#25](https://github.com/domenicomassafra/plancia/issues/25).

La distribuzione donor Muxin rimane sul suo [upstream](https://github.com/LumaryLabsLLC/muxin); le attribuzioni storiche restano in `LICENSE`, `NOTICE` e `THIRD_PARTY_NOTICES.md`.
