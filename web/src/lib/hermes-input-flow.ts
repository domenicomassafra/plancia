/** Tiny browser state helpers; Hermes itself owns session admission and deduplication. */
import type { HermesLease, HermesProfile, HermesSession } from "./agents-remoting";

export type HermesAttempt = { lease: HermesLease; nonce: string; text: string };

const NONCE = /^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/;

export function canUseHermesInput(profile: HermesProfile | undefined, session: HermesSession | null): boolean {
  return Boolean(profile && session && profile.gateway === "running" && profile.capabilities.readable
    && profile.capabilities.attachable && profile.capabilities.sendable);
}

export function sameHermesLease(first: HermesLease, second: HermesLease): boolean {
  return first.profileId === second.profileId && first.sessionId === second.sessionId
    && first.instanceId === second.instanceId && first.authorityEpoch === second.authorityEpoch;
}

export function createHermesAttempt(
  lease: HermesLease, text: string, generateNonce: () => string = () => crypto.randomUUID().replaceAll("-", ""),
): HermesAttempt {
  if (!text.trim() || text.length > 4096) throw new Error("Testo Hermes non valido");
  const nonce = generateNonce();
  if (!NONCE.test(nonce)) throw new Error("Nonce Hermes non valido");
  return { lease, nonce, text };
}
