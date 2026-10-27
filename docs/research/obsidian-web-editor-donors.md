# Donor per l'editor web/PWA del vault Obsidian — plancia#1

Ricerca del 2026-10-21 per la decisione [controlcenter#216](https://github.com/domenicomassafra/controlcenter/issues/216)
e il parent [controlcenter-ops#184](https://github.com/domenicomassafra/controlcenter-ops/issues/184):
il prodotto finale è unico (fork Muxin, Apache-2.0), la console web/PWA è possibile, il Workspace
Obsidian deve lavorare sul **vault canonico** e Notion resta database-first. Questo ticket non
importa né scrive nel vault: produce solo la matrice dei donor, le licenze e la scelta motivata.

## Criterio

Un donor va sul vault canonico se legge e scrive **gli stessi file `.md`** della cartella Obsidian
(wikilink, embed, frontmatter), senza database o storage parallelo. Contano poi: maturità e
manutenzione, licenza compatibile con un fork Apache-2.0 distribuito, e una forma di integrazione
realistica con Muxin (sidecar Rust/HTTP sull'host, client PWA proxato, crate Rust, componente web).
Un donor può essere ottimo e comunque non passare: in quel caso resta nella tabella col motivo.

## Metodo

Dati letti dall'API GitHub il **2026-10-21** con `gh api` (stelle, ultimo push, default branch,
release più recente, licenza SPDX, `archived`) e commit pinnato = testa del default branch in quel
momento. Candidati trovati con ricerche GitHub su `obsidian pwa`, `markdown wiki flat file`,
`obsidian vault web editor`, più i donor già noti a Muxin/ControlCenter. La licenza è stata
verificata sul file `LICENSE*` quando GitHub risponde `NOASSERTION`. La tabella è generata dal
JSON: la fonte di verità è `obsidian-web-editor-donors.json`, non questo testo.

<!-- donor-table:begin -->
| Donor | Ruolo | Licenza | ★ | Ultimo push | Release | Commit | Vault .md | Web/PWA | Integrazione Muxin | Esito |
| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| [silverbulletmd/silverbullet](https://github.com/silverbulletmd/silverbullet) | editor vault web | MIT | 6364 | 2026-10-21 | 2.12.1 (2026-10-12) | `b77f66eb6107` | read-write | sì | sidecar sull'host + client PWA proxato | **adopt** · Space = cartella di .md, server Rust, PWA, query |
| [makenotion/notion-sdk-js](https://github.com/makenotion/notion-sdk-js) | client Notion | MIT | 5679 | 2026-10-16 | v5.27.1 (2026-10-16) | `0ffdbba3004b` | n/a | n/a | API ufficiale per i database Actions | **component** · Notion database-first via API ufficiale |
| [zoni/obsidian-export](https://github.com/zoni/obsidian-export) | parser Obsidian Rust | BSD-2-Clause | 1340 | 2026-10-20 | v25.3.0 (2025-03-25) | `87cb4ceb0ae4` | read-only | n/a | crate Rust: wikilink, embed, frontmatter | **component** · parsing fedele del Markdown Obsidian lato server |
| [Milkdown/milkdown](https://github.com/Milkdown/milkdown) | componente editor | MIT | 12002 | 2026-10-20 | v7.22.3 (2026-10-10) | `d1a7af63637e` | n/a | n/a | componente TS/ProseMirror nel client PWA | **alternate** · editor WYSIWYG se il client va riscritto |
| [NotionX/react-notion-x](https://github.com/NotionX/react-notion-x) | renderer Notion | MIT | 5437 | 2026-10-18 | v8.0.8 (2026-09-03) | `dd4c891997f6` | n/a | n/a | render React delle pagine Notion | **alternate** · opzionale, solo se serve leggere pagine Notion |
| [mdx-editor/editor](https://github.com/mdx-editor/editor) | componente editor | MIT | 3699 | 2026-10-13 | v4.3.3 (2026-10-13) | `4d9499a86cb7` | n/a | n/a | componente React markdown | **alternate** · alternativa React se serve un editor proprio |
| [dullage/flatnotes](https://github.com/dullage/flatnotes) | editor vault web | MIT | 3262 | 2026-08-29 | v5.5.5 (2026-08-29) | `7f5b773c9cb3` | read-write | parziale | sidecar Python + web, nessun wikilink | **alternate** · solo cartella piatta .md, niente link/viste |
| [jackyzha0/quartz](https://github.com/jackyzha0/quartz) | viste statiche | MIT | 13431 | 2026-10-04 | v4.0.8 (2023-08-21) | `97a2d05f80c4` | read-only | no | build statica dal vault, nessuna scrittura | **read-only** · pubblica il vault ma non lo edita |
| [foambubble/foam](https://github.com/foambubble/foam) | logica wikilink | MIT | 17462 | 2026-10-21 | vscode@0.46.0 (2026-09-30) | `5d3adbe1d7cc` | read-write | no | estensione VS Code, non web | **reference** · grafo/link utili come riferimento, non PWA |
| [Smaug6739/Alexandrie](https://github.com/Smaug6739/Alexandrie) | editor vault web | MIT | 3301 | 2026-10-18 | v8.14.2 (2026-09-07) | `32a8c76da138` | no | sì | riferimento UI/PWA, import/export ZIP | **reference** · documenti in DB + S3, non sul vault |
| [coder/code-server](https://github.com/coder/code-server) | editor vault web | MIT | 79662 | 2026-10-20 | v4.141.0 (2026-10-08) | `f07405cba331` | read-write | no | IDE completo in browser, pesante su iPhone | **reject** · MIT ma IDE, non esperienza note/PWA |
| [toeverything/AFFiNE](https://github.com/toeverything/AFFiNE) | database Notion-like | MIT + directory escluse | 73728 | 2026-10-12 | v0.27.4 (2026-08-18) | `0b69a6da21e8` | no | no | workspace DB, nessun file .md | **reject** · licenza mista e storage proprietario |
| [nocodb/nocodb](https://github.com/nocodb/nocodb) | database Notion-like | Sustainable Use License | 65316 | 2026-10-21 | 2026.09.1 (2026-09-29) | `55bc42cb80e8` | no | no | alternativa self-hosted, licenza restrittiva | **reject** · non OSI, uso commerciale limitato |
| [laurent22/joplin](https://github.com/laurent22/joplin) | editor vault web | AGPL-3.0 | 56822 | 2026-10-21 | v3.7.21 (2026-09-25) | `585ec251b056` | no | no | solo sync/export, SQLite | **reject** · AGPL-3.0 e DB SQLite |
| [siyuan-note/siyuan](https://github.com/siyuan-note/siyuan) | editor vault web | AGPL-3.0 | 46873 | 2026-10-21 | v3.8.8 (2026-10-20) | `bfe36100477b` | no | no | kernel Go, blocchi .sy | **reject** · AGPL-3.0 e formato .sy non .md |
| [logseq/logseq](https://github.com/logseq/logseq) | editor vault web | AGPL-3.0 | 45324 | 2026-10-21 | 2.0.2 (2026-10-07) | `05c3ae73e084` | parziale | no | graph proprio (pages/journals), non il vault | **reject** · AGPL-3.0 e modello DB graph |
| [TriliumNext/Trilium](https://github.com/TriliumNext/Trilium) | editor vault web | AGPL-3.0 | 38433 | 2026-10-21 | v0.106.0 (2026-09-25) | `a7cae93a63ad` | no | sì | solo import, SQLite proprietario | **reject** · AGPL-3.0 e DB interno |
| [TypeCellOS/BlockNote](https://github.com/TypeCellOS/BlockNote) | componente editor | MPL-2.0 | 10318 | 2026-10-21 | v0.56.0 (2026-10-14) | `3217d7ccdf73` | n/a | n/a | blocchi Notion-like, export markdown | **reject** · MPL-2.0 e modello a blocchi, non Markdown-first |
| [dendronhq/dendron](https://github.com/dendronhq/dendron) | editor vault web | Apache-2.0 | 7468 | 2025-11-13 | — | `4420715a4217` | read-write | no | estensione VS Code, gerarchia propria | **reject** · fermo da 11 mesi, non web |
<!-- donor-table:end -->

## Scelta motivata

**SilverBullet (MIT) è il donor scelto**, non un fork né un reimplemento: il suo modello di dati è
già quello che serve. Uno *Space* è una cartella di pagine Markdown, il server è un workspace Cargo
Rust che si avvia con `silverbullet <PATH-TO-YOUR-DATA-FOLDER>`, il client è una PWA con editor
live-preview, wikilink, backlink, query e viste, e la programmazione è Lua lato space. Questo
copre i tre requisiti del ticket (editing Markdown, link, viste) e la compatibilità col vault senza
conversioni: la cartella canonica è la cartella servita.

**`zoni/obsidian-export` (BSD-2-Clause) è il solo componente Rust da aggiungere**, e non per
l'editor: serve a parsare in modo fedele wikilink, embed e frontmatter lato server, così Muxin può
indicizzare e validare il vault senza riscrivere un parser Obsidian proprio. È una libreria, non
un'applicazione: nessun runtime da ospitare.

**Notion resta database-first via API**: `makenotion/notion-sdk-js` (MIT) per i database Actions;
`NotionX/react-notion-x` (MIT) solo se in futuro servirà rendere pagine Notion dentro la PWA. Non
si copiano database Notion in storage locale.

Gli editor React (`Milkdown`, `mdx-editor/editor`, entrambi MIT) restano alternative solo se il
client SilverBullet venisse sostituito: non sono sul percorso critico e adottarli ora sarebbe
lavoro in più, non riuso.

## Colla minima prevista (motivata)

Il fork non incorpora SilverBullet come crate: la licenza lo permetterebbe, ma l'integrazione
naturale è un **sidecar** avviato dall'agent sull'host che possiede il vault, con la cartella
canonica come space e il client PWA raggiunto dal reverse proxy già previsto per Signal Deck. La
colla è quindi configurazione e supervisione del processo (host, path, porta, auth), non codice
nuovo: nessuna seconda API, nessun secondo modello di dati, nessuna copia del vault. L'eventuale
patch a SilverBullet va autorizzata e isolata, come da contratto sui prodotti di terzi.

## Licenze da segnalare prima dell'adozione

- **AGPL-3.0** — `logseq/logseq`, `TriliumNext/Trilium`, `siyuan-note/siyuan`, `laurent22/joplin`:
  esclusi dal prodotto distribuito Apache-2.0; l'obbligo di sorgente su rete renderebbe il fork
  un'opera diversa. Nessuno di loro è comunque sul vault come file `.md`.
- **MPL-2.0** — `TypeCellOS/BlockNote`: copyleft debole per file, adottabile solo senza modificare
  i file MPL e con notice; scartato perché il modello a blocchi non è Markdown-first.
- **Sustainable Use License** — `nocodb/nocodb`: non è open source (limiti d'uso commerciale),
  escluso a prescindere.
- **Licenza mista** — `toeverything/AFFiNE`: MIT fuori da `packages/backend` e
  `packages/common/native`, che hanno termini propri; escluso.
- **BSD-2-Clause** — `zoni/obsidian-export`: permissiva, compatibile, richiede solo di conservare
  copyright e disclaimer in `THIRD_PARTY_NOTICES.md` al momento dell'adozione.

## Ostacoli reali

Il vincolo vero non è la licenza ma il **possesso del vault**: solo l'host che monta la cartella
canonica può servire SilverBullet, quindi il sidecar deve girare lì (o su un host con quel mount) e
non sul MiniPC per comodità. SilverBullet non ha multi-utente con permessi fini: l'autenticazione
va tenuta davanti al proxy, e la prima integrazione va fatta in sola lettura per non rischiare
scritture con concurrency Obsidian. `quartz` pubblica il vault ma non lo edita; `flatnotes` legge la
cartella ma non ha wikilink né viste; `code-server` scrive la cartella ma è un IDE, inadatto a
iPhone; `Alexandrie`, `Trilium`, `SiYuan`, `Joplin` tengono i documenti in DB propri e al massimo
importano Markdown. `dendron` è fermo da novembre 2025. `BlockNote`, `NocoDB`, `AFFiNE` cadono sulle
licenze. Il pin di ogni commit è la testa del branch al 2026-10-21: invecchia, e va rinfrescato con
`scan`, non citato come eterno.

## Riproduzione

```sh
scripts/donors/vault-editor-donors.sh render       # tabella dal JSON
scripts/donors/vault-editor-donors.sh check        # la tabella nel doc è aggiornata
scripts/donors/vault-editor-donors.sh verify-live  # i commit pinnati esistono ancora upstream
scripts/donors/vault-editor-donors.sh scan         # rilegge l'API GitHub e riscrive tabella+pin
```

`check` è offline e deterministico e va eseguito per verificare la coerenza tra tabella e JSON.
Il vecchio test Rust donor è stato ritirato con la distribuzione nativa (#60). `scan` e `verify-live` richiedono `gh` autenticato.
