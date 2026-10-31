/** The official Hermes app on the same tailnet host, never a copied session UI. */
export function hermesDashboardUrl(location: Pick<Location, "protocol" | "hostname" | "port">): string | null {
  if (location.protocol !== "https:" || location.port !== "8443"
    || !/^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net$/i.test(location.hostname)) return null;
  return `https://${location.hostname}:19443/chat`;
}
