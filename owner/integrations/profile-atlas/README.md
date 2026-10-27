# Profile Atlas agent

Agent host-local per i profili Chrome ed Edge usato dalla superficie **Account**
di Signal Deck. L'orchestrazione fleet vive esclusivamente nel backend
`ControlCenter/dashboard`; questo package non espone un secondo server web e non
sceglie host remoti.

## Usage

```bash
# List all profiles
chrome-profiles list

# Filter by name, email, or browser
chrome-profiles list "aiconic"

# Open matching profiles
chrome-profiles open "dodo"

# Check system status
chrome-profiles check
```

## Dashboard integration

La UI canonica vive in `ControlCenter/dashboard`. Questo package resta il
piccolo agent host-local per inventario e apertura esatta dei profili; non
avvia un secondo servizio permanente.

Signal Deck aggrega inventari da MiniPC, Mac Studio, MacBook Air e **MateBook
Omarchy**. Il runtime Windows del MateBook è una identity separata e pending-on-
boot: Profile Atlas non usa Windows come fallback quando Omarchy non risponde.
Il numero locale (`Profile 32`, `Profile 45`, ecc.) è informativo e non diventa
mai identità cross-host.

L'identità account deriva soltanto dai metadati nativi del profilo Chromium
(`Local State` / `Preferences`). Email presenti in password salvate, autofill o
altri database di contenuto non sono identità del browser e non vengono usate
per raggruppare profili o indirizzare azioni.

## Mutazioni governate

Il sottocomando nascosto `agent` è il protocollo JSON usato via SSH da Signal
Deck. L'orchestratore centrale esegue preflight, fingerprint e post-read; l'agent
locale accetta soltanto target browser/email/directory validati e risolve il path
reale sul proprio host. La cancellazione conserva backup limitati di `Local
State`, che non contengono i dati del profilo eliminato.

L'agent deve essere alla stessa versione governata sui runtime gestiti. Non
esistono più i vecchi comandi `chrome-profiles web`, `sync`, `sync-dry-run`,
`sync-to-email` e `sync-to-email-dry-run`: GUI standalone e mutazioni bulk sono
state ritirate per evitare un secondo control plane e operazioni fleet fuori da
Signal Deck.

## Nomenclatura fleet

`Aggiorna nomi su tutti i computer` ricava il tag storico dalla rename map e costruisce sempre il
nome canonico `[TAG] email-completa`, per esempio `[A]
owner@example.invalid`, `[VINTED] market@example.invalid` e `[TEST]
test@example.invalid`. Gli account identificati ma non ancora classificati
ricevono automaticamente il tag `[TEST]`.

La nomenclatura e una **invariante stretta del nome locale governabile**: per
ogni profilo identificato il nome locale e lo shortcut devono essere esattamente
`[TAG] email`, con tag in maiuscolo. Un profilo nuovo o identificato ma non
ancora classificato riceve `[TEST]`; un tag gia valido viene preservato e
normalizzato.

Nel picker nativo di Chrome/Edge, un account autenticato puo comunque essere
mostrato come `Nome provider ([TAG] email)`: Chromium costruisce volutamente
quella stringa quando il nome GAIA/Microsoft differisce dal nome locale.
I campi provider vengono aggiornati dal servizio di identita e non sono una
source of truth locale persistente. Profile Atlas quindi **non li falsifica**:
Signal Deck mostra sempre il nome canonico `[TAG] email`, mentre il picker
nativo resta soggetto alla presentazione del browser.

La convergenza fleet appartiene esclusivamente a Signal Deck. Non esistono
timer, LaunchAgent o daemon che rinominano profili o chiudono browser in
background. Il pulsante **Aggiorna nomi su tutti i computer** esegue un
preflight read-only, calcola un piano con fingerprint, mostra quanti profili e
quali host verranno toccati e applica solo dopo conferma esplicita. Durante
l'apply vengono chiuse soltanto le famiglie Chrome/Edge che hanno drift su
quell'host, vengono creati backup limitati e poi ogni target viene riletto e
verificato. Gli host offline restano dichiarati incompleti e vengono recuperati
al successivo click quando tornano raggiungibili.

La categoria salvata in Signal Deck e la source of truth primaria del tag. Se
un account non ha ancora metadati, un unico tag `[TAG]` gia presente nella
flotta viene adottato; se non esiste alcun tag, il fallback deterministico e
`[TEST]`. Categorie incompatibili non vengono indovinate: il piano si blocca
prima di qualsiasi scrittura.

L'email individua l'account logico; browser + email + directory identificano
l'installazione esatta sul singolo host. Prima di scrivere, Signal Deck esegue
un nuovo inventario e l'agent crea backup limitati dei file Chromium coinvolti.
