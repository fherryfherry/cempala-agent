import { cn } from "@/lib/utils";

/** CEMPALA startup banner (ANSI Shadow style), rendered with monospace spacing preserved. */
export const BANNER = `██████╗ ███████╗ ███╗   ███╗ ██████╗  ██████╗  ██╗      ██████╗
██╔════╝ ██╔════╝ ████╗ ████║ ██╔══██╗ ██╔══██╗ ██║      ██╔══██╗
███████╗ █████╗   ██╔████╔██║ ██████╔╝ ███████║ ██║      ███████║
██╔═══╝  ██╔══╝   ██║╚██╔╝██║ ██╔═══╝  ██╔══██║ ██║      ██╔══██║
╚██████╗ ╚██████╗ ██║ ╚═╝ ██║ ██║      ██║  ██║ ███████╗ ██║  ██║
╚═════╝  ╚═════╝  ╚═╝     ╚═╝ ╚═╝      ╚═╝  ╚═╝ ╚══════╝ ╚═╝  ╚═╝`;

/** Tighter variant (no inter-letter gaps), used on the home page. */
export const HOME_BANNER = `██████╗███████╗███╗   ███╗██████╗  █████╗ ██╗      █████╗
██╔════╝██╔════╝████╗ ████║██╔══██╗██╔══██╗██║     ██╔══██╗
██║     █████╗  ██╔████╔██║██████╔╝███████║██║     ███████║
██║     ██╔══╝  ██║╚██╔╝██║██╔═══╝ ██╔══██║██║     ██╔══██║
╚██████╗███████╗██║ ╚═╝ ██║██║     ██║  ██║███████╗██║  ██║
 ╚═════╝╚══════╝╚═╝     ╚═╝╚═╝     ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝`;

export function LogoBanner({
  variant = "nav",
  className,
}: {
  variant?: "nav" | "home";
  className?: string;
}) {
  return (
    <pre
      aria-label="CEMPALA"
      className={cn(
        "overflow-hidden font-mono leading-[1.3] font-semibold text-foreground select-none",
        className,
      )}
    >
      {variant === "home" ? HOME_BANNER : BANNER}
    </pre>
  );
}
