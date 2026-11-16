import type { AppConfig } from "./config";

const TOKEN_KEY = "regs.accessToken";
const VERIFIER_KEY = "regs.pkce";

function base64url(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let text = "";
  for (const byte of view) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return base64url(digest);
}

export function accessToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

export function signOut(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  window.location.hash = "#/";
  window.location.reload();
}

export async function beginLogin(config: AppConfig): Promise<void> {
  if (!config.clientId || !config.cognitoDomain || !config.redirectUri) {
    throw new Error("Cognito is not configured. /config.json is missing clientId or cognitoDomain.");
  }
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  const challenge = await sha256(verifier);
  const url = new URL(`${config.cognitoDomain.replace(/\/$/, "")}/oauth2/authorize`);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", config.redirectUri);
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("code_challenge", challenge);
  window.location.assign(url.toString());
}

export async function finishLogin(config: AppConfig): Promise<void> {
  if (config.local) return;
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  if (!code || !config.clientId || !config.cognitoDomain || !config.redirectUri) return;
  const verifier = sessionStorage.getItem(VERIFIER_KEY) || "";
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: config.clientId,
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier,
  });
  const response = await fetch(`${config.cognitoDomain.replace(/\/$/, "")}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!response.ok) {
    throw new Error("Cognito token exchange failed.");
  }
  const payload = (await response.json()) as { id_token: string };
  // The ID token carries email, name, and group claims. The API authorizer
  // checks its audience, which is this app client.
  sessionStorage.setItem(TOKEN_KEY, payload.id_token);
  window.history.replaceState({}, "", window.location.pathname);
}
