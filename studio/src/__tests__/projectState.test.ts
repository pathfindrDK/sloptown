import { describe, expect, it } from "vitest";
import { SseParser } from "../api/sse";
import type { Thread } from "../api/types";
import { initialProjectState, projectReducer } from "../lib/projectState";

const thread = (id: string): Thread => ({ id, projectId: "p", title: id, ownerSub: "u", createdAt: "", archived: false, status: "idle" });

describe("SseParser", () => {
  it("splits events across chunk boundaries and joins multi-line data", () => {
    const p = new SseParser();
    expect(p.push("event: ping\ndata: {}\n\nevent: message.part\nda")).toEqual([{ event: "ping", data: "{}" }]);
    expect(p.push('ta: {"a":\ndata: 1}\n\n')).toEqual([{ event: "message.part", data: '{"a":\n1}' }]);
  });
  it("ignores comments and handles CRLF", () => {
    const p = new SseParser();
    expect(p.push(": keepalive\r\n\r\nevent: x\r\ndata: 1\r\n\r\n")).toEqual([{ event: "x", data: "1" }]);
  });
});

describe("projectReducer", () => {
  const base = projectReducer(initialProjectState, { event: "local.threads", data: [thread("a"), thread("b")] });

  it("upserts streamed parts by index and creates the message on first part", () => {
    let s = projectReducer(base, { event: "message.part", data: { threadId: "a", messageId: "m", index: 1, part: { type: "text", text: "he" } } });
    expect(s.messages.a[0].parts).toEqual([{ type: "text", text: "" }, { type: "text", text: "he" }]);
    s = projectReducer(s, { event: "message.part", data: { threadId: "a", messageId: "m", index: 1, part: { type: "text", text: "hello" } } });
    s = projectReducer(s, { event: "message.part", data: { threadId: "a", messageId: "m", index: 0, part: { type: "tool", tool: "read", title: "Læste", status: "done" } } });
    expect(s.messages.a).toHaveLength(1);
    expect(s.messages.a[0].parts.map((p) => p.type)).toEqual(["tool", "text"]);
    expect((s.messages.a[0].parts[1] as { text: string }).text).toBe("hello");
  });

  it("tracks running, queued and finished turns", () => {
    let s = projectReducer(base, { event: "turn.started", data: { threadId: "a", turnId: "1" } });
    s = projectReducer(s, { event: "turn.queued", data: { threadId: "b", turnId: "2" } });
    expect(s.runningThreadId).toBe("a");
    expect(s.queuedThreadId).toBe("b");
    expect(s.threads.find((t) => t.id === "b")?.status).toBe("queued");
    s = projectReducer(s, { event: "files.changed", data: { paths: ["x.ts"] } });
    expect(s.changedFiles).toEqual(["x.ts"]);
    s = projectReducer(s, { event: "turn.finished", data: { threadId: "a", turnId: "1", commitSha: "abc", changedFiles: ["x.ts", "y.ts"], status: "done" } });
    expect(s.runningThreadId).toBeNull();
    expect(s.lastCommitSha).toBe("abc");
    expect(s.changedFiles).toEqual(["x.ts", "y.ts"]);
    expect(s.historyVersion).toBe(1);
    s = projectReducer(s, { event: "turn.started", data: { threadId: "b", turnId: "2" } });
    expect(s.queuedThreadId).toBeNull();
    expect(s.changedFiles).toEqual([]);
  });
});

describe("events the reducer does not handle", () => {
  it("leave the state as it was (project.published after a publish)", () => {
    const next = projectReducer(initialProjectState, { event: "project.published", data: { publishedSha: "a".repeat(40) } } as never);
    expect(next).toBe(initialProjectState);
  });
});
