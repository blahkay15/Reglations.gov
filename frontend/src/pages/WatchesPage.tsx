import { useEffect, useState, type FormEvent } from "react";
import { createWatch, deleteWatch, listWatches } from "../api";
import type { Watch } from "../types";

export function WatchesPage() {
  const [watches, setWatches] = useState<Watch[]>([]);
  const [documentId, setDocumentId] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    listWatches()
      .then((payload) => setWatches(payload.watches))
      .catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await createWatch(documentId.trim(), webhookUrl.trim());
      setWebhookUrl("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the watch.");
    }
  }

  return (
    <section className="max-w-3xl">
      <h1 className="font-serif text-4xl">Watch a document</h1>
      <p className="mt-3 text-sm leading-6 text-ink/75">
        On weekdays the scheduler checks for comments it has not reported yet. If nothing is new,
        it stays quiet. The webhook URL is stored for the server only and is not shown again.
      </p>
      <form className="mt-6 space-y-3 rounded-xl border border-line bg-card p-5" onSubmit={onCreate}>
        <label className="block text-sm">
          Document ID
          <input
            className="mt-1 w-full rounded-md border border-line px-3 py-2"
            value={documentId}
            onChange={(event) => setDocumentId(event.target.value)}
            required
          />
        </label>
        <label className="block text-sm">
          Teams webhook URL
          <input
            className="mt-1 w-full rounded-md border border-line px-3 py-2"
            value={webhookUrl}
            onChange={(event) => setWebhookUrl(event.target.value)}
            placeholder="https://...."
            type="url"
            required
          />
        </label>
        {error ? <p className="text-sm text-copper">{error}</p> : null}
        <button className="rounded-md bg-pine px-4 py-2 text-sm text-white" type="submit">
          Save watch
        </button>
      </form>
      <ul className="mt-6 divide-y divide-line rounded-xl border border-line bg-card">
        {watches.length === 0 ? <li className="px-4 py-3 text-sm text-ink/60">No watches yet.</li> : null}
        {watches.map((watch) => (
          <li key={watch.documentId} className="flex items-center justify-between px-4 py-3 text-sm">
            <div>
              <p className="font-medium">{watch.documentId}</p>
              <p className="text-xs text-ink/50">
                {watch.webhookConfigured ? "Webhook stored" : "No webhook"} · last check {watch.lastCheckedAt || "never"}
              </p>
            </div>
            <button
              className="text-copper"
              onClick={() => deleteWatch(watch.documentId).then(refresh)}
              type="button"
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
