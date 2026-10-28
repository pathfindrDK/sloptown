import { useState } from "react";
import { ArrowUp, ImageIcon, Pencil, Trash2 } from "lucide-react";
import type { QueueItem } from "../api/types";
import { useT } from "../lib/i18n";

type Props = {
  messages: QueueItem[];
  paused: boolean;
  busy: boolean;
  onSendNow: (m: QueueItem) => void;
  onEdit: (m: QueueItem, text: string) => void;
  onDelete: (m: QueueItem) => void;
};

/** The thread's waiting messages, right above the input (as in Cursor). The queue lives in the
 *  control plane, so it is the same in every tab and survives leaving the page. */
export function QueuedMessages({ messages, paused, busy, onSendNow, onEdit, onDelete }: Props) {
  const t = useT();
  const [editing, setEditing] = useState<string | null>(null);
  const [text, setText] = useState("");
  if (!messages.length) return null;

  const save = (m: QueueItem) => {
    if (text.trim() !== m.text) onEdit(m, text);
    setEditing(null);
  };

  return (
    <section className="queue" aria-label={t("chat.queue.title")}>
      <div className="queue__head">
        {t("chat.queue.title")} · {messages.length}
        {paused && <span className="queue__paused">{t("chat.queue.paused")}</span>}
      </div>
      <ul className="queue__list">
        {messages.map((m) => (
          <li key={m.id} className="queue__item">
            {editing === m.id ? (
              <textarea
                className="input queue__edit"
                value={text}
                rows={2}
                autoFocus
                aria-label={t("chat.queue.edit")}
                onChange={(e) => setText(e.target.value)}
                onBlur={() => save(m)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    save(m);
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    setEditing(null);
                  }
                }}
              />
            ) : (
              <span className="queue__text">
                {m.imageCount > 0 && <ImageIcon size={14} aria-label={t("chat.attach")} />}
                {m.text}
              </span>
            )}
            <div className="queue__actions">
              <button
                type="button"
                className="btn btn--small"
                disabled={busy}
                onClick={() => onSendNow(m)}
                title={t("chat.queue.sendNowTitle")}
              >
                <ArrowUp size={14} /> {t("chat.queue.sendNow")}
              </button>
              <button
                type="button"
                className="icon-btn"
                aria-label={t("chat.queue.edit")}
                title={t("chat.queue.edit")}
                onMouseDown={(e) => editing === m.id && e.preventDefault()}
                onClick={() => {
                  setText(m.text);
                  setEditing(m.id);
                }}
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                className="icon-btn"
                aria-label={t("chat.queue.delete")}
                title={t("chat.queue.delete")}
                onClick={() => onDelete(m)}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
