// Reducer for the editor's live state, fed by the project SSE stream.
import type { BudgetStatus, Commit, Message, Part, ProjectEvent, PublishFailure, PublishFixing, Published, QueueItem, Thread } from "../api/types";

/** True while only the platform has committed (template seed, design starter,
 *  platform updates: all `Vibe-Author: SlopTown`, as agentd's
 *  only_platform_commits). The preview then shows the welcome cover. */
export function onlyPlatformCommits(commits: Commit[]): boolean {
  return commits.every((c) => c.authorName === "SlopTown");
}

/** When the earliest loaded teacher message was sent (ms), or null if none is loaded.
 *  While a Vibe is still empty that is the message its first build answers. */
export function firstMessageAt(messages: Record<string, Message[]>): number | null {
  let first: number | null = null;
  for (const list of Object.values(messages))
    for (const m of list) {
      const at = Date.parse(m.createdAt);
      if (m.role === "user" && !Number.isNaN(at) && (first === null || at < first)) first = at;
    }
  return first;
}

export type ProjectState = {
  threads: Thread[];
  messages: Record<string, Message[]>; // by thread id
  runningThreadId: string | null;
  queuedThreadId: string | null;
  queue: QueueItem[];
  queuePaused: boolean;
  changedFiles: string[]; // changed by the last finished turn (or in progress)
  lastCommitSha: string | null;
  budget: BudgetStatus | null;
  filesVersion: number; // bumped on files.changed so views refetch
  historyVersion: number; // bumped on turn.finished
  // Publishing that finishes on the server after the request returned (pre-publish check):
  publishCheck: PublishFixing | null; // the AI is fixing what the check found
  published: Published | null; // the latest project.published
  publishFailure: PublishFailure | null; // the latest publish.failed
};

export const initialProjectState: ProjectState = {
  threads: [],
  messages: {},
  runningThreadId: null,
  queuedThreadId: null,
  queue: [],
  queuePaused: false,
  changedFiles: [],
  lastCommitSha: null,
  budget: null,
  filesVersion: 0,
  historyVersion: 0,
  publishCheck: null,
  published: null,
  publishFailure: null,
};

export type Action =
  | ProjectEvent
  | { event: "local.threads"; data: Thread[] }
  | { event: "local.messages"; data: { threadId: string; messages: Message[] } }
  | { event: "local.budget"; data: BudgetStatus };

function upsertThread(threads: Thread[], t: Thread): Thread[] {
  const i = threads.findIndex((x) => x.id === t.id);
  if (i === -1) return [...threads, t];
  const next = threads.slice();
  next[i] = t;
  return next;
}

function setThreadStatus(threads: Thread[], id: string, status: Thread["status"]): Thread[] {
  return threads.map((t) => (t.id === id ? { ...t, status } : t));
}

function upsertMessage(list: Message[], msg: Message): Message[] {
  const i = list.findIndex((m) => m.id === msg.id);
  if (i === -1) return [...list, msg];
  const next = list.slice();
  next[i] = msg;
  return next;
}

function upsertPart(list: Message[], threadId: string, messageId: string, index: number, part: Part): Message[] {
  const i = list.findIndex((m) => m.id === messageId);
  const base: Message =
    i === -1
      ? { id: messageId, threadId, role: "assistant", parts: [], createdAt: new Date().toISOString() }
      : list[i];
  const parts = base.parts.slice();
  while (parts.length < index) parts.push({ type: "text", text: "" });
  parts[index] = part;
  const msg = { ...base, parts };
  return i === -1 ? [...list, msg] : upsertMessage(list, msg);
}

const uniq = (xs: string[]) => Array.from(new Set(xs));

export function projectReducer(state: ProjectState, action: Action): ProjectState {
  switch (action.event) {
    case "local.threads":
      // Coming back to the page mid-turn: the thread list is what says a turn is under way.
      return {
        ...state,
        threads: action.data,
        runningThreadId: action.data.find((t) => t.status === "running")?.id ?? state.runningThreadId,
      };
    case "queue.changed":
      return {
        ...state,
        queue: action.data.items,
        queuePaused: action.data.paused,
        queuedThreadId: action.data.items[0]?.threadId ?? null,
      };
    case "local.messages":
      return { ...state, messages: { ...state.messages, [action.data.threadId]: action.data.messages } };
    case "local.budget":
    case "budget":
      return { ...state, budget: action.data };
    case "thread.updated":
      return { ...state, threads: upsertThread(state.threads, action.data) };
    case "turn.queued":
      return {
        ...state,
        queuedThreadId: action.data.threadId,
        threads: setThreadStatus(state.threads, action.data.threadId, "queued"),
      };
    case "turn.started": {
      const { threadId } = action.data;
      return {
        ...state,
        runningThreadId: threadId,
        queuedThreadId: state.queuedThreadId === threadId ? null : state.queuedThreadId,
        changedFiles: [],
        threads: setThreadStatus(state.threads, threadId, "running"),
      };
    }
    case "message.created": {
      const m = action.data;
      return {
        ...state,
        messages: { ...state.messages, [m.threadId]: upsertMessage(state.messages[m.threadId] ?? [], m) },
      };
    }
    case "message.part": {
      const { threadId, messageId, index, part } = action.data;
      return {
        ...state,
        messages: {
          ...state.messages,
          [threadId]: upsertPart(state.messages[threadId] ?? [], threadId, messageId, index, part),
        },
      };
    }
    case "turn.finished": {
      const { threadId, commitSha, changedFiles } = action.data;
      return {
        ...state,
        runningThreadId: state.runningThreadId === threadId ? null : state.runningThreadId,
        changedFiles: uniq(changedFiles),
        lastCommitSha: commitSha ?? state.lastCommitSha,
        threads: setThreadStatus(state.threads, threadId, "idle"),
        historyVersion: state.historyVersion + 1,
        filesVersion: state.filesVersion + 1,
      };
    }
    case "files.changed":
      return {
        ...state,
        changedFiles: state.runningThreadId ? uniq([...state.changedFiles, ...action.data.paths]) : state.changedFiles,
        filesVersion: state.filesVersion + 1,
      };
    case "publish.checking":
      return { ...state, publishCheck: action.data, publishFailure: null };
    case "project.published":
      return { ...state, publishCheck: null, published: action.data, publishFailure: null };
    case "publish.failed":
      return { ...state, publishCheck: null, publishFailure: action.data };
    case "ping":
      return state;
    default:
      // The stream carries events this reducer has no use for (whatever the control
      // plane adds later); they must not wipe the state.
      return state;
  }
}
