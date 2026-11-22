import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { deleteJob, listJobs } from "../api";
import { ScopeTabs } from "../components/ScopeTabs";
import type { Job } from "../types";

export function HistoryPage() {
  const [scope, setScope] = useState<"mine" | "team">("mine");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");

  function refresh(next = scope) {
    listJobs(next)
      .then((payload) => setJobs(payload.jobs))
      .catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    refresh(scope);
  }, [scope]);

  return (
    <section>
      <h1 className="font-serif text-4xl">{scope === "mine" ? "My pulls" : "Team pulls"}</h1>
      <ScopeTabs scope={scope} onChange={setScope} />
      {error ? <p className="mt-3 text-sm text-copper">{error}</p> : null}
      {jobs.length === 0 ? (
        <p className="mt-6 text-sm text-ink/60">No pulls in this view.</p>
      ) : (
        <ul className="mt-6 divide-y divide-line rounded-xl border border-line bg-card">
          {jobs.map((job) => (
            <li key={job.jobId} className="flex items-center justify-between gap-4 px-4 py-3">
              <div>
                <Link className="font-medium text-pine" to={`/jobs/${job.jobId}`}>
                  {job.title || job.documentId}
                </Link>
                <p className="text-xs text-ink/50">
                  {job.documentId} · {job.status} · {job.createdAt}
                  {scope === "team" ? ` · ${job.ownerName || job.ownerEmail || job.userId}` : ""}
                </p>
              </div>
              <div className="flex items-center gap-4">
                <span className="text-sm tabular-nums text-ink/70">{job.fetched || job.commentCount || 0} comments</span>
                {job.canManage ? (
                  <button
                    className="text-sm text-copper"
                    onClick={() => deleteJob(job.jobId).then(() => refresh())}
                    type="button"
                  >
                    {job.status === "queued" || job.status === "running" ? "Cancel" : "Delete"}
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
