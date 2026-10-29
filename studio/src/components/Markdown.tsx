// The AI's chat replies are Markdown (bold, lists, code, tables). Rendered to React
// elements only: raw HTML in the text stays text, javascript: URLs are dropped by
// react-markdown's default urlTransform.
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  // The studio runs inside Miira's iframe: never navigate it away.
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  // No remote images in the chat (they would load from anywhere); show the alt text.
  img: ({ alt }) => <>{alt}</>,
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
