"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import {
  AVATAR_COLORS,
  AVATAR_TEMPLATE_IDS,
  AgentAvatar,
  avatarInitials,
} from "@/components/agent-avatar";
import type { AvatarTemplate } from "@/lib/api";

/** Picker state: null template = plain 2-letter initials, otherwise a robot SVG. */
export interface AvatarSelection {
  template: AvatarTemplate | null;
  color: string | null;
}

/**
 * Avatar + background-color picker used by the create/edit agent forms. Grid of
 * 10 cartoon robot templates (plus an "initials" first option) and 10 round
 * color swatches. Picking a template without an explicit color auto-selects the
 * first swatch so robots never render on a mismatched fallback.
 */
export function AvatarPicker({
  value,
  onChange,
  previewName,
}: {
  value: AvatarSelection;
  onChange: (next: AvatarSelection) => void;
  previewName: string;
}) {
  const [color, setColor] = useState<string>(value.color ?? AVATAR_COLORS[0]);
  const colorHex = value.color ?? color;

  function pickTemplate(template: AvatarTemplate | null) {
    // Templates always carry an explicit color: if the user hasn't picked one,
    // snap to the first swatch so the preview isn't a random hash hue.
    onChange({ template, color: value.color ?? color });
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <div className="flex size-14 items-center justify-center rounded-full bg-zinc-100 dark:bg-zinc-800">
          <AgentAvatar name={previewName} template={value.template} color={colorHex} size={48} />
        </div>
        <div className="text-xs text-zinc-500">
          Preview —{" "}
          {value.template ? `Person ${value.template.replace("person-", "")}` : "2-letter initials"}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => pickTemplate(null)}
          title="2-letter initials"
          className={cn(
            "flex size-8 items-center justify-center rounded-full bg-zinc-500 text-[10px] font-semibold text-white transition",
            value.template === null
              ? "ring-2 ring-foreground ring-offset-2"
              : "hover:scale-110",
          )}
        >
          {avatarInitials(previewName)}
        </button>
        {AVATAR_TEMPLATE_IDS.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => pickTemplate(id)}
            title={id}
            className={cn(
              "flex size-8 items-center justify-center overflow-hidden rounded-full transition",
              value.template === id
                ? "ring-2 ring-foreground ring-offset-2"
                : "hover:scale-110",
            )}
            style={{ backgroundColor: colorHex }}
          >
            <AgentAvatar
              name={previewName}
              template={id}
              color={colorHex}
              size={32}
              className="rounded-none bg-transparent"
            />
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        {AVATAR_COLORS.map((hex) => (
          <button
            key={hex}
            type="button"
            onClick={() => {
              setColor(hex);
              onChange({ ...value, color: hex });
            }}
            title={hex}
            aria-label={`Background ${hex}`}
            className={cn(
              "size-6 rounded-full transition",
              hex === colorHex ? "ring-2 ring-foreground ring-offset-2" : "hover:scale-110",
            )}
            style={{ backgroundColor: hex }}
          />
        ))}
      </div>
    </div>
  );
}
