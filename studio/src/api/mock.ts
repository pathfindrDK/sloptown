// In-browser fake control plane for VITE_MOCK=1. Enough behaviour to develop
// and screenshot every screen without containers: streaming turns, queueing,
// files, history, publish, restore and budgets.
import type { Transport } from "./client";
import { MOCK_DESIGN } from "./mockDesign";
import {
  ApiError,
  type BudgetStatus,
  type Commit,
  type FileEntry,
  type Me,
  type Message,
  type Project,
  type ProjectEvent,
  type QueueItem,
  type QueueState,
  type Thread,
} from "./types";

const now = () => new Date().toISOString();
const id = () => Math.random().toString(36).slice(2, 10);
const sha = () => Array.from({ length: 40 }, () => "0123456789abcdef"[Math.floor(Math.random() * 16)]).join("");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function nextMonday(): string {
  const d = new Date();
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

const me: Me = {
  user: { sub: "mock-teacher", name: "Mette Hansen", email: "mette@skole.dk", role: "laerer", orgId: "skole-1", isSystemAdmin: false },
  termsAcceptedAt: null,
};

const TEMPLATE: Record<string, string> = {
  "AGENTS.md": "# Platform rules\n\nUse miira_vibe and @miira/vibe for identity, members and AI.\n",
  "api/main.py": `from fastapi import Depends, FastAPI
from miira_vibe import User, current_user, members

app = FastAPI()


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user


@app.get("/api/class")
async def class_list():
    return await members.list()
`,
  "web/package.json": '{\n  "name": "vibe-app",\n  "private": true\n}\n',
  "web/src/App.tsx": `import { useCurrentUser, useMembers } from "@miira/vibe";

export default function App() {
  const user = useCurrentUser();
  const members = useMembers();
  return (
    <main>
      <h1>Hej {user?.name}</h1>
      <ul>
        {members.map((m) => (
          <li key={m.id}>{m.name}</li>
        ))}
      </ul>
    </main>
  );
}
`,
  "web/src/main.tsx": `import { createRoot } from "react-dom/client";
import { VibeProvider } from "@miira/vibe";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <VibeProvider>
    <App />
  </VibeProvider>,
);
`,
  "web/src/styles.css": "main {\n  padding: 24px;\n}\n",
};
const LOCKED = new Set(["AGENTS.md", "web/package.json"]);

type ProjectData = {
  project: Project;
  threads: Thread[];
  messages: Record<string, Message[]>;
  versions: { commit: Commit; tree: Record<string, string> }[]; // newest first
  tree: Record<string, string>;
  changed: Set<string>;
  running: { threadId: string; abort: boolean } | null;
  queue: QueueItem[];
  queuePaused: boolean;
  usedTokens: number;
};

const projects = new Map<string, ProjectData>();
const listeners = new Map<string, Set<(e: ProjectEvent) => void>>();

function emit(pid: string, e: ProjectEvent) {
  listeners.get(pid)?.forEach((l) => l(e));
}

function queueState(d: ProjectData): QueueState {
  return { items: d.queue, paused: d.queuePaused && d.queue.length > 0 };
}

function emitQueue(d: ProjectData): QueueState {
  const q = queueState(d);
  setTimeout(() => emit(d.project.id, { event: "queue.changed", data: q }));
  return q;
}

function runNext(d: ProjectData) {
  if (d.running || d.queuePaused || !d.queue.length) return;
  const [q, ...rest] = d.queue;
  d.queue = rest;
  emitQueue(d);
  const qth = d.threads.find((t) => t.id === q.threadId);
  if (qth) void runTurn(d, qth, q.text);
}

function findQueued(turnId: string): [ProjectData, QueueItem] {
  for (const d of projects.values()) {
    const q = d.queue.find((x) => x.id === turnId);
    if (q) return [d, q];
  }
  throw new ApiError(409, "not_queued", "Beskeden er allerede sendt");
}

function budgetFor(d: ProjectData): BudgetStatus {
  const hard = 2_000_000;
  const nerf = 1_500_000;
  const used = d.usedTokens;
  const state = used >= hard ? "blocked" : used >= nerf ? "nerfed" : used >= hard * 0.8 ? "warning" : "ok";
  return {
    enabled: true,
    build: { usedTokens: used, nerfLimitTokens: nerf, hardLimitTokens: hard, state, resetsAt: nextMonday() },
    app: { usedTokens: 12_000, nerfLimitTokens: null, hardLimitTokens: 500_000, state: "ok", resetsAt: nextMonday() },
  };
}

function makeProject(pid: string, title: string, seeded: boolean): ProjectData {
  const created = new Date(Date.now() - 86400_000).toISOString();
  const initial: Commit = {
    sha: sha(),
    message: "Startskabelon",
    authorName: "SlopTown",
    threadId: null,
    threadTitle: null,
    createdAt: created,
    files: Object.keys(TEMPLATE),
  };
  const d: ProjectData = {
    project: {
      id: pid,
      title,
      kind: "webapp",
      slug: pid,
      ownerSub: me.user.sub,
      myRole: "owner",
      status: "ready",
      previewUrl: "/mock-preview.html",
      appUrl: null,
      publishedSha: null,
      public: false,
      description: "",
      style: "miira",
      palette: "hav",
      createdAt: created,
      updatedAt: now(),
    },
    threads: [],
    messages: {},
    versions: [{ commit: initial, tree: { ...TEMPLATE } }],
    tree: { ...TEMPLATE },
    changed: new Set(),
    running: null,
    queue: [],
    queuePaused: false,
    usedTokens: 1_240_000,
  };
  if (seeded) {
    const th: Thread = { id: "t-" + id(), projectId: pid, title: "Quiz om vikingetiden", ownerSub: me.user.sub, createdAt: created, archived: false, status: "idle" };
    d.threads.push(th, { ...th, id: "t-" + id(), title: "Farver og layout", archived: false });
    d.messages[th.id] = [
      { id: id(), threadId: th.id, role: "user", authorName: me.user.name, parts: [{ type: "text", text: "Lav en quiz om vikingetiden med 10 spørgsmål til 5. klasse." }], createdAt: created },
      {
        id: id(),
        threadId: th.id,
        role: "assistant",
        parts: [
          { type: "tool", tool: "read", title: "Læste web/src/App.tsx", status: "done", output: "…" },
          { type: "tool", tool: "write", title: "Skrev web/src/Quiz.tsx", status: "done" },
          { type: "tool", tool: "bash", title: "Kørte npm run build", status: "done", output: "✓ built in 1.2s" },
          { type: "text", text: "Jeg har lavet en quiz med 10 spørgsmål. Eleverne får point for hvert rigtigt svar, og du kan se resultaterne under \"Klassen\"." },
        ],
        createdAt: created,
      },
    ];
    const tree = { ...d.tree, "web/src/Quiz.tsx": QUIZ, "web/src/App.tsx": TEMPLATE["web/src/App.tsx"].replace("<h1>Hej {user?.name}</h1>", "<h1>Hej {user?.name}</h1>\n      <Quiz />") };
    const c: Commit = { sha: sha(), message: "Tilføj quiz om vikingetiden", authorName: me.user.name, threadId: th.id, threadTitle: th.title, createdAt: now(), files: ["web/src/Quiz.tsx", "web/src/App.tsx"] };
    d.versions.unshift({ commit: c, tree });
    d.tree = { ...tree };
    d.project.publishedSha = initial.sha;
    d.project.appUrl = "/mock-preview.html";
  }
  return d;
}

const QUIZ = `import { useState } from "react";

const questions = [
  { q: "Hvad hed vikingernes skibe?", a: ["Langskibe", "Kogger", "Galejer"], correct: 0 },
  { q: "Hvem var Harald Blåtand?", a: ["En konge", "En smed", "En gud"], correct: 0 },
];

export function Quiz() {
  const [i, setI] = useState(0);
  const [score, setScore] = useState(0);
  const item = questions[i];
  if (!item) return <p>Du fik {score} point!</p>;
  return (
    <section>
      <h2>{item.q}</h2>
      {item.a.map((a, j) => (
        <button key={a} onClick={() => { if (j === item.correct) setScore(score + 1); setI(i + 1); }}>
          {a}
        </button>
      ))}
    </section>
  );
}
`;

projects.set("vikinger", makeProject("vikinger", "Vikingequiz", true));
projects.set("broeker", makeProject("broeker", "Brøkspil", false));
projects.get("broeker")!.project.myRole = "editor";

function get(pid: string): ProjectData {
  const d = projects.get(pid);
  if (!d) throw new ApiError(404, "not_found", "Projektet findes ikke");
  return d;
}

function findThread(tid: string): [ProjectData, Thread] {
  for (const d of projects.values()) {
    const th = d.threads.find((t) => t.id === tid);
    if (th) return [d, th];
  }
  throw new ApiError(404, "not_found", "Tråden findes ikke");
}

function files(d: ProjectData): FileEntry[] {
  return Object.keys(d.tree)
    .sort()
    .map((path) => ({ path, size: d.tree[path].length, locked: LOCKED.has(path), changed: d.changed.has(path) }));
}

async function runTurn(d: ProjectData, th: Thread, text: string) {
  const pid = d.project.id;
  const turnId = id();
  d.running = { threadId: th.id, abort: false };
  th.status = "running";
  emit(pid, { event: "turn.started", data: { threadId: th.id, turnId } });
  const userMsg: Message = { id: id(), threadId: th.id, role: "user", authorName: me.user.name, parts: [{ type: "text", text }], createdAt: now() };
  d.messages[th.id] = [...(d.messages[th.id] ?? []), userMsg];
  emit(pid, { event: "message.created", data: userMsg });

  const mid = id();
  const assistant: Message = { id: mid, threadId: th.id, role: "assistant", parts: [], createdAt: now() };
  d.messages[th.id].push(assistant);
  const part = (index: number, p: Message["parts"][number]) => {
    assistant.parts[index] = p;
    emit(pid, { event: "message.part", data: { threadId: th.id, messageId: mid, index, part: p } });
  };

  const steps: [number, Message["parts"][number]][] = [
    [0, { type: "tool", tool: "read", title: "Læser web/src/App.tsx", status: "running" }],
    [0, { type: "tool", tool: "read", title: "Læste web/src/App.tsx", status: "done" }],
    [1, { type: "tool", tool: "write", title: "Skriver web/src/styles.css", status: "running" }],
  ];
  for (const [i, p] of steps) {
    if (d.running.abort) break;
    await sleep(500);
    part(i, p);
  }
  const path = "web/src/styles.css";
  d.tree[path] = `${d.tree[path]}\n/* ${text.slice(0, 60)} */\nh1 {\n  color: var(--miira-color-accent);\n}\n`;
  d.changed = new Set([path]);
  emit(pid, { event: "files.changed", data: { paths: [path] } });
  part(1, { type: "tool", tool: "write", title: "Skrev web/src/styles.css", status: "done" });

  const reply = "Færdig! Jeg har opdateret stilarket, så overskriften bruger Miiras accentfarve. Se resultatet i forhåndsvisningen.";
  let acc = "";
  for (const word of reply.split(" ")) {
    if (d.running.abort) break;
    acc += (acc ? " " : "") + word;
    part(2, { type: "text", text: acc });
    await sleep(60);
  }
  const stopped = d.running.abort;
  const commit: Commit = { sha: sha(), message: text.slice(0, 72), authorName: me.user.name, threadId: th.id, threadTitle: th.title, createdAt: now(), files: [path] };
  d.versions.unshift({ commit, tree: { ...d.tree } });
  d.usedTokens += 180_000;
  d.running = null;
  th.status = "idle";
  emit(pid, { event: "turn.finished", data: { threadId: th.id, turnId, commitSha: commit.sha, changedFiles: [path], status: stopped ? "stopped" : "done" } });
  emit(pid, { event: "budget", data: budgetFor(d) });

  runNext(d);
}

type Handler = (m: RegExpMatchArray, body: any) => unknown;
const routes: [string, RegExp, Handler][] = [
  ["GET", /^\/api\/me$/, () => me],
  ["GET", /^\/api\/terms$/, () => ({ version: "2026-10-1", html: "<h3>Vilkår for SlopTown Vibe</h3><p>Du må ikke indsamle personfølsomme oplysninger om elever i dine apps. AI-genereret kode kan indeholde fejl; du er ansvarlig for at teste appen før du deler den.</p><p>Brug af AI tæller mod et ugentligt budget.</p>" })],
  ["POST", /^\/api\/terms\/accept$/, () => ((me.termsAcceptedAt = now()), {})],
  ["POST", /^\/api\/auth\/launch$/, () => ({ token: "mock", user: me.user, fileId: "vikinger", fileRole: "owner" })],
  ["GET", /^\/api\/projects$/, () => [...projects.values()].map((d) => d.project)],
  [
    "POST",
    /^\/api\/projects$/,
    (_m, b) => {
      const d = makeProject(b.fileId, b.title ?? "Ny Vibe", false);
      d.usedTokens = 0;
      const palettes = MOCK_DESIGN.palettes.map((p) => p.id);
      d.project.style = b.style ?? "miira";
      d.project.palette = b.palette ?? palettes[Math.floor(Math.random() * palettes.length)];
      if (b.kind === "scratch") {
        d.project.style = d.project.palette = null;
        const readme = "# Mit projekt\n\nEt tomt projekt. Intet herfra bliver lagt på nettet.\n";
        d.project.kind = "scratch";
        d.project.previewUrl = null;
        d.tree = { "README.md": readme, "AGENTS.md": TEMPLATE["AGENTS.md"] ?? "" };
        d.versions = [{ commit: { ...d.versions[0].commit, message: "Start fra bunden", files: ["README.md"] }, tree: { ...d.tree } }];
      }
      projects.set(b.fileId, d);
      return d.project;
    },
  ],
  ["GET", /^\/api\/design$/, () => MOCK_DESIGN],
  // Like the control plane for the file's owner: an unknown Vibe is one to start.
  ["GET", /^\/api\/projects\/([^/]+)$/, (m) => {
    if (!projects.has(m[1])) throw new ApiError(404, "not_started", "Vibe'en er ikke startet", { canStart: true });
    return get(m[1]).project;
  }],
  ["PATCH", /^\/api\/projects\/([^/]+)$/, (m, b) => {
    const keys = ["title", "description", "public", "style", "palette"];
    const patch = Object.fromEntries(keys.filter((k) => b[k] !== undefined).map((k) => [k, b[k]]));
    // Like agentd: a palette alone on an older Vibe gives it the Miira style.
    if (patch.palette && !get(m[1]).project.style && !patch.style) patch.style = "miira";
    return Object.assign(get(m[1]).project, patch, { updatedAt: now() });
  }],
  [
    "POST",
    /^\/api\/projects\/([^/]+)\/publish$/,
    (m, b) => {
      const p = get(m[1]).project;
      p.publishedSha = b.sha;
      p.appUrl = "/mock-preview.html";
      return { publishedSha: b.sha, url: p.appUrl };
    },
  ],
  ["POST", /^\/api\/projects\/([^/]+)\/unpublish$/, (m) => ((get(m[1]).project.publishedSha = null), {})],
  ["GET", /^\/api\/projects\/([^/]+)\/budget$/, (m) => budgetFor(get(m[1]))],
  ["POST", /^\/api\/projects\/([^/]+)\/preview-session$/, (_m, b) => ({ url: `/mock-preview.html?viewAs=${b?.viewAs ?? "owner"}` })],
  ["POST", /^\/api\/projects\/([^/]+)\/app-session$/, () => ({ url: "/mock-preview.html?viewAs=viewer" })],
  ["GET", /^\/api\/projects\/([^/]+)\/threads/, (m) => get(m[1]).threads],
  [
    "POST",
    /^\/api\/projects\/([^/]+)\/threads$/,
    (m, b) => {
      const d = get(m[1]);
      const th: Thread = { id: "t-" + id(), projectId: d.project.id, title: b?.title ?? "Ny tråd", ownerSub: me.user.sub, createdAt: now(), archived: false, status: "idle" };
      d.threads.push(th);
      d.messages[th.id] = [];
      return th;
    },
  ],
  ["PATCH", /^\/api\/threads\/([^/]+)$/, (m, b) => Object.assign(findThread(m[1])[1], b)],
  ["GET", /^\/api\/threads\/([^/]+)\/messages$/, (m) => findThread(m[1])[0].messages[m[1]] ?? []],
  [
    "POST",
    /^\/api\/threads\/([^/]+)\/messages$/,
    (m, b) => {
      const [d, th] = findThread(m[1]);
      if (budgetFor(d).build.state === "blocked") throw new ApiError(429, "budget_blocked", "Budget opbrugt", { retryAt: nextMonday() });
      const turnId = id();
      if (d.running) {
        if (d.queue.length >= 20) throw new ApiError(409, "queue_full", "Der står allerede 20 beskeder i kø");
        d.queue.push({ id: turnId, threadId: th.id, text: b.text, imageCount: b.images?.length ?? 0, authorSub: me.user.sub, queuedAt: now() });
        emitQueue(d);
        return { turnId, queued: true };
      }
      d.queuePaused = false;
      emitQueue(d);
      void runTurn(d, th, b.text);
      return { turnId, queued: false };
    },
  ],
  [
    "POST",
    /^\/api\/threads\/([^/]+)\/stop$/,
    (m) => {
      const [d] = findThread(m[1]);
      if (d.running?.threadId === m[1]) {
        d.running.abort = true;
        d.queuePaused = true;
        emitQueue(d);
      }
      return {};
    },
  ],
  ["GET", /^\/api\/projects\/([^/]+)\/queue$/, (m) => queueState(get(m[1]))],
  [
    "PATCH",
    /^\/api\/turns\/([^/]+)$/,
    (m, b) => {
      const [d, q] = findQueued(m[1]);
      q.text = b.text;
      return emitQueue(d);
    },
  ],
  [
    "DELETE",
    /^\/api\/turns\/([^/]+)$/,
    (m) => {
      const [d, q] = findQueued(m[1]);
      d.queue = d.queue.filter((x) => x !== q);
      return emitQueue(d);
    },
  ],
  [
    "POST",
    /^\/api\/turns\/([^/]+)\/send-now$/,
    (m) => {
      const [d, q] = findQueued(m[1]);
      d.queue = [q, ...d.queue.filter((x) => x !== q)];
      d.queuePaused = false;
      if (d.running?.threadId === q.threadId) d.running.abort = true;
      else runNext(d);
      return emitQueue(d);
    },
  ],
  ["GET", /^\/api\/projects\/([^/]+)\/files$/, (m) => ({ files: files(get(m[1])) })],
  [
    "GET",
    /^\/api\/projects\/([^/]+)\/files\/(.+?)(?:\?sha=(.+))?$/,
    (m) => {
      const d = get(m[1]);
      const path = decodeURIComponent(m[2]);
      const tree = m[3] ? d.versions.find((v) => v.commit.sha === m[3])?.tree : d.tree;
      if (!tree || !(path in tree)) throw new ApiError(404, "not_found", "Filen findes ikke");
      return { path, content: tree[path], locked: LOCKED.has(path), language: path.split(".").pop() };
    },
  ],
  ["GET", /^\/api\/projects\/([^/]+)\/history$/, (m) => ({ commits: get(m[1]).versions.map((v) => v.commit), publishedSha: get(m[1]).project.publishedSha })],
  [
    "GET",
    /^\/api\/projects\/([^/]+)\/history\/([^/]+)\/diff$/,
    (m) => {
      const d = get(m[1]);
      const i = d.versions.findIndex((v) => v.commit.sha === m[2]);
      const cur = d.versions[i];
      const prev = d.versions[i + 1]?.tree ?? {};
      return {
        sha: m[2],
        files: cur.commit.files.map((path) => ({
          path,
          status: path in prev ? "modified" : "added",
          patch: `@@ ${path} @@\n${(prev[path] ?? "").split("\n").map((l) => "-" + l).join("\n")}\n${cur.tree[path].split("\n").map((l) => "+" + l).join("\n")}`,
        })),
      };
    },
  ],
  [
    "POST",
    /^\/api\/projects\/([^/]+)\/restore$/,
    (m, b) => {
      const d = get(m[1]);
      const v = d.versions.find((x) => x.commit.sha === b.sha);
      if (!v) throw new ApiError(404, "not_found", "Versionen findes ikke");
      d.tree = { ...v.tree };
      const c: Commit = { sha: sha(), message: `Gendan version ${b.sha.slice(0, 7)}`, authorName: me.user.name, threadId: null, threadTitle: null, createdAt: now(), files: v.commit.files };
      d.versions.unshift({ commit: c, tree: { ...d.tree } });
      emit(d.project.id, { event: "files.changed", data: { paths: c.files } });
      return c;
    },
  ],
];

export const mockTransport: Transport = {
  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    await sleep(80);
    for (const [m, re, handler] of routes) {
      const match = m === method ? path.match(re) : null;
      if (match) return structuredClone(handler(match, body)) as T;
    }
    throw new ApiError(404, "not_found", `mock: no route for ${method} ${path}`);
  },
  subscribe(path, onEvent, signal) {
    const pid = decodeURIComponent(path.split("/")[3]);
    const set = listeners.get(pid) ?? new Set();
    listeners.set(pid, set);
    const l = (e: ProjectEvent) => onEvent(structuredClone(e));
    set.add(l);
    signal.addEventListener("abort", () => set.delete(l));
  },
};
