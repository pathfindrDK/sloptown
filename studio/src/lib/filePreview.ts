// Which files the code view can also show rendered ("Visning"), and how.
export type PreviewKind = "html" | "markdown" | "image" | "audio" | "video" | "pdf";

const KINDS: Record<string, PreviewKind> = {
  html: "html", htm: "html",
  md: "markdown", markdown: "markdown",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", avif: "image", svg: "image", ico: "image", bmp: "image",
  mp3: "audio", wav: "audio", ogg: "audio", oga: "audio", m4a: "audio", flac: "audio",
  mp4: "video", webm: "video", ogv: "video", mov: "video",
  pdf: "pdf",
};

export function previewKind(path: string): PreviewKind | null {
  return KINDS[path.split(".").pop()?.toLowerCase() ?? ""] ?? null;
}

/** Shown from the file's bytes (GET /raw, a blob URL) rather than its text. */
export function isMedia(kind: PreviewKind | null): kind is "image" | "audio" | "video" | "pdf" {
  return kind === "image" || kind === "audio" || kind === "video" || kind === "pdf";
}

/** A "fra bunden" project's UI: an index.html at the root, served on its preview host. */
export const SCRATCH_UI_ENTRY = "index.html";
