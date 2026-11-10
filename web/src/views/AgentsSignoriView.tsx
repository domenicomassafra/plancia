import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import {
  AdapterError,
  adapterMessage,
  attachHermesSession,
  getHermesMessages,
  getHermesProfiles,
  getHermesSessions,
  sendHermesPrompt,
  stageHermesFile,
  type HermesLease,
  type HermesMessage,
  type HermesPhoto,
  type HermesProfile,
  type HermesSession,
} from "../lib/agents-remoting";
import { request } from "../lib/api";
import { canUseHermesInput, createHermesAttempt, sameHermesLease, type HermesAttempt } from "../lib/hermes-input-flow";

type Load<T> = { state: "loading" } | { state: "error"; message: string }
  | { state: "ready"; data: T };

function StateNotice({ message }: { message: string }) {
  return <p role="status" className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-5 text-sm text-ink-300">{message}</p>;
}

function hermesWriteMessage(error: unknown): string {
  if (error instanceof AdapterError) return adapterMessage(error);
  if (error instanceof TypeError) return "Servizio Hermes non raggiungibile.";
  if (error instanceof Error && error.message.trim()) return error.message.slice(0, 240);
  return "Errore durante la comunicazione con Hermes.";
}

type HermesAttachment = { file: File; kind: "photo" | "file" | "voice" };
const MEDIA_LIMIT = 4 * 1024 * 1024;
const PHOTO_MIMES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

function readHermesAttachment(file: File): Promise<string> {
  if (!file.size || file.size > MEDIA_LIMIT) throw new Error("Allegato vuoto o superiore a 4 MiB.");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("Lettura allegato fallita."));
    reader.onerror = () => reject(new Error("Lettura allegato fallita."));
    reader.readAsDataURL(file);
  });
}

