import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listJobs } from "../api";
import type { Job } from "../types";

export function HistoryPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    listJobs()
      .then((payload) => setJobs(payload.jobs))
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <section>
      <h1 className="font-serif text-4xl">Your pulls</h1>
      {error ? <p className="mt-3 text-sm text-copper">{error}</p> : null}
      {jobs.length === 0 ? (
        <p className="mt-6 text-sm text-ink/60">No pulls yet.</p>
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
                </p>
              </div>
              <span className="text-sm tabular-nums text-ink/70">{job.fetched || job.commentCount || 0} comments</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
