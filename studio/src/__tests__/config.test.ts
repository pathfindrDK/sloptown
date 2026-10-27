import { describe, expect, it } from "vitest";
import { getConfig, loadConfig } from "../lib/config";

const respond = (body: unknown, ok = true) =>
  (async () => ({ ok, json: async () => body }) as Response) as unknown as typeof fetch;

describe("runtime config", () => {
  it("takes Miira origins from /api/config and strips trailing slashes", async () => {
    const c = await loadConfig(respond({ miiraOrigins: ["https://miira.dk/", "https://test.miira.dk"] }));
    expect(c.miiraOrigins).toEqual(["https://miira.dk", "https://test.miira.dk"]);
    expect(c.miiraOrigin).toBe("https://miira.dk");
    expect(getConfig()).toEqual(c);
  });

  it("keeps the previous values when the server is unreachable or says nothing", async () => {
    const before = await loadConfig(respond({ miiraOrigins: ["https://miira.dk"] }));
    expect(await loadConfig(respond({}, false))).toEqual(before);
    expect(await loadConfig(respond({ miiraOrigins: [] }))).toEqual(before);
    const failing = (async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    expect(await loadConfig(failing)).toEqual(before);
  });
});
