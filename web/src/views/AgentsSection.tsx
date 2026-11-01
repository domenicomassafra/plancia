import { useState } from "react";
import { AgentsSignoriView } from "./AgentsSignoriView";
import { HermesDashboardView } from "./HermesDashboardView";
import { RemoteDesktopView } from "./RemoteDesktopView";
import { OrcaChatPanel } from "./OrcaChatPanel";

type AgentsSubView = "hermes-app" | "sessions" | "orca" | "desktop";

/** Authorized native agent surfaces under Plancia's existing navigation. */
export function AgentsSection() {
  const [subView, setSubView] = useState<AgentsSubView>("hermes-app");
  const [visited, setVisited] = useState<Set<AgentsSubView>>(() => new Set(["hermes-app"]));
  const selectSubView = (next: AgentsSubView) => {
    setVisited((current) => new Set(current).add(next));
    setSubView(next);
  };
  return (
    <div className="min-w-0 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-ink-100">Agenti</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-300">
          Conversazioni native e cronologia nello stesso spazio, secondo i permessi verificati
          dal backend owner. Le sessioni restano nei rispettivi runtime.
        </p>
      </div>
      <div role="group" aria-label="Sezioni Agenti" className="flex flex-wrap gap-2">
        {([
          { id: "hermes-app", label: "App Hermes" },
          { id: "sessions", label: "Cronologia redatta" },
          { id: "orca", label: "Pi · Orca" },
          { id: "desktop", label: "Desktop" },
        ] as const).map((item) => (
          <button key={item.id} type="button" aria-pressed={subView === item.id}
            aria-controls="agents-content" onClick={() => selectSubView(item.id)}
            className={`button-secondary min-h-11 text-sm ${subView === item.id ? "border-ink-300 bg-ink-800 text-ink-100" : ""}`}>
            {item.label}
          </button>
        ))}
      </div>
      <div id="agents-content" role="region" aria-label={subView === "hermes-app" ? "App Hermes" : subView === "sessions" ? "Cronologia Hermes" : subView === "orca" ? "Chat Orca" : "Desktop Agenti"}>
        {subView === "hermes-app" && <HermesDashboardView />}
        <section hidden={subView !== "sessions"} aria-label="Sessioni Hermes">
          {visited.has("sessions") && <AgentsSignoriView />}
        </section>
        <section hidden={subView !== "orca"} aria-label="Cronologia Pi e Orca">
          {visited.has("orca") && <OrcaChatPanel active={subView === "orca"} />}
        </section>
        <section hidden={subView !== "desktop"} aria-label="Desktop autorizzati">
          {visited.has("desktop") && <RemoteDesktopView active={subView === "desktop"} />}
        </section>
      </div>
    </div>
  );
}
