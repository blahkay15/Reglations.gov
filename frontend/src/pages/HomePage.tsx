import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { previewDocument, startJob } from "../api";
import type { Preview } from "../types";

function day(value: string): string {
  if (!value) return "—";
  return value.slice(0, 10);
}

export function HomePage() {
  const navigate = useNavigate();
  const [documentId, setDocumentId] = useState("FDA-2025-N-3262-0001");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    const trimmed = documentId.trim();
    if (trimmed.length < 4) {
      setPreview(null);
      return;
    }
    setBusy(true);
    setError("");
    const handle = window.setTimeout(() => {
      previewDocument(trimmed)
        .then((next) => {
          setPreview(next);
          setError("");
        })
        .catch((err: Error) => {
          setPreview(null);
          setError(err.message);
        })
        .finally(() => setBusy(false));
    }, 350);
    return () => window.clearTimeout(handle);
  }, [documentId]);

  async function onStart() {
    if (!preview) return;
    setStarting(true);
    try {
      const job = await startJob(preview.documentId, preview.title);
      navigate(`/jobs/${job.jobId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the job.");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
      <section>
        <h1 className="font-serif text-4xl tracking-tight">Pull a proposed rule</h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-ink/75">
          Enter the document ID from regulations.gov. The preview uses a couple of API calls. The
          full pull runs as a background job because a large rule needs one call per comment, and
          the key allows about 1,000 calls an hour.
        </p>
        <label className="mt-6 block text-sm font-medium" htmlFor="document-id">
          Document ID
        </label>
        <input
          id="document-id"
          className="mt-2 w-full max-w-xl rounded-md border border-line bg-card px-3 py-2 text-base outline-none ring-pine focus:ring-2"
          value={documentId}
          onChange={(event) => setDocumentId(event.target.value)}
          spellCheck={false}
        />
        {error ? <p className="mt-3 text-sm text-copper">{error}</p> : null}
        {preview ? (
          <article className="mt-6 max-w-xl rounded-xl border border-line bg-card p-5">
            <p className="text-xs uppercase tracking-[0.14em] text-ink/50">
              {preview.agencyId || "Agency"} · {preview.documentType || "Document"}
            </p>
            <h2 className="mt-2 font-serif text-2xl leading-snug">{preview.title || preview.documentId}</h2>
            <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-ink/50">Comments</dt>
                <dd className="text-lg tabular-nums">{preview.totalComments.toLocaleString()}</dd>
              </div>
              <div>
                <dt className="text-ink/50">Estimated pull</dt>
                <dd className="text-lg tabular-nums">{preview.estimatedHours} h</dd>
              </div>
              <div>
                <dt className="text-ink/50">Comment period</dt>
                <dd>
                  {day(preview.commentStartDate)} to {day(preview.commentEndDate)}
                </dd>
              </div>
              <div>
                <dt className="text-ink/50">Open for comment</dt>
                <dd>{preview.openForComment ? "Yes" : "No"}</dd>
              </div>
            </dl>
            <p className="mt-3 text-xs leading-5 text-ink/60">
              About {preview.estimatedApiCalls.toLocaleString()} API calls at {preview.ratePerHour.toLocaleString()}
              /hour. Attachment files are downloaded separately and do not use that budget.
            </p>
            {preview.warning ? (
              <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-warn">{preview.warning}</p>
            ) : null}
            <div className="mt-5 flex items-center gap-4">
              <button
                className="rounded-md bg-pine px-4 py-2 text-sm text-white"
                disabled={starting}
                onClick={onStart}
                type="button"
              >
                {starting ? "Starting…" : "Start background pull"}
              </button>
              <a className="text-sm text-pine underline" href={preview.regulationsGovUrl} target="_blank" rel="noreferrer">
                View on regulations.gov
              </a>
            </div>
          </article>
        ) : (
          <p className="mt-6 text-sm text-ink/60">{busy ? "Looking up the document…" : "Enter a document ID to preview it."}</p>
        )}
      </section>
      <aside className="rounded-xl border border-line bg-card p-5 text-sm leading-6 text-ink/75">
        <h2 className="font-serif text-xl text-ink">What the job saves</h2>
        <ul className="mt-3 list-disc space-y-2 pl-4">
          <li>comments.csv, with form-letter cluster columns</li>
          <li>attachments.csv and the files themselves</li>
          <li>a zip of the whole folder</li>
          <li>a dashboard of volume, organizations, and duplicate letters</li>
        </ul>
      </aside>
    </div>
  );
}
