import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { deleteJob, downloadJob, getComments, getJob, getSummary } from "../api";
import { Bars } from "../components/Bars";
import type { CommentPage, Job, Summary } from "../types";

export function JobPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [page, setPage] = useState<CommentPage | null>(null);
  const [query, setQuery] = useState("");
  const [form, setForm] = useState("");
  const [pageNumber, setPageNumber] = useState(1);
  const [error, setError] = useState("");

  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const next = await getJob(jobId);
        if (stop) return;
        setJob(next);
        if (next.status === "succeeded") {
          const [sum, comments] = await Promise.all([
            getSummary(jobId),
            getComments(jobId, { q: query, form, page: pageNumber }),
          ]);
          if (!stop) {
            setSummary(sum);
            setPage(comments);
          }
        }
      } catch (err) {
        if (!stop) setError(err instanceof Error ? err.message : "Could not load the job.");
      }
    }
    tick();
    const handle = window.setInterval(tick, 2000);
    return () => {
      stop = true;
      window.clearInterval(handle);
    };
  }, [jobId, query, form, pageNumber]);

  if (!job) {
    return <p className="text-sm">{error || "Loading the job…"}</p>;
  }
  const running = job.status === "queued" || job.status === "running";
  const total = job.totalComments || job.queued || 0;
  const progress = total > 0 ? Math.min(100, Math.round((job.fetched / total) * 100)) : running ? 8 : 0;

  return (
    <section>
      <p className="text-xs uppercase tracking-[0.14em] text-ink/50">{job.documentId}</p>
      <h1 className="mt-1 font-serif text-4xl">{job.title || "Comment pull"}</h1>
      <p className="mt-2 text-sm text-ink/70">
        Status: <span className="font-medium text-ink">{job.status}</span>
        {job.ownerName ? ` · started by ${job.ownerName}` : ""}
        {job.createdAt ? ` · ${job.createdAt}` : ""}
      </p>
      {job.canManage ? (
        <button
          className="mt-3 text-sm text-copper"
          onClick={() =>
            deleteJob(jobId).then((result) => {
              if (result.deleted) navigate("/history");
              else getJob(jobId).then(setJob);
            })
          }
          type="button"
        >
          {job.status === "queued" || job.status === "running" ? "Cancel pull" : "Delete pull"}
        </button>
      ) : null}
      {job.error ? <p className="mt-3 text-sm text-copper">{job.error}</p> : null}
      {running ? (
        <div className="mt-6 max-w-xl">
          <div className="h-2 rounded-full bg-line">
            <div className="h-2 rounded-full bg-pine" style={{ width: `${progress}%` }} />
          </div>
          <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
            <div>
              <dt className="text-ink/50">Comments saved</dt>
              <dd className="text-xl tabular-nums">{job.fetched}</dd>
            </div>
            <div>
              <dt className="text-ink/50">Attachments</dt>
              <dd className="text-xl tabular-nums">{job.attachmentsDownloaded}</dd>
            </div>
            <div>
              <dt className="text-ink/50">Failed files</dt>
              <dd className="text-xl tabular-nums">{job.attachmentFailures}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-ink/50">This page updates every few seconds. You can leave and come back.</p>
        </div>
      ) : null}
      {summary ? (
        <div className="mt-8 space-y-8">
          <div className="flex flex-wrap gap-3">
            <button className="rounded-md bg-pine px-3 py-2 text-sm text-white" onClick={() => downloadJob(jobId, "zip")} type="button">
              Download zip
            </button>
            <button className="rounded-md border border-line bg-card px-3 py-2 text-sm" onClick={() => downloadJob(jobId, "comments")} type="button">
              comments.csv
            </button>
            <button className="rounded-md border border-line bg-card px-3 py-2 text-sm" onClick={() => downloadJob(jobId, "attachments")} type="button">
              attachments.csv
            </button>
          </div>
          <div className="grid gap-4 sm:grid-cols-4">
            <Stat label="Comments" value={summary.commentCount} />
            <Stat label="Form letters" value={summary.formLetterCount} />
            <Stat label="Unique" value={summary.uniqueCount} />
            <Stat label="Files saved" value={summary.attachmentsDownloaded} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Panel title="Comments over time">
              <Bars items={summary.commentsOverTime.map((item) => ({ label: item.date, count: item.count }))} empty="No dates on these comments." />
            </Panel>
            <Panel title="Top organizations">
              <Bars items={summary.topOrganizations.map((item) => ({ label: item.name, count: item.count }))} empty="No organizations." />
            </Panel>
            <Panel title="Attachment types">
              <Bars items={summary.attachmentTypes.map((item) => ({ label: item.format, count: item.count }))} empty="No attachments." />
            </Panel>
            <Panel title="Form-letter clusters">
              {summary.clusters.length === 0 ? (
                <p className="text-sm text-ink/60">No cluster reached the minimum size.</p>
              ) : (
                <ul className="space-y-4">
                  {summary.clusters.map((cluster) => (
                    <li key={cluster.clusterId}>
                      <p className="text-sm font-medium">{cluster.count} copies</p>
                      <p className="mt-1 text-sm leading-6 text-ink/75">{cluster.representativeText}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
          <Panel title="Comments">
            <div className="mb-4 flex flex-wrap gap-3">
              <input
                className="rounded-md border border-line px-3 py-2 text-sm"
                placeholder="Search text or organization"
                value={query}
                onChange={(event) => {
                  setPageNumber(1);
                  setQuery(event.target.value);
                }}
              />
              <select
                className="rounded-md border border-line bg-card px-3 py-2 text-sm"
                value={form}
                onChange={(event) => {
                  setPageNumber(1);
                  setForm(event.target.value);
                }}
              >
                <option value="">All comments</option>
                <option value="yes">Form letters</option>
                <option value="no">Unique</option>
              </select>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-ink/50">
                  <tr>
                    <th className="py-2 pr-3">Posted</th>
                    <th className="py-2 pr-3">Organization</th>
                    <th className="py-2 pr-3">Comment</th>
                    <th className="py-2">Files</th>
                  </tr>
                </thead>
                <tbody>
                  {(page?.comments || []).map((comment) => (
                    <tr key={comment.id} className="border-t border-line align-top">
                      <td className="py-3 pr-3 whitespace-nowrap">{comment.postedDate || "—"}</td>
                      <td className="py-3 pr-3">{comment.organization || "—"}</td>
                      <td className="py-3 pr-3">
                        <a className="text-pine underline" href={comment.commentUrl} target="_blank" rel="noreferrer">
                          {comment.title || comment.id}
                        </a>
                        <p className="mt-1 line-clamp-3 text-ink/75">{comment.comment}</p>
                        {comment.isFormLetter === "yes" ? (
                          <p className="mt-1 text-xs uppercase tracking-wide text-copper">Form letter · {comment.clusterId}</p>
                        ) : null}
                      </td>
                      <td className="py-3 text-ink/70">{comment.attachmentFiles ? comment.attachmentFiles.split("; ").length : 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {page && page.total > page.pageSize ? (
              <div className="mt-4 flex items-center gap-3 text-sm">
                <button
                  className="rounded border border-line px-2 py-1"
                  disabled={pageNumber <= 1}
                  onClick={() => setPageNumber((current) => current - 1)}
                  type="button"
                >
                  Previous
                </button>
                <span>
                  Page {page.page} of {Math.ceil(page.total / page.pageSize)}
                </span>
                <button
                  className="rounded border border-line px-2 py-1"
                  disabled={page.page * page.pageSize >= page.total}
                  onClick={() => setPageNumber((current) => current + 1)}
                  type="button"
                >
                  Next
                </button>
              </div>
            ) : null}
          </Panel>
        </div>
      ) : null}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-line bg-card px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-ink/50">{label}</p>
      <p className="mt-1 text-2xl tabular-nums">{value.toLocaleString()}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-card p-5">
      <h2 className="mb-4 font-serif text-2xl">{title}</h2>
      {children}
    </section>
  );
}
