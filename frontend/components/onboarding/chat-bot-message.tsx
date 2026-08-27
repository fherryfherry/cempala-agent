"use client";

import { useEffect, useRef, useState } from "react";

/** Reveals `text` a few characters at a time, like a chatbot "typing" — then calls
 * `onDone` once. Click/tap anywhere on the message skips straight to full text. */
export function ChatBotMessage({
  text,
  onDone,
  className,
}: {
  text: string;
  onDone?: () => void;
  className?: string;
}) {
  const [shown, setShown] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let i = 0;
    const step = 2; // characters revealed per tick
    const id = setInterval(() => {
      i += step;
      setShown(text.slice(0, i));
      // "auto" (instant), not "smooth" — this fires every 15ms, and each smooth
      // scroll call restarts its own animation, which fights the previous one and
      // looks jittery. Instant + this frequency reads as one continuous glide.
      ref.current?.scrollIntoView({ behavior: "auto", block: "end" });
      if (i >= text.length) {
        clearInterval(id);
        onDone?.();
      }
    }, 15);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  const done = shown.length >= text.length;

  return (
    <div
      ref={ref}
      className={`animate-in fade-in-0 slide-in-from-bottom-1 max-w-[85%] cursor-default rounded-2xl rounded-bl-sm bg-muted px-5 py-3.5 text-lg ${className ?? ""}`}
      onClick={() => {
        if (!done) {
          setShown(text);
          onDone?.();
        }
      }}
    >
      {shown}
      {!done && <span className="animate-pulse">▍</span>}
    </div>
  );
}
