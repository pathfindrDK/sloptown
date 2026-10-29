// "Visning" in the code view: a file rendered instead of its source.
// - HTML in a "fra bunden" project's working tree: the page on its preview host, so
//   its relative CSS, JS and images load. Elsewhere (a webapp's HTML, an old
//   version): the text in a sandboxed srcdoc frame with an opaque origin.
// - Markdown: the chat's renderer (no raw HTML, no remote images).
// - Images, audio, video, PDF: the bytes from /raw, shown from a blob URL.
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { Loading } from "../components/Loading";
import { Markdown } from "../components/Markdown";
import { isMedia, type PreviewKind } from "../lib/filePreview";
import { useT } from "../lib/i18n";

type Props = {
  projectId: string;
  path: string;
  sha?: string;
  kind: PreviewKind;
  /** The file's text (HTML, Markdown, SVG), when loaded. */
  content: string | null;
  /** Serve HTML from the preview host (a "fra bunden" project's current files). */
  previewHost: boolean;
  reloadSignal: number;
};

export function FilePreview({ projectId, path, sha, kind, content, previewHost, reloadSignal }: Props) {
  const t = useT();
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hosted = kind === "html" && previewHost && !sha;

  useEffect(() => {
    let live = true;
    let blobUrl: string | null = null;
    setError(null);
    setSrc(null);
    if (hosted) {
      // The one-time session URL sets the preview cookie, then redirects to the file.
      api
        .previewSession(projectId)
        .then((r) => {
          const u = new URL(r.url, window.location.href);
          u.searchParams.set("next", `/${path.split("/").map(encodeURIComponent).join("/")}`);
          if (live) setSrc(u.toString());
        })
        .catch(() => live && setError(t("code.previewFailed")));
    } else if (isMedia(kind)) {
      api
        .rawFile(projectId, path, sha)
        .then((blob) => {
          blobUrl = URL.createObjectURL(blob);
          if (live) setSrc(blobUrl);
        })
        .catch((e) => live && setError(t(e instanceof ApiError && e.status === 413 ? "code.tooLarge" : "code.previewFailed")));
    }
    return () => {
      live = false;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [projectId, path, sha, kind, hosted, reloadSignal, t]);

  if (error) return <div className="empty">{error}</div>;

  if (kind === "markdown") {
    return content === null ? <Loading /> : (
      <div className="file-preview file-preview--doc">
        <Markdown text={content} />
      </div>
    );
  }
  if (kind === "html" && !hosted) {
    return content === null ? <Loading /> : (
      <iframe className="file-preview__frame" title={path} srcDoc={content} sandbox="allow-scripts allow-forms allow-modals" />
    );
  }
  if (!src) return <Loading />;
  switch (kind) {
    case "html":
      return (
        <iframe
          key={src}
          className="file-preview__frame"
          title={path}
          src={src}
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads"
        />
      );
    case "image":
      return (
        <div className="file-preview file-preview--media file-preview--checker">
          <img src={src} alt={path} />
        </div>
      );
    case "audio":
      return (
        <div className="file-preview file-preview--media">
          <audio src={src} controls />
        </div>
      );
    case "video":
      return (
        <div className="file-preview file-preview--media">
          <video src={src} controls />
        </div>
      );
    case "pdf":
      return <iframe className="file-preview__frame" title={path} src={src} />;
    default:
      return null;
  }
}
