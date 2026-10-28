import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import {
  Archive,
  ArchiveRestore,
  ChevronDown,
  ArrowUp,
  ChevronRight,
  Clock,
  FileText,
  MoreHorizontal,
  Pencil,
  Plus,
  Square,
  Terminal,
  X,
} from "lucide-react";
import { api } from "../api/client";
import { LoadingTiles } from "../components/Loading";
import { QueuedMessages } from "./QueuedMessages";
import { ApiError, type ImageAttachment, type Message, type Part, type QueueItem, type Thread } from "../api/types";
import type { Action, ProjectState } from "../lib/projectState";
import { useT } from "../lib/i18n";
import { Menu } from "../components/Menu";
import { Modal } from "../components/Modal";

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
          <button className="btn btn--3d btn--small" onClick={newThread}>
            <Plus size={14} /> {t("chat.newThread")}
          </button>
          <label className="check">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            {t("chat.showArchived")}
          </label>
        </div>
        <ul className="thread-list" role="listbox" aria-label={t("side.chat")}>
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
  const [images, setImages] = useState<ImageAttachment[]>([]);
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

  const addFiles = async (files: FileList | File[]) => {
    const imgs = Array.from(files).filter((f) => f.type.startsWith("image/")).slice(0, 4);
    const read = await Promise.all(
      imgs.map(
        (f) =>
          new Promise<ImageAttachment>((resolve, reject) => {
            const r = new FileReader();
            r.onload = () => resolve({ mime: f.type, dataUrl: String(r.result) });
            r.onerror = reject;
            r.readAsDataURL(f);
          }),
      ),
    );
    setImages((cur) => [...cur, ...read].slice(0, 4));
  };

  const send = async () => {
    if (!text.trim() || sending) return;
    if (blocked) return onBlocked();
    setSending(true);
    setError(null);
    try {
      const th = thread ?? (await createThread(text.trim()));
      // While the AI works the control plane queues the message (and keeps it if the page closes).
      await api.send(th.id, text.trim(), images.length ? images : undefined);
      setText("");
      setImages([]);
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
              {images.map((img, i) => (
                <div className="composer__thumb" key={i}>
                  <img src={img.dataUrl} alt="" />
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={t("common.close")}
                    onClick={() => setImages((xs) => xs.filter((_, j) => j !== i))}
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
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
            <input ref={fileInput} type="file" accept="image/*" multiple hidden onChange={(e) => e.target.files && addFiles(e.target.files)} />
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
                disabled={!text.trim() || sending}
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
        <PartView key={i} part={p} />
      ))}
    </div>
  );
}

function PartView({ part }: { part: Part }) {
  const [open, setOpen] = useState(false);
  switch (part.type) {
    case "text":
      return part.text ? <div className="msg__text">{part.text}</div> : null;
    case "image":
      return <img className="msg__image" src={part.url} alt="" />;
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
