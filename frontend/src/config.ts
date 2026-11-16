export type AppConfig = {
  local: boolean;
  clientId?: string;
  cognitoDomain?: string;
  redirectUri?: string;
};

export async function loadConfig(): Promise<AppConfig> {
  // `npm run dev` is the local fixture. A production build reads Cognito
  // settings from /config.json published next to the site.
  if (import.meta.env.DEV || import.meta.env.VITE_LOCAL === "1") {
    return { local: true };
  }
  const response = await fetch("/config.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("config.json is missing. Publish it with the site deploy before signing in.");
  }
  const data = (await response.json()) as Omit<AppConfig, "local">;
  return { local: false, ...data };
}