/** Only mounted on a server-granted pre-existing session. Parent retains uncertain nonce across a list refresh. */
function HermesComposer({ profile, session, pending, onPendingChange, onReadback }: {
  profile: HermesProfile; session: HermesSession; pending: HermesAttempt | null;
  onPendingChange: (attempt: HermesAttempt | null) => void; onReadback: () => void;
}) {
  const [lease, setLease] = useState<HermesLease | null>(null);
  const [draft, setDraft] = useState("");
  const [attachment, setAttachment] = useState<HermesAttachment | null>(null);
  const [recording, setRecording] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("Connetti al gateway nativo per scrivere nella sessione esistente.");
  const enabled = canUseHermesInput(profile, session);

  useEffect(() => () => {
    const active = recorder.current;
    if (active && active.state !== "inactive") active.stop();
    active?.stream.getTracks().forEach((track) => track.stop());
  }, []);

  function selectAttachment(event: ChangeEvent<HTMLInputElement>, kind: HermesAttachment["kind"]) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    if (!file.size || file.size > MEDIA_LIMIT) {
      setFeedback("Allegato vuoto o superiore a 4 MiB.");
      return;
    }
    if (kind === "photo" && !PHOTO_MIMES.has(file.type)) {
      setFeedback("Foto non supportata: usa PNG, JPEG, GIF o WebP.");
      return;
    }
    if (kind === "voice" && !file.type.startsWith("audio/")) {
      setFeedback("Seleziona una registrazione audio.");
      return;
    }
    setAttachment({ file, kind });
    setFeedback(`Allegato pronto: ${file.name} (${Math.ceil(file.size / 1024)} KiB).`);
  }

  async function beginRecording() {
    if (!lease || busy || pending || recording) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setFeedback("Registrazione non supportata: scegli una nota vocale dal telefono.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"]
        .find((candidate) => MediaRecorder.isTypeSupported(candidate));
      const media = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks: BlobPart[] = [];
      media.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      media.onstop = () => {
        const type = media.mimeType.split(";", 1)[0] || "audio/webm";
        const suffix = type === "audio/mp4" ? "m4a" : "webm";
        const file = new File(chunks, `nota-vocale.${suffix}`, { type });
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        if (file.size && file.size <= MEDIA_LIMIT) {
          setAttachment({ file, kind: "voice" });
          setFeedback("Nota vocale pronta. Premi «Invia a Hermes» per consegnarla.");
        } else setFeedback("Nota vocale vuota o superiore a 4 MiB; registra un messaggio più breve.");
      };
      media.onerror = () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        setFeedback("Registrazione non riuscita.");
      };
      recorder.current = media;
      media.start();
      setRecording(true);
      setFeedback("Registrazione in corso; premi «Termina registrazione» quando hai finito.");
    } catch {
      setFeedback("Microfono non disponibile. Controlla il permesso del browser o scegli un file audio.");
    }
  }

  function stopRecording() {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }

  async function connect() {
    if (!enabled || busy) return;
    setBusy(true);
    try {
      const next = await attachHermesSession(profile, session, request);
      setLease(next);
      setFeedback(pending && !sameHermesLease(pending.lease, next)
        ? "Il gateway è cambiato. Il precedente invio ha esito incerto: controlla il readback prima di ogni nuovo invio."
        : "Connessione alla sessione nativa verificata. Nessuna nuova sessione creata.");
      onReadback();
    } catch (error: unknown) {
      setLease(null);
      setFeedback(`Impossibile connettere: ${hermesWriteMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!enabled || !lease || busy || recording) return;
    if (pending && !sameHermesLease(pending.lease, lease)) {
      setFeedback("Gateway o sessione cambiati: riconcilia l'invio incerto dal readback prima di proseguire.");
      return;
    }
    setBusy(true);
    let admissionStarted = false;
    try {
      let attempt: HermesAttempt;
      if (pending) attempt = pending;
      else {
        let content = draft.trim();
        let photo: HermesPhoto | undefined;
        if (attachment) {
          const dataUrl = await readHermesAttachment(attachment.file);
          if (attachment.kind === "photo") {
            photo = { mime: attachment.file.type as HermesPhoto["mime"], data_url: dataUrl };
            content ||= "Descrivi la fotografia allegata.";
          } else {
            const reference = await stageHermesFile(lease, profile.capabilities,
              attachment.file.name, attachment.kind, dataUrl, request);
            const preface = attachment.kind === "voice"
              ? "Questa è una nota vocale. Trascrivi l'audio e rispondi al contenuto:"
              : "Leggi il file allegato e rispondi:";
            content = [content || preface, reference].join("\n\n");
          }
        }
        attempt = createHermesAttempt(lease, content, undefined, photo);
      }
      onPendingChange(attempt);
      admissionStarted = true;
      const receipt = await sendHermesPrompt(attempt.lease, profile.capabilities,
        attempt.nonce, attempt.text, request, attempt.photo);
      if (receipt.status === "unknown") {
        setFeedback("Esito incerto: il gateway non ha confermato il turno. Rileggi la sessione; «Riprova stesso invio» conserva il nonce originale.");
      } else {
        onPendingChange(null);
        setDraft("");
        setAttachment(null);
        setFeedback(`Il gateway nativo ha registrato l'invio (${receipt.status}). Aggiorna il readback per controllarne l'esito.`);
      }
      onReadback();
    } catch (error: unknown) {
      // An admission may have succeeded even when HTTP failed: keep its exact nonce.
      // A stage failure before admission has no nonce to replay.
      setFeedback(admissionStarted
        ? `Invio da verificare: ${hermesWriteMessage(error)}. Rileggi la sessione prima di riprovare; «Riprova stesso invio» conserva il nonce.`
        : `Allegato o messaggio non inviato: ${hermesWriteMessage(error)}. Riprova dopo aver verificato la sessione.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-3" aria-label="Invio nella sessione Hermes esistente">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void connect()} disabled={busy || !enabled}
          className="button-secondary min-h-11">{lease ? "Riconnetti alla stessa sessione" : "Connetti alla sessione"}</button>
        <button type="button" onClick={onReadback} className="button-secondary min-h-11">Rileggi cronologia</button>
        <span className="text-xs text-ink-400">{lease ? `Gateway ${lease.instanceId} · epoch ${lease.authorityEpoch}` : "Nessun attach interattivo"}</span>
      </div>
      <label htmlFor="hermes-composer" className="sr-only">Messaggio Hermes</label>
      <textarea id="hermes-composer" rows={2} maxLength={4096} value={pending?.text ?? draft}
        onChange={(event) => setDraft(event.target.value)} readOnly={Boolean(pending)}
        disabled={!enabled || !lease || busy} placeholder="Scrivi nella sessione nativa esistente"
        className="w-full resize-none rounded-xl border border-ink-700 bg-ink-950 p-3 text-sm text-ink-100 disabled:cursor-not-allowed disabled:text-ink-400" />
      <div className="flex flex-wrap items-center gap-3 text-xs text-ink-300">
        <label className="button-secondary min-h-11 cursor-pointer px-3 py-2">
          Foto <input className="sr-only" aria-label="Allega foto Hermes" type="file" accept="image/png,image/jpeg,image/gif,image/webp"
            disabled={!lease || busy || Boolean(pending) || recording} onChange={(event) => selectAttachment(event, "photo")} />
        </label>
        <label className="button-secondary min-h-11 cursor-pointer px-3 py-2">
          File <input className="sr-only" aria-label="Allega file Hermes" type="file"
            disabled={!lease || busy || Boolean(pending) || recording} onChange={(event) => selectAttachment(event, "file")} />
        </label>
        <label className="button-secondary min-h-11 cursor-pointer px-3 py-2">
          Nota vocale <input className="sr-only" aria-label="Carica nota vocale Hermes" type="file" accept="audio/*"
            disabled={!lease || busy || Boolean(pending) || recording} onChange={(event) => selectAttachment(event, "voice")} />
        </label>
        <button type="button" className="button-secondary min-h-11" disabled={!lease || busy || Boolean(pending)}
          onClick={() => recording ? stopRecording() : void beginRecording()}>
          {recording ? "Termina registrazione" : "Registra voce"}
        </button>
        {attachment && <span className="break-all">{attachment.kind === "voice" ? "Audio" : attachment.kind === "photo" ? "Foto" : "File"}: {attachment.file.name}
          <button type="button" className="ml-2 underline" onClick={() => setAttachment(null)} disabled={busy || Boolean(pending)}>Rimuovi</button>
        </span>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p role="status" className="text-xs leading-5 text-ink-400">{feedback}</p>
        <button type="submit" disabled={!enabled || !lease || busy || recording || (!pending && !draft.trim() && !attachment)}
          className="button-secondary min-h-11">{pending ? "Riprova stesso invio" : "Invia a Hermes"}</button>
      </div>
    </form>
  );
}

export function AgentsSignoriView() {
  const [refresh, setRefresh] = useState(0);
  const [inventory, setInventory] = useState<Load<HermesProfile[]>>({ state: "loading" });
  const [profileId, setProfileId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Load<HermesSession[]>>({ state: "loading" });
  const [messages, setMessages] = useState<Load<HermesMessage[]>>({ state: "loading" });
  const [readbackRefresh, setReadbackRefresh] = useState(0);
  const [attempts, setAttempts] = useState<Record<string, HermesAttempt>>({});

  useEffect(() => {
    const controller = new AbortController();
    getHermesProfiles(fetch, controller.signal)
      .then(({ profiles }) => {
        if (!controller.signal.aborted) setInventory({ state: "ready", data: profiles });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setInventory({ state: "error", message: adapterMessage(error) });
      });
    return () => controller.abort();
  }, [refresh]);

  const selectedProfile = inventory.state === "ready"
    ? inventory.data.find((profile) => profile.id === profileId) : undefined;
  const canRead = selectedProfile?.capabilities.readable === true;

  useEffect(() => {
    if (!profileId || !canRead) return;
    const controller = new AbortController();
    getHermesSessions(profileId, fetch, controller.signal)
      .then(({ sessions: data }) => {
        if (!controller.signal.aborted) setSessions({ state: "ready", data });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setSessions({ state: "error", message: adapterMessage(error) });
      });
    return () => controller.abort();
  }, [profileId, canRead, refresh]);

  useEffect(() => {
    if (!profileId || !sessionId || !canRead || sessions.state !== "ready"
      || !sessions.data.some((item) => item.id === sessionId)) return;
    const controller = new AbortController();
    getHermesMessages(profileId, sessionId, fetch, controller.signal)
      .then(({ messages: data }) => {
        if (!controller.signal.aborted) setMessages({ state: "ready", data });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setMessages({ state: "error", message: adapterMessage(error) });
      });
    return () => controller.abort();
  }, [profileId, sessionId, canRead, sessions, refresh, readbackRefresh]);

  const selectProfile = (id: string) => {
    setProfileId(id);
    setSessionId(null);
    setSessions({ state: "loading" });
    setMessages({ state: "loading" });
  };
  const selectSession = (id: string) => {
    setSessionId(id);
    setMessages({ state: "loading" });
  };
  const reload = () => {
    setInventory({ state: "loading" });
    setSessions({ state: "loading" });
    setMessages({ state: "loading" });
    setRefresh((previous) => previous + 1);
  };
  const selectedSession = sessions.state === "ready"
    ? sessions.data.find((session) => session.id === sessionId) : undefined;
  const selectedKey = selectedProfile && selectedSession ? `${selectedProfile.id}/${selectedSession.id}` : "";
  const rememberAttempt = (attempt: HermesAttempt | null) => {
    if (!selectedKey) return;
    setAttempts((previous) => {
      const next = { ...previous };
      if (attempt) next[selectedKey] = attempt;
      else delete next[selectedKey];
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Origine nativa · Hermes</p>
          <h2 className="mt-1 text-xl font-semibold text-ink-100">Conversazioni dei Signori</h2>
          <p className="mt-2 text-sm text-ink-400">Sessioni originali sul MiniPC, cronologia redatta e capacità dichiarate dal server.</p>
        </div>
        <button type="button" onClick={reload} className="button-secondary min-h-11 shrink-0">Aggiorna</button>
      </header>
      {inventory.state === "loading" && <StateNotice message="Verifica dell'adapter Hermes in corso…" />}
      {inventory.state === "error" && <StateNotice message={`Hermes non disponibile: ${inventory.message}`} />}
      {inventory.state === "ready" && inventory.data.length === 0 &&
        <StateNotice message="Nessun profilo Hermes autorizzato restituito dal server." />}

      {inventory.state === "ready" && inventory.data.length > 0 && (
        <div className="grid min-h-[560px] min-w-0 overflow-hidden rounded-2xl border border-ink-700 bg-ink-900 lg:grid-cols-[minmax(220px,300px)_minmax(0,1fr)]">
          <aside className="min-w-0 border-b border-ink-700 lg:border-r lg:border-b-0" aria-label="Profili e sessioni Hermes">
            <h3 className="border-b border-ink-700 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-ink-400">Profili nativi</h3>
            <div className="max-h-64 divide-y divide-ink-700 overflow-y-auto">
              {inventory.data.map((profile) => (
                <button key={profile.id} type="button" onClick={() => selectProfile(profile.id)}
                  aria-pressed={profileId === profile.id}
                  className={`flex min-h-14 w-full items-center justify-between gap-2 px-4 py-3 text-left hover:bg-ink-850 ${profileId === profile.id ? "bg-ink-800" : ""}`}>
                  <span className="min-w-0"><span className="block truncate text-sm font-medium text-ink-100">{profile.name}</span>
                    <span className="block truncate text-xs text-ink-400">{profile.id} · {profile.host}</span></span>
                  <span className={`h-2 w-2 shrink-0 rounded-full ${profile.gateway === "running" ? "bg-ink-200" : "bg-ink-600"}`} aria-label={`Gateway ${profile.gateway}`} />
                </button>
              ))}
            </div>
            <h3 className="border-y border-ink-700 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-ink-400">Sessioni esistenti</h3>
            <div className="max-h-80 space-y-1 overflow-auto p-3">
              {!selectedProfile && <p className="text-sm text-ink-400">Seleziona un Signore.</p>}
              {selectedProfile && !canRead && <p role="status" className="text-sm text-ink-400">Lettura non autorizzata.</p>}
              {selectedProfile && canRead && sessions.state === "loading" && <p role="status" className="text-sm text-ink-400">Caricamento sessioni…</p>}
              {selectedProfile && canRead && sessions.state === "error" && <p role="status" className="text-sm text-ink-400">{sessions.message}</p>}
              {selectedProfile && canRead && sessions.state === "ready" && sessions.data.length === 0 && <p className="text-sm text-ink-400">Nessuna sessione restituita.</p>}
              {selectedProfile && canRead && sessions.state === "ready" && sessions.data.map((session) => (
                <button key={session.id} type="button" onClick={() => selectSession(session.id)} aria-pressed={sessionId === session.id}
                  className={`min-h-11 w-full rounded-lg px-3 py-2 text-left hover:bg-ink-850 ${sessionId === session.id ? "bg-ink-800 ring-1 ring-ink-600" : ""}`}>
                  <span className="block truncate text-sm font-medium text-ink-100">{session.title || session.id}</span>
                  <span className="block truncate text-xs text-ink-400">{session.updated_at || session.id}</span>
                </button>
              ))}
            </div>
          </aside>
          <section className="flex min-h-[520px] min-w-0 flex-col" aria-label="Conversazione Hermes">
            <header className="border-b border-ink-700 px-5 py-4">
              <h3 className="font-semibold text-ink-100">{selectedProfile?.name || "Scegli un Signore"}</h3>
              <p className="mt-1 break-all text-xs text-ink-400">
                {sessionId ? `Sessione nativa ${sessionId} · lettura redatta` : "Cronologia e permessi del profilo originale"}
              </p>
            </header>
            <div role="log" aria-label="Messaggi redatti" className="max-h-[560px] min-h-72 flex-1 space-y-4 overflow-auto p-5">
              {!sessionId && <div className="grid min-h-64 place-content-center text-center">
                <p className="font-medium text-ink-100">Le conversazioni restano in Hermes</p>
                <p className="mt-2 text-sm text-ink-400">Seleziona un profilo e una sessione esistente per leggerla qui.</p>
              </div>}
              {sessionId && messages.state === "loading" && <p role="status" className="text-sm text-ink-400">Lettura dei messaggi…</p>}
              {sessionId && messages.state === "error" && <p role="status" className="text-sm text-ink-400">{messages.message}</p>}
              {sessionId && messages.state === "ready" && messages.data.length === 0 && <p className="text-sm text-ink-400">Nessun messaggio esportato.</p>}
              {sessionId && messages.state === "ready" && <ol className="space-y-4">
                {messages.data.map((message, index) => (
                  <li key={`${sessionId}-${index}`} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                    <article className={`max-w-[90%] min-w-0 rounded-2xl border p-4 sm:max-w-[80%] ${message.role === "user" ? "border-ink-500 bg-ink-800" : "border-ink-700 bg-ink-850"}`}>
                      <p className="text-xs font-medium text-ink-400">{message.role === "assistant" ? selectedProfile?.name || "Hermes" : message.role === "user" ? "Tu" : message.role === "tool" ? "Tool" : "Sistema"}{message.timestamp ? ` · ${message.timestamp}` : ""}</p>
                      <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-sm leading-6 text-ink-100">{message.text}</pre>
                    </article>
                  </li>
                ))}
              </ol>}
            </div>
            <footer className="border-t border-ink-700 p-4">
              {selectedProfile && selectedSession && canUseHermesInput(selectedProfile, selectedSession)
                ? <HermesComposer key={`${selectedProfile.id}/${selectedSession.id}`} profile={selectedProfile}
                    session={selectedSession} pending={attempts[selectedKey] ?? null}
                    onPendingChange={rememberAttempt} onReadback={() => setReadbackRefresh((value) => value + 1)} />
                : <>
                    <label htmlFor="hermes-composer" className="sr-only">Messaggio Hermes</label>
                    <textarea id="hermes-composer" disabled rows={2}
                      placeholder="Invio non disponibile: il backend non ha ancora autorizzato attach e send nativi"
                      className="w-full resize-none rounded-xl border border-ink-700 bg-ink-950 p-3 text-sm text-ink-400 disabled:cursor-not-allowed" />
                  </>}
              <p className="mt-2 text-xs leading-5 text-ink-400">Cronologia redatta da Hermes. Foto, file e note vocali entrano nella sessione originale attraverso il gateway nativo; limite 4 MiB per allegato.</p>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
