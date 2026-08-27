"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { ChoiceGroup } from "@/lib/parse-choices";

/** Renders a PM ~~~choices block (see lib/parse-choices.ts) as pills — "single"
 * answers immediately on click; "multiple" toggles pills first, then needs an
 * explicit Kirim since more than one can be picked. Shared by every surface that
 * shows PM chat messages (onboarding handoff, the workspace Chat page), so a
 * quick-pick question looks and behaves the same everywhere. */
export function ChoicePills({
  group,
  disabled,
  onAnswer,
}: {
  group: ChoiceGroup;
  disabled: boolean;
  onAnswer: (text: string) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  if (group.type === "single") {
    return (
      <div className="flex flex-wrap gap-2">
        {group.options.map((opt) => (
          <button
            key={opt}
            type="button"
            disabled={disabled}
            onClick={() => onAnswer(opt)}
            className="rounded-full border border-border px-4 py-2 text-lg text-zinc-700 dark:text-zinc-300 transition-colors hover:border-primary hover:bg-accent/50 disabled:pointer-events-none disabled:opacity-40"
          >
            {opt}
          </button>
        ))}
      </div>
    );
  }

  const toggle = (opt: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(opt)) next.delete(opt);
      else next.add(opt);
      return next;
    });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {group.options.map((opt) => (
          <button
            key={opt}
            type="button"
            disabled={disabled}
            onClick={() => toggle(opt)}
            className={`rounded-full border px-4 py-2 text-lg transition-colors disabled:pointer-events-none disabled:opacity-40 ${
              selected.has(opt) ? "border-primary bg-accent/50" : "border-border text-zinc-700 dark:text-zinc-300"
            }`}
          >
            {opt}
          </button>
        ))}
      </div>
      <Button
        size="lg"
        className="w-fit text-lg"
        disabled={disabled || selected.size === 0}
        onClick={() => onAnswer(group.options.filter((o) => selected.has(o)).join(", "))}
      >
        Kirim
      </Button>
    </div>
  );
}
