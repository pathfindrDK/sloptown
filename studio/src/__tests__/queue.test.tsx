import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { QueueItem, Thread } from "../api/types";
import { QueuedMessages } from "../editor/QueuedMessages";
import { LocaleProvider } from "../lib/i18n";
import { initialProjectState, projectReducer } from "../lib/projectState";

afterEach(cleanup);

const item = (id: string, text: string, threadId = "t1"): QueueItem => ({
  id,
  threadId,
  text,
  imageCount: 0,
  authorSub: "s-lea",
  queuedAt: null,
});

describe("the queue in the editor state", () => {
  it("follows the control plane's queue.changed", () => {
    const s = projectReducer(initialProjectState, {
      event: "queue.changed",
      data: { items: [item("a", "Gør den blå", "t2"), item("b", "Tilføj point")], paused: true },
    });
    expect(s.queue.map((q) => q.id)).toEqual(["a", "b"]);
    expect(s.queuePaused).toBe(true);
    expect(s.queuedThreadId).toBe("t2");
    expect(projectReducer(s, { event: "queue.changed", data: { items: [], paused: false } }).queuedThreadId).toBeNull();
  });

  it("knows a turn is running when the page is opened mid-turn", () => {
    const thread = (id: string, status: Thread["status"]): Thread => ({
      id,
      projectId: "p",
      title: id,
      ownerSub: "s-lea",
      createdAt: "",
      archived: false,
      status,
    });
    const s = projectReducer(initialProjectState, { event: "local.threads", data: [thread("t1", "idle"), thread("t2", "running")] });
    expect(s.runningThreadId).toBe("t2");
  });
});

describe("QueuedMessages", () => {
  const renderQueue = (messages: QueueItem[]) => {
    const handlers = { onSendNow: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn() };
    render(
      <LocaleProvider locale="da">
        <QueuedMessages messages={messages} paused={false} busy={false} {...handlers} />
      </LocaleProvider>,
    );
    return handlers;
  };

  it("offers send now, edit and delete for each waiting message", () => {
    const m = item("a", "Gør knapperne større");
    const h = renderQueue([m]);
    fireEvent.click(screen.getByRole("button", { name: /Send nu/ }));
    expect(h.onSendNow).toHaveBeenCalledWith(m);
    fireEvent.click(screen.getByRole("button", { name: "Slet" }));
    expect(h.onDelete).toHaveBeenCalledWith(m);

    fireEvent.click(screen.getByRole("button", { name: "Redigér" }));
    const box = screen.getByRole("textbox", { name: "Redigér" });
    fireEvent.change(box, { target: { value: "Gør knapperne blå" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(h.onEdit).toHaveBeenCalledWith(m, "Gør knapperne blå");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("does not save an edit that changed nothing", () => {
    const h = renderQueue([item("a", "Gør knapperne større")]);
    fireEvent.click(screen.getByRole("button", { name: "Redigér" }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Redigér" }), { key: "Enter" });
    expect(h.onEdit).not.toHaveBeenCalled();
  });

  it("shows nothing when the queue is empty", () => {
    renderQueue([]);
    expect(screen.queryByRole("region")).toBeNull();
  });
});
