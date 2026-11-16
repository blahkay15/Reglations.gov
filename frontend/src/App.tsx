import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { setApiConfig } from "./api";
import { beginLogin, finishLogin, signOut, accessToken } from "./auth";
import { loadConfig, type AppConfig } from "./config";

export function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    loadConfig()
      .then(async (next) => {
        setApiConfig(next);
        await finishLogin(next);
        setConfig(next);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  if (!config) {
    return <p className="p-8 text-sm">{error || "Loading…"}</p>;
  }
  const signedIn = config.local || Boolean(accessToken());

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-card">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-6 py-4">
          <div>
            <Link to="/" className="font-serif text-2xl tracking-tight">
              Regulations comments
            </Link>
            <p className="text-xs uppercase tracking-[0.14em] text-ink/50">
              {config.local ? "Local mode" : "Team workspace"}
            </p>
          </div>
          <nav className="flex items-center gap-5 text-sm">
            <Link to="/" className="hover:text-pine">
              New pull
            </Link>
            <Link to="/history" className="hover:text-pine">
              History
            </Link>
            <Link to="/watches" className="hover:text-pine">
              Watches
            </Link>
            {config.local ? null : signedIn ? (
              <button className="text-ink/70 hover:text-ink" onClick={signOut} type="button">
                Sign out
              </button>
            ) : (
              <button
                className="rounded-md bg-pine px-3 py-1.5 text-white"
                onClick={() => beginLogin(config)}
                type="button"
              >
                Sign in
              </button>
            )}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        {error ? <p className="mb-4 text-sm text-copper">{error}</p> : null}
        {!config.local && !signedIn ? (
          <section className="max-w-xl rounded-xl border border-line bg-card p-6">
            <h1 className="font-serif text-3xl">Sign in to pull comments</h1>
            <p className="mt-3 text-sm leading-6 text-ink/75">
              The workspace uses your company sign-in. Pulls run in the background, so you can close
              the browser while a large rule downloads.
            </p>
            <button
              className="mt-5 rounded-md bg-pine px-4 py-2 text-sm text-white"
              onClick={() => beginLogin(config)}
              type="button"
            >
              Continue to sign-in
            </button>
          </section>
        ) : (
          <Outlet />
        )}
      </main>
    </div>
  );
}
