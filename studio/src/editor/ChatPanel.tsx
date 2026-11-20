import { Suspense, lazy, useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import {
  Archive,
  ArchiveRestore,
  ChevronDown,
  ArrowUp,
  ChevronRight,
  Clock,
  FileArchive,
  FileText,
  MoreHorizontal,
  Paperclip,
  Pencil,
  Plus,
  Square,
  Terminal,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { LoadingTiles } from "../components/Loading";
import { imageForAi } from "../lib/imageForAi";
import { QueuedMessages } from "./QueuedMessages";
import { ApiError, type ImageAttachment, type Message, type Part, type QueueItem, type Thread, type Upload } from "../api/types";
import type { Action, ProjectState } from "../lib/projectState";
import { useT } from "../lib/i18n";
import { Menu } from "../components/Menu";
import { Modal } from "../components/Modal";

// Files attached in the chat go to the project's hidden .uploads/ (a .zip is unpacked) and the
// AI is told where they are. Limits match the control plane's.
export const UPLOAD_MAX_BYTES = 25 * 1024 * 1024;
export const MAX_UPLOADS = 5;
const ATTACH_ACCEPT = "image/*,.zip,application/zip,.txt,.md,.csv,.json,.html,.htm,.css,.js,.ts,.tsx,.py,.ino,.svg,.xml";
type Attached = { key: string; name: string; upload?: Upload };
// An image goes to the AI as a data URL and is also uploaded, so sending it puts it in the
// app's public library; one that fails to upload still goes to the AI.
type AttachedImage = ImageAttachment & { key: string; upload?: Upload; failed?: boolean };
// The image types the AI can look at; other images (e.g. SVG) are attached as files.
const VISION_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

type Props = {
  projectId: string;
  state: ProjectState;
  dispatch: (a: Action) => void;
  activeThreadId: string | null;
  /** null = a fresh conversation; its thread is created on the first send. */
  setActiveThreadId: (id: string | null) => void;
  draft: string | null;
  clearDraft: () => void;
  blocked: boolean;
  onBlocked: () => void;
};

export function ChatPanel(props: Props) {
  const { projectId, state, dispatch, activeThreadId, setActiveThreadId } = props;
  const t = useT();
  const [showArchived, setShowArchived] = useState(false);
  const [renaming, setRenaming] = useState<Thread | null>(null);
  const [queueBusy, setQueueBusy] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);

  const threads = state.threads.filter((th) => showArchived || !th.archived);
  const active = state.threads.find((th) => th.id === activeThreadId) ?? null;

  useEffect(() => {
    if (!activeThreadId || state.messages[activeThreadId]) return;
    api.messages(activeThreadId).then((messages) => dispatch({ event: "local.messages", data: { threadId: activeThreadId, messages } }));
  }, [activeThreadId, state.messages, dispatch]);

  // The queue is the control plane's; these only ask it to change and show what failed.
  // The new queue arrives as queue.changed for every open tab, this one included.
  const changeQueue = async (call: () => Promise<unknown>) => {
    setQueueBusy(true);
    setQueueError(null);
    try {
      await call();
    } catch (e) {
      if (e instanceof ApiError && e.code === "not_queued") return;
      setQueueError(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setQueueBusy(false);
    }
  };

  // Threads are created lazily on the first send, so "Ny tråd" just clears the view.
  const newThread = () => setActiveThreadId(null);

  const createThread = async (firstMessage: string) => {
    const title = firstMessage.replace(/\s+/g, " ").slice(0, 60);
    const th = await api.createThread(projectId, title);
    dispatch({ event: "thread.updated", data: th });
    dispatch({ event: "local.messages", data: { threadId: th.id, messages: [] } });
    setActiveThreadId(th.id);
    return th;
  };

  const setArchived = async (th: Thread, archived: boolean) => {
    dispatch({ event: "thread.updated", data: await api.updateThread(th.id, { archived }) });
  };

  return (
    <div className="chat">
      <div className="chat__threads">
        <div className="chat__threads-head">
          <h3 className="chat__threads-label" id="chat-threads-label">
            {t("chat.threads")}
          </h3>
          <label className="check">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            {t("chat.showArchived")}
          </label>
          <button className="btn btn--3d btn--small" onClick={newThread}>
            <Plus size={14} /> {t("chat.newThread")}
          </button>
        </div>
        <ul className="thread-list" role="listbox" aria-labelledby="chat-threads-label">
          {threads.map((th) => (
            <li
              key={th.id}
              role="option"
              aria-selected={th.id === activeThreadId}
              className={`thread-list__item${th.id === activeThreadId ? " is-active" : ""}${th.archived ? " is-archived" : ""}`}
              onClick={() => setActiveThreadId(th.id)}
            >
              <ThreadStatus
                status={
                  th.id === state.runningThreadId ? "running" : state.queue.some((q) => q.threadId === th.id) ? "queued" : "idle"
                }
              />
              <span className="thread-list__title">{th.title}</span>
              <Menu
                label={th.title}
                trigger={<MoreHorizontal size={14} />}
                items={[
                  { label: t("chat.rename"), icon: <Pencil size={14} />, onSelect: () => setRenaming(th) },
                  th.archived
                    ? { label: t("chat.unarchive"), icon: <ArchiveRestore size={14} />, onSelect: () => setArchived(th, false) }
                    : { label: t("chat.archive"), icon: <Archive size={14} />, onSelect: () => setArchived(th, true) },
                ]}
              />
            </li>
          ))}
        </ul>
      </div>
      <Conversation
        {...props}
        thread={active}
        messages={active ? (state.messages[active.id] ?? []) : []}
        createThread={createThread}
        queue={active ? state.queue.filter((m) => m.threadId === active.id) : []}
        queueBusy={queueBusy}
        queueError={queueError}
        onSendNow={(m) => changeQueue(() => api.sendNow(m.id))}
        onEditQueued={(m, text) =>
          changeQueue(() => (text.trim() ? api.editQueued(m.id, text.trim()) : api.deleteQueued(m.id)))
        }
        onDeleteQueued={(m) => changeQueue(() => api.deleteQueued(m.id))}
      />
      {renaming && (
        <RenameModal
          thread={renaming}
          onClose={() => setRenaming(null)}
          onSave={async (title) => {
            dispatch({ event: "thread.updated", data: await api.updateThread(renaming.id, { title }) });
            setRenaming(null);
          }}
        />
      )}
    </div>
  );
}

function ThreadStatus({ status }: { status: Thread["status"] }) {
  if (status === "running")
    return (
      <span className="thread-list__busy" role="img" aria-label="running">
        <LoadingTiles size="xs" />
      </span>
    );
  if (status === "queued") return <Clock size={14} className="muted" aria-label="queued" />;
  return <span className="thread-list__dot" aria-hidden />;
}

function RenameModal({ thread, onClose, onSave }: { thread: Thread; onClose: () => void; onSave: (t: string) => void }) {
  const t = useT();
  const [title, setTitle] = useState(thread.title);
  return (
    <Modal
      title={t("chat.rename")}
      onClose={onClose}
      actions={
        <>
          <button className="btn btn--3d" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className="btn btn--3d btn--primary" onClick={() => title.trim() && onSave(title.trim())}>
            {t("common.save")}
          </button>
        </>
      }
    >
      <input
        className="input"
        value={title}
        autoFocus
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && title.trim() && onSave(title.trim())}
      />
    </Modal>
  );
}

function Conversation({
  projectId,
  state,
  thread,
  messages,
  draft,
  clearDraft,
  blocked,
  onBlocked,
  createThread,
  queue,
  queueBusy,
  queueError,
  onSendNow,
  onEditQueued,
  onDeleteQueued,
}: Props & {
  thread: Thread | null;
  messages: Message[];
  createThread: (firstMessage: string) => Promise<Thread>;
  queue: QueueItem[];
  queueBusy: boolean;
  queueError: string | null;
  onSendNow: (m: QueueItem) => void;
  onEditQueued: (m: QueueItem, text: string) => void;
  onDeleteQueued: (m: QueueItem) => void;
}) {
  const t = useT();
  const [text, setText] = useState("");
  const [images, setImages] = useState<AttachedImage[]>([]);
  const [attached, setAttached] = useState<Attached[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (draft) {
      setText(draft);
      clearDraft();
    }
  }, [draft, clearDraft]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Grow the textarea with its content, up to the CSS max-height.
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  const running = !!thread && state.runningThreadId === thread.id;
  const otherRunning = !!state.runningThreadId && !running;
  const busy = !!state.runningThreadId;

  const uploading = attached.some((a) => !a.upload) || images.some((i) => !i.upload && !i.failed);

  const uploadFile = async (f: File, key: string) => {
    try {
      const up = await api.upload(projectId, f);
      setAttached((cur) => cur.map((a) => (a.key === key ? { ...a, upload: up } : a)));
    } catch (e) {
      setAttached((cur) => cur.filter((a) => a.key !== key));
      setError(e instanceof Error ? e.message : t("common.error"));
    }
  };

  const uploadImage = async (f: File, key: string) => {
    try {
      const up = await api.upload(projectId, f);
      setImages((cur) => cur.map((i) => (i.key === key ? { ...i, upload: up } : i)));
    } catch (e) {
      setImages((cur) => cur.map((i) => (i.key === key ? { ...i, failed: true } : i)));
      setError(e instanceof Error ? e.message : t("common.error"));
    }
  };

  const addFiles = async (files: FileList | File[]) => {
    setError(null);
    const all = Array.from(files);
    const isImage = (f: File) => VISION_TYPES.includes(f.type);
    const others = all.filter((f) => !isImage(f));
    const room = MAX_UPLOADS - attached.length;
    if (others.length > room) setError(t("chat.upload.tooMany"));
    for (const f of others.slice(0, Math.max(0, room))) {
      if (f.size > UPLOAD_MAX_BYTES) {
        setError(t("chat.upload.tooLarge"));
        continue;
      }
      const key = `${Date.now()}-${Math.random()}`;
      setAttached((cur) => [...cur, { key, name: f.name }]);
      void uploadFile(f, key);
    }
    const imgs = all.filter(isImage).slice(0, Math.max(0, 4 - images.length));
    const read: AttachedImage[] = await Promise.all(
      imgs.map(async (f) => ({ key: `${Date.now()}-${Math.random()}`, ...(await imageForAi(f)) })),
    );
    setImages((cur) => [...cur, ...read].slice(0, 4));
    read.forEach((img, i) => void uploadImage(imgs[i], img.key));
  };

  const send = async () => {
    if (!text.trim() || sending || uploading) return;
    if (blocked) return onBlocked();
    setSending(true);
    setError(null);
    try {
      const th = thread ?? (await createThread(text.trim()));
      // While the AI works the control plane queues the message (and keeps it if the page closes).
      const ids = [...images, ...attached].flatMap((a) => (a.upload ? [a.upload.id] : []));
      const sent = images.map(({ mime, dataUrl }) => ({ mime, dataUrl }));
      await api.send(th.id, text.trim(), sent.length ? sent : undefined, ids.length ? ids : undefined);
      setText("");
      setImages([]);
      setAttached([]);
    } catch (e) {
      if (e instanceof ApiError && e.code === "budget_blocked") onBlocked();
      else if (e instanceof ApiError && e.code === "queue_full") setError(t("chat.queueFull"));
      else setError(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="conversation">
      <div className="conversation__messages" ref={scroller}>
        {messages.length === 0 && <p className="empty">{t("chat.empty")}</p>}
        {messages.map((m) => (
          <MessageView key={m.id} message={m} />
        ))}
        {running && (
          <div className="conversation__status">
            <LoadingTiles size="sm" /> {t("chat.running")}
          </div>
        )}
      </div>
      <div
        className="composer"
        onDragOver={(e: DragEvent) => e.preventDefault()}
        onDrop={(e: DragEvent) => {
          e.preventDefault();
          void addFiles(e.dataTransfer.files);
        }}
      >
        {otherRunning && <div className="composer__note">{t("chat.otherRunning")}</div>}
        <QueuedMessages
          messages={queue}
          paused={state.queuePaused}
          busy={queueBusy}
          onSendNow={onSendNow}
          onEdit={onEditQueued}
          onDelete={onDeleteQueued}
        />
        {queueError && <div className="composer__error">{queueError}</div>}
        <form
          className="composer__shell"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          {images.length > 0 && (
            <div className="composer__images">
              {images.map((img) => (
                <div className="composer__thumb" key={img.key}>
                  <img src={img.dataUrl} alt="" />
                  {!img.upload && !img.failed && (
                    <span className="composer__thumb-busy" aria-label={t("chat.upload.uploading")}>
                      <LoadingTiles size="xs" />
                    </span>
                  )}
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={t("common.close")}
                    onClick={() => setImages((xs) => xs.filter((x) => x.key !== img.key))}
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {attached.length > 0 && (
            <ul className="composer__files" aria-label={t("chat.upload.list")}>
              {attached.map((a) => (
                <li className="file-chip" key={a.key}>
                  {a.name.toLowerCase().endsWith(".zip") ? <FileArchive size={14} /> : <Paperclip size={14} />}
                  <span className="file-chip__name">{a.name}</span>
                  <span className="file-chip__meta">
                    {a.upload ? (
                      a.upload.kind === "zip" ? `${a.upload.fileCount} ${t("chat.upload.files")}` : null
                    ) : (
                      <>
                        <LoadingTiles size="xs" /> {t("chat.upload.uploading")}
                      </>
                    )}
                  </span>
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`${t("chat.upload.remove")} ${a.name}`}
                    onClick={() => setAttached((xs) => xs.filter((x) => x.key !== a.key))}
                  >
                    <X size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <textarea
            ref={input}
            className="composer__input"
            value={text}
            rows={1}
            autoFocus
            placeholder={t(busy ? "chat.placeholderBusy" : "chat.placeholder")}
            onChange={(e) => setText(e.target.value)}
            onPaste={(e: ClipboardEvent) => {
              if (e.clipboardData.files.length) {
                e.preventDefault();
                void addFiles(e.clipboardData.files);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="composer__row">
            <input
              ref={fileInput}
              type="file"
              accept={ATTACH_ACCEPT}
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files) void addFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className="composer__icon-btn"
              onClick={() => fileInput.current?.click()}
              title={t("chat.attach")}
              aria-label={t("chat.attach")}
            >
              <Plus size={20} />
            </button>
            {running && thread && !text.trim() ? (
              <button
                type="button"
                className="composer__send composer__send--stop"
                onClick={() => api.stop(thread.id)}
                title={t("chat.stop")}
                aria-label={t("chat.stop")}
              >
                <Square size={14} fill="currentColor" />
              </button>
            ) : (
              <button
                type="submit"
                className="composer__send"
                disabled={!text.trim() || sending || uploading}
                title={t(busy ? "chat.queue.add" : "chat.send")}
                aria-label={t(busy ? "chat.queue.add" : "chat.send")}
              >
                <ArrowUp size={18} />
              </button>
            )}
          </div>
        </form>
        {error && <div className="composer__error">{error}</div>}
        <div className="composer__hint">{t("chat.hint")}</div>
      </div>
    </div>
  );
}

function MessageView({ message }: { message: Message }) {
  return (
    <div className={`msg msg--${message.role}`}>
      {message.role === "user" && message.authorName && <div className="msg__author">{message.authorName}</div>}
      {message.parts.map((p, i) => (
        <PartView key={i} part={p} markdown={message.role === "assistant"} />
      ))}
    </div>
  );
}

// react-markdown + remark-gfm are ~170 kB: load them with the first AI reply.
const Markdown = lazy(() => import("../components/Markdown").then((m) => ({ default: m.Markdown })));

/** `markdown`: the AI writes Markdown; a teacher's own text is shown as typed. */
function PartView({ part, markdown }: { part: Part; markdown: boolean }) {
  const [open, setOpen] = useState(false);
  switch (part.type) {
    case "text":
      if (!part.text) return null;
      const plain = <div className="msg__text">{part.text}</div>;
      return markdown ? (
        <Suspense fallback={plain}>
          <Markdown text={part.text} />
        </Suspense>
      ) : (
        plain
      );
    case "image":
      return <img className="msg__image" src={part.url} alt="" />;
    case "upload":
      return <UploadChip part={part} />;
    case "tool": {
      const Icon = /bash|run|shell|command/i.test(part.tool) ? Terminal : FileText;
      return (
        <div className={`tool tool--${part.status}`}>
          <button className="tool__head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {part.status === "running" ? <LoadingTiles size="xs" /> : <Icon size={14} />}
            <span className="tool__title">{part.title || part.tool}</span>
          </button>
          {open && (
            <pre className="tool__body">
              {part.input !== undefined && `${JSON.stringify(part.input, null, 2)}\n`}
              {part.output}
            </pre>
          )}
        </div>
      );
    }
  }
}

function UploadChip({ part }: { part: Extract<Part, { type: "upload" }> }) {
  const t = useT();
  const zip = part.name.toLowerCase().endsWith(".zip");
  return (
    <div className="file-chip file-chip--sent">
      {zip ? <FileArchive size={14} /> : <Paperclip size={14} />}
      <span className="file-chip__name">{part.name}</span>
      {zip && <span className="file-chip__meta">{`${part.fileCount} ${t("chat.upload.files")}`}</span>}
      {part.public && (
        <span className="file-chip__meta" title={t("chat.upload.publicTitle").replace("{path}", part.public)}>
          {t("chat.upload.public")}
        </span>
      )}
    </div>
  );
}
