// Typed client for the control-plane API (docs/internal-api.md).
import { SseParser } from "./sse";
import {
  ApiError,
  type BudgetStatus,
  type Commit,
  type DesignCatalog,
  type Diff,
  type FileContent,
  type FileEntry,
  type History,
  type ImageAttachment,
  type LaunchResult,
  type Me,
  type Message,
  type Project,
  type ProjectEvent,
  type ProjectKind,
  type PublishFixing,
  type Published,
  type QueueState,
  type Terms,
  type Thread,
} from "./types";

export type Transport = {
  request<T>(method: string, path: string, body?: unknown): Promise<T>;
  subscribe(path: string, onEvent: (ev: ProjectEvent) => void, signal: AbortSignal): void;
};

// Bearer token from a launch session lives only in memory (never storage).
let bearer: string | null = null;
export function setBearer(token: string | null) {
  bearer = token;
}

function headers(json: boolean): HeadersInit {
  const h: Record<string, string> = { Accept: "application/json" };
  if (json) h["Content-Type"] = "application/json";
  if (bearer) h.Authorization = `Bearer ${bearer}`;
  return h;
}

export const httpTransport: Transport = {
  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(path, {
      method,
      credentials: "include",
      headers: headers(body !== undefined),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    const parsed = text ? JSON.parse(text) : {};
    if (!res.ok) {
      throw new ApiError(res.status, parsed.error ?? "http_error", parsed.message ?? res.statusText, parsed);
    }
    return parsed as T;
  },

  subscribe(path, onEvent, signal) {
    let retry = 1000;
    const run = async () => {
      while (!signal.aborted) {
        try {
          const res = await fetch(path, { credentials: "include", headers: { ...headers(false), Accept: "text/event-stream" }, signal });
          if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`);
          retry = 1000;
          const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
          const parser = new SseParser();
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            for (const raw of parser.push(value)) {
              try {
                onEvent({ event: raw.event, data: JSON.parse(raw.data) } as ProjectEvent);
              } catch {
                /* ignore malformed event */
              }
            }
          }
        } catch {
          if (signal.aborted) return;
        }
        await new Promise((r) => setTimeout(r, retry));
        retry = Math.min(retry * 2, 15000);
      }
    };
    void run();
  },
};

let transport: Transport = httpTransport;
export function setTransport(t: Transport) {
  transport = t;
}

const req = <T>(method: string, path: string, body?: unknown) => transport.request<T>(method, path, body);
const enc = encodeURIComponent;
const encPath = (p: string) => p.split("/").map(enc).join("/");

export const api = {
  // Auth
  loginUrl: (next: string) => `/auth/login?next=${enc(next)}`,
  logout: () => req<void>("POST", "/auth/logout"),
  launch: (launchToken: string) => req<LaunchResult>("POST", "/api/auth/launch", { launchToken }),
  me: () => req<Me>("GET", "/api/me"),
  terms: () => req<Terms>("GET", "/api/terms"),
  acceptTerms: (version: string) => req<void>("POST", "/api/terms/accept", { version }),

  // Projects
  listProjects: () => req<Project[]>("GET", "/api/projects"),
  createProject: (fileId: string, kind: ProjectKind = "webapp", title?: string, design?: { style?: string; palette?: string }) =>
    req<Project>("POST", "/api/projects", { fileId, kind, title, ...design }),
  design: () => req<DesignCatalog>("GET", "/api/design"),
  getProject: (pid: string) => req<Project>("GET", `/api/projects/${enc(pid)}`),
  renameProject: (pid: string, title: string) => req<Project>("PATCH", `/api/projects/${enc(pid)}`, { title }),
  updateProject: (pid: string, patch: { description?: string; public?: boolean; style?: string; palette?: string }) =>
    req<Project>("PATCH", `/api/projects/${enc(pid)}`, patch),
  publish: (pid: string, sha: string) =>
    req<Published | PublishFixing>("POST", `/api/projects/${enc(pid)}/publish`, { sha }),
  unpublish: (pid: string) => req<void>("POST", `/api/projects/${enc(pid)}/unpublish`),
  budget: (pid: string) => req<BudgetStatus>("GET", `/api/projects/${enc(pid)}/budget`),
  previewSession: (pid: string, viewAs?: string) =>
    req<{ url: string }>("POST", `/api/projects/${enc(pid)}/preview-session`, viewAs ? { viewAs } : {}),
  appSession: (pid: string) => req<{ url: string }>("POST", `/api/projects/${enc(pid)}/app-session`),
  /** The files at HEAD as a .zip, saved by the browser. Fetched (not a link) so the
   *  in-memory bearer of an embedded session is sent. */
  download: async (pid: string, filename: string) => {
    const res = await fetch(`/api/projects/${enc(pid)}/download`, { credentials: "include", headers: headers(false) });
    if (!res.ok) throw new ApiError(res.status, "download_failed", res.statusText);
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: filename });
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },

  // Threads and chat
  listThreads: (pid: string, archived = false) =>
    req<Thread[]>("GET", `/api/projects/${enc(pid)}/threads${archived ? "?archived=1" : ""}`),
  createThread: (pid: string, title?: string) => req<Thread>("POST", `/api/projects/${enc(pid)}/threads`, { title }),
  updateThread: (tid: string, patch: { title?: string; archived?: boolean }) =>
    req<Thread>("PATCH", `/api/threads/${enc(tid)}`, patch),
  messages: (tid: string) => req<Message[]>("GET", `/api/threads/${enc(tid)}/messages`),
  send: (tid: string, text: string, images?: ImageAttachment[]) =>
    req<{ turnId: string; queued: boolean }>("POST", `/api/threads/${enc(tid)}/messages`, { text, images }),
  stop: (tid: string) => req<void>("POST", `/api/threads/${enc(tid)}/stop`),
  queue: (pid: string) => req<QueueState>("GET", `/api/projects/${enc(pid)}/queue`),
  editQueued: (turnId: string, text: string) => req<QueueState>("PATCH", `/api/turns/${enc(turnId)}`, { text }),
  deleteQueued: (turnId: string) => req<QueueState>("DELETE", `/api/turns/${enc(turnId)}`),
  sendNow: (turnId: string) => req<QueueState>("POST", `/api/turns/${enc(turnId)}/send-now`),
  events: (pid: string, onEvent: (ev: ProjectEvent) => void, signal: AbortSignal) =>
    transport.subscribe(`/api/projects/${enc(pid)}/events`, onEvent, signal),

  // Files and history
  files: (pid: string) => req<{ files: FileEntry[] }>("GET", `/api/projects/${enc(pid)}/files`),
  file: (pid: string, path: string, sha?: string) =>
    req<FileContent>("GET", `/api/projects/${enc(pid)}/files/${encPath(path)}${sha ? `?sha=${enc(sha)}` : ""}`),
  /** A media file's bytes for the file preview (images, audio, video, PDF), as a Blob.
   *  Fetched so the in-memory bearer is sent; shown from a blob URL. */
  rawFile: async (pid: string, path: string, sha?: string): Promise<Blob> => {
    const url = `/api/projects/${enc(pid)}/raw/${encPath(path)}${sha ? `?sha=${enc(sha)}` : ""}`;
    const res = await fetch(url, { credentials: "include", headers: headers(false) });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, body.error ?? "raw_failed", body.message ?? res.statusText);
    }
    return res.blob();
  },
  history: (pid: string) => req<History>("GET", `/api/projects/${enc(pid)}/history`),
  diff: (pid: string, sha: string) => req<Diff>("GET", `/api/projects/${enc(pid)}/history/${enc(sha)}/diff`),
  restore: (pid: string, sha: string) => req<Commit>("POST", `/api/projects/${enc(pid)}/restore`, { sha }),
};
