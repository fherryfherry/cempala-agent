"use client";

import { useState } from "react";
import { ArrowUpIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface ChatOption {
  label: string;
  value: string;
}

/** Quick-reply buttons for a bot question, plus an always-present "Lainnya..."
 * fallback that swaps to a free-text input. `onAnswer` fires once, with either
 * the picked option's value or the typed free text. */
export function ChatOptions({
  options,
  onAnswer,
  freeTextPlaceholder = "Tulis jawabanmu…",
  minLength = 1,
}: {
  options: ChatOption[];
  onAnswer: (value: string) => void;
  freeTextPlaceholder?: string;
  minLength?: number;
}) {
  const [customMode, setCustomMode] = useState(options.length === 0);
  const [text, setText] = useState("");
  const [answered, setAnswered] = useState(false);

  if (answered) return null;

  const submitText = () => {
    const trimmed = text.trim();
    if (trimmed.length < minLength) return;
    setAnswered(true);
    onAnswer(trimmed);
  };

  if (customMode) {
    return (
      <form
        className="animate-in fade-in-0 relative w-full max-w-[85%]"
        onSubmit={(e) => {
          e.preventDefault();
          submitText();
        }}
      >
        <Input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={freeTextPlaceholder}
          className="h-14 rounded-full pr-14 pl-5 text-lg md:text-lg"
        />
        <Button
          type="submit"
          size="icon"
          className="absolute top-1/2 right-2 size-10 -translate-y-1/2 rounded-full"
          disabled={text.trim().length < minLength}
        >
          <ArrowUpIcon className="size-5" />
        </Button>
      </form>
    );
  }

  return (
    <div className="animate-in fade-in-0 flex flex-wrap gap-2">
      {options.map((opt) => (
        <Button
          key={opt.value}
          type="button"
          variant="outline"
          size="lg"
          className="text-lg"
          onClick={() => {
            setAnswered(true);
            onAnswer(opt.value);
          }}
        >
          {opt.label}
        </Button>
      ))}
      <Button type="button" variant="ghost" size="lg" className="text-lg" onClick={() => setCustomMode(true)}>
        Lainnya...
      </Button>
    </div>
  );
}
