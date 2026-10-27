import { describe, expect, it } from "vitest";
import { changedLines } from "../lib/lineDiff";
import { buildTree } from "../lib/fileTree";
import { parsePreviewError } from "../editor/Preview";

describe("changedLines", () => {
  it("marks inserted and modified lines in the new text", () => {
    expect([...changedLines("a\nb\nc", "a\nB\nc\nd")]).toEqual([2, 4]);
  });
  it("returns nothing for identical text and nothing for pure deletions", () => {
    expect(changedLines("a\nb", "a\nb").size).toBe(0);
    expect(changedLines("a\nb\nc", "a\nc").size).toBe(0);
  });
  it("treats everything as new when there was no previous file", () => {
    expect([...changedLines("", "x\ny")]).toEqual([1, 2]);
  });
});

describe("buildTree", () => {
  it("nests, sorts dirs first, hides platform dirs and propagates changed", () => {
    const tree = buildTree(
      [
        { path: "web/src/App.tsx", size: 1, locked: false, changed: false },
        { path: "AGENTS.md", size: 1, locked: true, changed: false },
        { path: ".git/HEAD", size: 1, locked: false, changed: false },
        { path: "web/node_modules/x.js", size: 1, locked: false, changed: false },
      ],
      new Set(["web/src/App.tsx"]),
    );
    expect(tree.map((n) => n.name)).toEqual(["web", "AGENTS.md"]);
    expect(tree[0].changed).toBe(true);
    expect(tree[0].children[0].children[0].path).toBe("web/src/App.tsx");
  });
});

describe("parsePreviewError", () => {
  it("accepts only vibe:error messages", () => {
    expect(parsePreviewError({ type: "vibe:error", message: "boom", stack: "at x" })?.message).toBe("boom");
    expect(parsePreviewError({ type: "other", message: "x" })).toBeNull();
    expect(parsePreviewError({ type: "vibe:error" })).toBeNull();
  });
});
