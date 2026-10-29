import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { FilePreview } from "../editor/FilePreview";
import { isMedia, previewKind } from "../lib/filePreview";
import { LocaleProvider } from "../lib/i18n";

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() }));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const show = (props: Partial<Parameters<typeof FilePreview>[0]> & { path: string }) =>
  render(
    <LocaleProvider locale="da">
      <FilePreview
        projectId="p1"
        kind={previewKind(props.path)!}
        content={null}
        previewHost={false}
        reloadSignal={0}
        {...props}
      />
    </LocaleProvider>,
  );

describe("which files have a preview", () => {
  it("knows HTML, Markdown and media by extension", () => {
    expect(previewKind("index.html")).toBe("html");
    expect(previewKind("README.md")).toBe("markdown");
    expect(previewKind("img/Logo.PNG")).toBe("image");
    expect(previewKind("lyd/klokke.mp3")).toBe("audio");
    expect(previewKind("film.webm")).toBe("video");
    expect(previewKind("opgaver.pdf")).toBe("pdf");
    expect(previewKind("trafiklys.ino")).toBeNull();
    expect(isMedia("image") && !isMedia("markdown")).toBe(true);
  });
});

describe("the file preview", () => {
  it("renders Markdown", () => {
    const { container } = show({ path: "README.md", content: "# Trafiklys\n\nTryk på **knappen**." });
    expect(container.querySelector("strong")?.textContent).toBe("knappen");
  });

  it("shows a webapp's HTML (or an old version) in a sandboxed srcdoc frame without same-origin", () => {
    const { container } = show({ path: "index.html", content: "<h1>Hej</h1>", sha: "abc1234", previewHost: true });
    const frame = container.querySelector("iframe")!;
    expect(frame.getAttribute("srcdoc")).toBe("<h1>Hej</h1>");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });

  it("opens a scratch project's HTML on its preview host, at the file's path", async () => {
    vi.spyOn(api, "previewSession").mockResolvedValue({ url: "https://preview-x.example/_vibe/session?code=c&next=/" });
    const { container } = show({ path: "spil/side 2.html", content: "<h1>Hej</h1>", previewHost: true });
    await waitFor(() => expect(container.querySelector("iframe")).not.toBeNull());
    const src = new URL(container.querySelector("iframe")!.getAttribute("src")!);
    expect(src.origin).toBe("https://preview-x.example");
    expect(src.searchParams.get("code")).toBe("c");
    expect(src.searchParams.get("next")).toBe("/spil/side%202.html");
  });

  it("shows media from a blob of the raw bytes", async () => {
    const raw = vi.spyOn(api, "rawFile").mockResolvedValue(new Blob(["x"], { type: "audio/mpeg" }));
    const { container } = show({ path: "lyd/klokke.mp3", sha: "abc1234" });
    await waitFor(() => expect(container.querySelector("audio")?.getAttribute("src")).toBe("blob:x"));
    expect(raw).toHaveBeenCalledWith("p1", "lyd/klokke.mp3", "abc1234");
  });

  it("says when a file is too big to show", async () => {
    vi.spyOn(api, "rawFile").mockRejectedValue(new ApiError(413, "too_large", "for stor"));
    show({ path: "film.mp4" });
    await act(async () => {});
    expect(screen.getByText(/for stor til at blive vist/)).toBeTruthy();
  });
});
