import assert from "node:assert/strict";
import { test } from "node:test";

import { BudgetExceededError, VibeClient, hasRole, installErrorBridge } from "../dist/index.js";

function mockFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  return calls;
}

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

test("hasRole ranks roles", () => {
  assert.equal(hasRole({ id: "a", name: "", role: "editor", classes: [] }, "viewer"), true);
  assert.equal(hasRole({ id: "a", name: "", role: "viewer", classes: [] }, "editor"), false);
  assert.equal(hasRole(null, "viewer"), false);
});

test("client hits /api/_vibe routes", async () => {
  const calls = mockFetch((url) => (url.endsWith("/me") ? json(200, { id: "u1", name: "Ali", role: "viewer", classes: [] }) : json(200, { text: "svar" })));
  const c = new VibeClient("/api");
  assert.equal((await c.me()).id, "u1");
  assert.equal(await c.chat("hej"), "svar");
  assert.equal(calls[0].url, "/api/_vibe/me");
  assert.deepEqual(JSON.parse(calls[1].init.body).messages, [{ role: "user", content: "hej" }]);
});

test("429 becomes BudgetExceededError with retryAt", async () => {
  mockFetch(() => json(429, { error: "budget_exceeded", message: "Brugt op", retryAt: "2026-11-02T00:00:00+01:00" }));
  await assert.rejects(new VibeClient().chat("x"), (e) => e instanceof BudgetExceededError && e.retryAt.toISOString() === "2026-11-01T23:00:00.000Z");
});

test("stream parses SSE deltas", async () => {
  const sse = 'event: delta\ndata: {"text":"He"}\n\nevent: delta\ndata: {"text":"j"}\n\nevent: done\ndata: {}\n\n';
  mockFetch(() => new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } }));
  const parts = [];
  assert.equal(await new VibeClient().stream("x", (t) => parts.push(t)), "Hej");
  assert.deepEqual(parts, ["He", "j"]);
});

function fakeWindow(hostname, framed = true) {
  const listeners = {};
  const posted = [];
  const win = {
    location: { hostname },
    document: { referrer: "https://studio.example/embed/p1" },
    console: { error: () => {} },
    addEventListener: (t, fn) => (listeners[t] = fn),
    removeEventListener: (t) => delete listeners[t],
  };
  win.parent = framed ? { postMessage: (m, o) => posted.push([m, o]) } : win;
  return { win, listeners, posted };
}

test("error bridge posts vibe:error from preview hosts only", () => {
  const { win, listeners, posted } = fakeWindow("preview-quiz.apps.example");
  const off = installErrorBridge(win);
  const err = new Error("boom");
  listeners.error({ message: "boom", error: err });
  listeners.unhandledrejection({ reason: new Error("nope") });
  win.console.error("bad", 1);
  assert.equal(posted.length, 3);
  assert.deepEqual(posted[0][0].type, "vibe:error");
  assert.equal(posted[0][0].message, "boom");
  assert.ok(posted[0][0].stack.includes("boom"));
  assert.equal(posted[0][1], "https://studio.example");
  assert.match(posted[1][0].message, /Unhandled rejection: nope/);
  assert.equal(posted[2][0].message, "bad 1");
  off();
  assert.equal(listeners.error, undefined);

  const pub = fakeWindow("quiz.apps.example");
  installErrorBridge(pub.win);
  assert.equal(pub.listeners.error, undefined);
});
