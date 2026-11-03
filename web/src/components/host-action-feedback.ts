import type { Receipt } from "../lib/types";

export type HostActionFeedback = { kind: "ok" | "warn" | "error"; text: string };

/** Only the server's succeeded receipt proves that a host action completed. */
export function hostActionFeedback(receipt: Pick<Receipt, "id" | "outcome">, actionLabel: string): HostActionFeedback {
  const reference = `Ricevuta ${receipt.id} in Audit.`;
  switch (receipt.outcome) {
    case "succeeded":
      return { kind: "ok", text: `${actionLabel} completata. ${reference}` };
    case "failed":
      return { kind: "error", text: `${actionLabel} fallita. ${reference}` };
    case "interrupted":
      return { kind: "warn", text: `${actionLabel} interrotta; completamento non verificato. ${reference}` };
    case "dispatched":
      return { kind: "warn", text: `${actionLabel} inviata; completamento da verificare. ${reference}` };
    case "resolved":
      return { kind: "warn", text: `Host nuovamente avviato e verificato da FleetProbe; esito del comando ${actionLabel} non confermato. ${reference}` };
    default:
      return { kind: "warn", text: `${actionLabel}: esito ${receipt.outcome}; completamento da verificare. ${reference}` };
  }
}
