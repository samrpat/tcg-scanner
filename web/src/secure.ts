/**
 * Where to go when the browser will not hand over the camera.
 *
 * A browser only gives a page the camera on `localhost` or over HTTPS, and the advice used to
 * be "run `make cert`" — which is useless to somebody who installed this from a compose file
 * and has never seen the repository. What they need is the address to open instead, so that
 * is what this builds.
 *
 * The port is learned from `/health/detail` once the app is up; 8443 is the documented default
 * and the right guess before then.
 */
let tlsPort = 8443;

export function rememberTlsPort(port: number | undefined) {
  if (port && port > 0) tlsPort = port;
}

export function isSecureEnough(): boolean {
  return (
    typeof window !== "undefined" &&
    (window.isSecureContext || window.location.hostname === "localhost")
  );
}

/** The https address for this same machine, for showing to somebody on http. */
export function secureUrl(): string {
  if (typeof window === "undefined") return "";
  return `https://${window.location.hostname}:${tlsPort}${window.location.pathname}`;
}

export const CAMERA_NEEDS_HTTPS =
  "A browser only gives a page the camera over a secure connection.";
