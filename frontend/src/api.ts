import { accessToken } from "./auth";
import type { AppConfig } from "./config";
import type { CommentPage, Job, Preview, Summary, Watch } from "./types";

let config: AppConfig = { local: true };

export function setApiConfig(next: AppConfig): void {
  config = next;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (config.local) {
    headers.set("X-Local-User", "local");
  } else if (accessToken()) {
    headers.set("authorization", `Bearer ${accessToken()}`);
  }
  if (init.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  const text = await response.text();
  const payload = text ? (JSON.parse(text) as T & { error?: string }) : ({} as T);
  if (!response.ok) {
    throw new Error((payload as { error?: string }).error || `Request failed (${response.status})`);
  }
  return payload;
}

export function previewDocument(documentId: string): Promise<Preview> {
  return request(`/api/preview?documentId=${encodeURIComponent(documentId)}`);
}

export function startJob(documentId: string, title: string): Promise<Job> {
  return request("/api/jobs", { method: "POST", body: JSON.stringify({ documentId, title }) });
}

export function listJobs(scope: "mine" | "team" = "mine"): Promise<{ jobs: Job[] }> {
  return request(`/api/jobs?scope=${scope}`);
}

export function deleteJob(jobId: string): Promise<{ status?: string; deleted?: string }> {
  return request(`/api/jobs/${jobId}`, { method: "DELETE" });
}

export function getJob(jobId: string): Promise<Job> {
  return request(`/api/jobs/${jobId}`);
}

export function getSummary(jobId: string): Promise<Summary> {
  return request(`/api/jobs/${jobId}/summary`);
}

export function getComments(
  jobId: string,
  query: { q?: string; organization?: string; form?: string; page?: number },
): Promise<CommentPage> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.organization) params.set("organization", query.organization);
  if (query.form) params.set("form", query.form);
  params.set("page", String(query.page || 1));
  params.set("pageSize", "20");
  return request(`/api/jobs/${jobId}/comments?${params.toString()}`);
}

export async function downloadJob(jobId: string, kind: "zip" | "comments" | "attachments"): Promise<void> {
  const payload = await request<{ url: string }>(`/api/jobs/${jobId}/download?kind=${kind}`);
  window.location.assign(payload.url);
}

export function listWatches(scope: "mine" | "team" = "mine"): Promise<{ watches: Watch[] }> {
  return request(`/api/watches?scope=${scope}`);
}

export function createWatch(documentId: string, webhookUrl: string): Promise<Watch> {
  return request("/api/watches", {
    method: "POST",
    body: JSON.stringify({ documentId, webhookUrl }),
  });
}

export function deleteWatch(documentId: string, ownerId?: string): Promise<{ deleted: string }> {
  const path = ownerId
    ? `/api/watches/${encodeURIComponent(ownerId)}/${encodeURIComponent(documentId)}`
    : `/api/watches/${encodeURIComponent(documentId)}`;
  return request(path, { method: "DELETE" });
}

export function replaceWatchWebhook(ownerId: string, documentId: string, webhookUrl: string): Promise<Watch> {
  return request(`/api/watches/${encodeURIComponent(ownerId)}/${encodeURIComponent(documentId)}`, {
    method: "PATCH",
    body: JSON.stringify({ webhookUrl }),
  });
}
