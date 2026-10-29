# UI Plancia

Questa directory è la superficie web React 19/TypeScript/Vite 7 originata da `controlcenter/dashboard/web` e importata nella issue [Plancia #27](https://github.com/domenicomassafra/plancia/issues/27). È la destinazione versionata della UI owner; il backend FastAPI è ora in `../owner/`, trasferito dalla issue [ControlCenter #919](https://github.com/domenicomassafra/controlcenter/issues/919). Vedi `../PRODUCT.md`, `../DESIGN.md`, `PROVENANCE.md`.

Agenti, sviluppo, build e test sul solo Mac Studio, dalla directory `web/`:

```bash
npm ci
npm run build
npm test
```

`npm run dev` avvia una UI di sviluppo e inoltra `/api` alla porta locale 8000 dichiarata in `vite.config.ts`; questo richiede un backend owner già autorizzato, non avvia servizi nuovi. Il pacchetto è `private: true` e non va pubblicato su npm. La build produce `dist/`, escluso da Git. Il MiniPC ospita il runtime installato: il contenuto statico di `dist` va servito dal consumer autenticato esistente. Una pagina Vite standalone non dimostra login o autorizzazione.

Historical ownership: #6 shell/CSS, #12 Notion, #13 Obsidian, #18 PWA, #20 integration/deployment. Current issue authors own scoped delivery through CLOSED under `../AGENTS.md`; coordinate shared-file edits with active writers. Il client in `src/lib/api.ts` conserva i nomi di attributi, endpoint e audit `signal-deck` finché la controparte backend non autorizza e verifica un eventuale cambio di contratto. I PNG locali nominati per macchina non sono inclusi per evitare pubblicazione di asset personali; il renderer deve mostrare i suoi fallback quando l'immagine non è disponibile.
