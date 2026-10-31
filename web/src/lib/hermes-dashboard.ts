/** The official Hermes app needs a separately named tailnet service for cookie isolation. */
export function hermesDashboardUrl(
  location: Pick<Location, "protocol" | "hostname" | "port">,
  configuredOrigin: string | null,
): string | null {
  if (location.protocol !== "https:" || location.port !== "8443"
    || !/^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net$/i.test(location.hostname)
    || !configuredOrigin) return null;
  try {
    const url = new URL(configuredOrigin);
    if (url.protocol !== "https:" || url.username || url.password || url.port
      || url.pathname !== "/" || url.search || url.hash
      || url.hostname.toLowerCase() === location.hostname.toLowerCase()
      || !/^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net$/i.test(url.hostname)
      || url.origin !== configuredOrigin) return null;
    return `${url.origin}/chat`;
  } catch {
    return null;
  }
}
