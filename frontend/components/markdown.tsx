import type { CSSProperties } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/** Renders trusted-ish markdown (ticket descriptions, comments, chat bubbles) with
 * Tailwind Typography styling. `invert` forces light-on-dark prose colors for use
 * on a colored bubble background, independent of the site's own dark-mode toggle.
 *
 * `remark-breaks` turns single newlines into `<br>` — agent replies often use plain
 * `\n` line breaks instead of markdown list syntax, and CommonMark would otherwise
 * collapse them into one inline paragraph. */
export function Markdown({
  children,
  className,
  invert = false,
  style,
}: {
  children: string;
  className?: string;
  invert?: boolean;
  /** Escape hatch for callers embedding Markdown in a plain-text bubble that
   * needs pixel-exact font matching — Typography's `prose-*` size classes set
   * their own font-size on this element, so a mismatched className alone
   * isn't reliable; inline style always wins over it. */
  style?: CSSProperties;
}) {
  return (
    <div
      className={cn(
        "prose prose-sm max-w-none break-words prose-p:my-1 prose-pre:my-1.5 prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-headings:my-1.5 prose-headings:font-semibold",
        invert ? "prose-invert" : "dark:prose-invert",
        className,
      )}
      style={style}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{children}</ReactMarkdown>
    </div>
  );
}
