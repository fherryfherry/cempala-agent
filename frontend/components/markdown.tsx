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
}: {
  children: string;
  className?: string;
  invert?: boolean;
}) {
  return (
    <div
      className={cn(
        "prose prose-sm max-w-none break-words prose-p:my-1 prose-pre:my-1.5 prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-headings:my-1.5 prose-headings:font-semibold",
        invert ? "prose-invert" : "dark:prose-invert",
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{children}</ReactMarkdown>
    </div>
  );
}
