// Types mirror docs/internal-api.md. Change that file first.

export type Role = "owner" | "editor" | "viewer";

export type Me = {
  user: { sub: string; name: string; email: string; role: string; orgId: string; isSystemAdmin: boolean };
  termsAcceptedAt: string | null;
};

export type Terms = { version: string; html: string };

/** "webapp": the template app with preview and publish. "scratch" ("fra bunden"):
 *  an empty project for code that is never served on the web, e.g. Arduino. */
export type ProjectKind = "webapp" | "scratch";

export type Project = {
  id: string;
  title: string;
  kind: ProjectKind;
  slug: string;
  ownerSub: string;
  myRole: Role;
  status: "starting" | "ready" | "stopped" | "error";
  previewUrl: string | null;
  appUrl: string | null;
  publishedSha: string | null;
  /** The published app opens without a Miira login. */
  public: boolean;
  description: string;
  /** Design system and colour palette ids (GET /api/design). null: a "fra bunden"
   *  project, or a webapp from before design systems (Miira look, no palette yet). */
  style: string | null;
  palette: string | null;
  createdAt: string;
  updatedAt: string;
};

export type Localized = { da: string; en: string };
export type PaletteColours = {
  primary: string;
  onPrimary: string;
  bg: string;
  surface: string;
  text: string;
  muted: string;
  border: string;
};
export type DesignStyle = { id: string; name: Localized; description: Localized; package?: string };
export type DesignPalette = { id: string; name: Localized; light: PaletteColours; dark: PaletteColours };
/** GET /api/design: template-design/catalog.json. */
export type DesignCatalog = { styles: DesignStyle[]; palettes: DesignPalette[] };

export type BudgetState = "ok" | "warning" | "nerfed" | "blocked";
export type BucketStatus = {
  usedTokens: number;
  nerfLimitTokens: number | null;
  hardLimitTokens: number | null;
  state: BudgetState;
  resetsAt: string;
};
export type BudgetStatus = { enabled: boolean; build: BucketStatus; app: BucketStatus };

export type Thread = {
  id: string;
  projectId: string;
  title: string;
  ownerSub: string;
  createdAt: string;
  archived: boolean;
  status: "idle" | "running" | "queued";
};

export type Part =
  | { type: "text"; text: string }
  | { type: "tool"; tool: string; title: string; status: "running" | "done" | "error"; input?: unknown; output?: string }
  | { type: "image"; mime: string; url: string };

export type Message = {
  id: string;
  threadId: string;
  role: "user" | "assistant";
  authorName?: string;
  parts: Part[];
  createdAt: string;
};

export type FileEntry = { path: string; size: number; locked: boolean; changed: boolean };
export type FileContent = { path: string; content: string; locked: boolean; language: string };

export type Commit = {
  sha: string;
  message: string;
  authorName: string;
  threadId: string | null;
  threadTitle: string | null;
  createdAt: string;
  files: string[];
};
export type History = { commits: Commit[]; publishedSha: string | null };
export type Diff = { sha: string; files: { path: string; status: string; patch: string }[] };

export type LaunchResult = { token: string; user: Me["user"]; fileId: string; fileRole: Role };

export type ImageAttachment = { mime: string; dataUrl: string };

/** A message waiting for the project's AI (the control plane keeps the queue). */
export type QueueItem = {
  id: string;
  threadId: string;
  text: string;
  imageCount: number;
  authorSub: string;
  queuedAt: string | null;
};

/** `paused`: Stop was pressed while messages waited; the next send resumes. */
export type QueueState = { items: QueueItem[]; paused: boolean };

export type ProjectEvent =
  | { event: "turn.started"; data: { threadId: string; turnId: string } }
  | { event: "message.part"; data: { threadId: string; messageId: string; index: number; part: Part } }
  | { event: "message.created"; data: Message }
  | {
      event: "turn.finished";
      data: {
        threadId: string;
        turnId: string;
        commitSha: string | null;
        changedFiles: string[];
        status: "done" | "stopped" | "error";
      };
    }
  | { event: "turn.queued"; data: { threadId: string; turnId: string } }
  | { event: "queue.changed"; data: QueueState }
  | { event: "files.changed"; data: { paths: string[] } }
  | { event: "budget"; data: BudgetStatus }
  | { event: "thread.updated"; data: Thread }
  | { event: "ping"; data: Record<string, never> };

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public body: Record<string, unknown> = {},
  ) {
    super(message);
  }
}
