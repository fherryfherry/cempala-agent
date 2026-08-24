"use client";

import { cn } from "@/lib/utils";
import type { AvatarTemplate } from "@/lib/api";

export const AVATAR_COLORS = [
  "#10b981", // emerald
  "#0ea5e9", // sky
  "#8b5cf6", // violet
  "#f59e0b", // amber
  "#f43f5e", // rose
  "#14b8a6", // teal
  "#6366f1", // indigo
  "#f97316", // orange
  "#06b6d4", // cyan
  "#ec4899", // pink
];

/** Fallback background when an agent has no avatar_color: stable per-name hue. */
export function avatarColorOf(name: string, explicit?: string | null): string {
  if (explicit) return explicit;
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

/** Deterministic 2-letter initials from an agent name ("Budi Santoso" -> "BS"). */
export function avatarInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0][0] ?? "?";
  const last = parts.length > 1 ? (parts[parts.length - 1][0] ?? "") : (parts[0][1] ?? "");
  return (first + last).toUpperCase();
}

const SKIN = "#ffd6bd";
const INK = "#26242c";
const HAIR_BLACK = "#2f2a33";
const HAIR_BROWN = "#6b4a2b";
const HAIR_CHESTNUT = "#8a5b2b";

/** Shared body parts, reused across the six busts. */
const LEFT_ARM = (
  <path d="M2.5 52 L2.5 44 Q2.5 37 7 34 L9 36 Q5 39 5 44 L5 52 Z" fill={SKIN} />
);
const RIGHT_ARM = (
  <path d="M49.5 52 L49.5 44 Q49.5 37 45 34 L43 36 Q47 39 47 44 L47 52 Z" fill={SKIN} />
);

/** Male bust: broad shoulders, shirt pulled down to the sides. */
const MALE_TORSO = (shirt: string) => (
  <>
    <path
      d="M6 52 L6 27 Q6 22 11 22 Q14 22 15 26 Q15.5 30 17.5 31 L34.5 31 Q36.5 30 37 26 Q38 22 41 22 Q46 22 46 27 L46 52 Z"
      fill={shirt}
    />
    <path d="M24.5 31 L24.5 52" stroke="rgba(0,0,0,0.15)" strokeWidth="1.2" />
  </>
);

/** Female bust: narrower dress with a rounded neckline. */
const FEMALE_TORSO = (dress: string) => (
  <>
    <path
      d="M8 52 L8 27 Q8 21 12.5 21 Q14.5 21 15.5 25 Q16 29 19 29.5 L28.5 29.5 L33 29.5 Q36 29 36.5 25 Q37.5 21 39.5 21 Q44 21 44 27 L44 52 Z"
      fill={dress}
    />
    <path
      d="M16.5 26 Q18.5 28 21 28.5 Q23.5 29 26 28.5 Q28.5 29 31 28.5 Q33.5 28 35.5 26"
      stroke="rgba(0,0,0,0.18)"
      strokeWidth="1.2"
      fill="none"
      strokeLinecap="round"
    />
  </>
);

/** Long hair masses falling over the shoulders (women). */
const LONG_HAIR_LEFT = (hair: string) => (
  <path
    d="M20 12 Q4 20 5.5 34 Q6.5 42 9.5 52 L7 52 Q4.5 42 5 33 Q4.5 21 17 13.5 Z"
    fill={hair}
  />
);
const LONG_HAIR_RIGHT = (hair: string) => (
  <path
    d="M32 12 Q48 20 46.5 34 Q45.5 42 42.5 52 L45 52 Q47.5 42 47 33 Q47.5 21 35 13.5 Z"
    fill={hair}
  />
);

const EYE_LEFT = <circle cx="21.8" cy="17.3" r="1.35" fill={INK} />;
const EYE_RIGHT = <circle cx="30.2" cy="17.3" r="1.35" fill={INK} />;

/** 6 cartoon-person busts (head + shoulders + body) filling the avatar circle:
 * 3 men (person-1..3) and 3 women (person-4..6). Shared trick: the hair circle
 * renders behind the head circle, so the head "eats" the lower part and leaves
 * a natural hairline. */
const PERSON_TEMPLATES: Record<AvatarTemplate, React.ReactNode> = {
  "person-1": (
    <>
      {LEFT_ARM}
      {RIGHT_ARM}
      {MALE_TORSO("#dfe9fb")}
      <circle cx="26" cy="10.5" r="13.5" fill={HAIR_BLACK} />
      <circle cx="26" cy="17" r="9.5" fill={SKIN} />
      <path d="M16 10 Q16 6 20.5 6 Q18 8 19 11 Z" fill={HAIR_BLACK} />
      {EYE_LEFT}
      {EYE_RIGHT}
      <path d="M26 20.5 q1.6 1.4 3 0" stroke={INK} strokeWidth="1.4" fill="none" strokeLinecap="round" />
    </>
  ),
  "person-2": (
    <>
      {LEFT_ARM}
      {RIGHT_ARM}
      <path d="M4 52 L4 40 Q4 30 8 27 L13 25 Q15 30 17 31 L35 31 Q37 30 39 25 L44 27 Q48 30 48 40 L48 52 Z" fill="#3b82f6" />
      <path d="M18 27 L24.5 31 L21 27 Z" fill="#dbeafe" />
      <path d="M34 27 L27.5 31 L31 27 Z" fill="#dbeafe" />
      <circle cx="26" cy="11" r="13" fill={HAIR_BROWN} />
      <circle cx="26" cy="17.5" r="9.5" fill={SKIN} />
      <circle cx="21.8" cy="17.5" r="3.4" fill="#d7e9f7" stroke={INK} strokeWidth="1.3" />
      <circle cx="30.2" cy="17.5" r="3.4" fill="#d7e9f7" stroke={INK} strokeWidth="1.3" />
      <path d="M25.2 17.5 h1.6" stroke={INK} strokeWidth="1.3" />
      <circle cx="21.8" cy="17.5" r="1.2" fill={INK} />
      <circle cx="30.2" cy="17.5" r="1.2" fill={INK} />
      <path d="M26 21.5 q1.5 1.2 3 0" stroke={INK} strokeWidth="1.3" fill="none" strokeLinecap="round" />
    </>
  ),
  "person-3": (
    <>
      {LEFT_ARM}
      {RIGHT_ARM}
      {MALE_TORSO("#86efac")}
      <circle cx="26" cy="12.5" r="13" fill={HAIR_BLACK} />
      <circle cx="26" cy="14" r="5.2" fill={HAIR_BLACK} />
      <circle cx="26" cy="19" r="9" fill={SKIN} />
      <path d="M26 22.6 q2 1.5 4 0" stroke={INK} strokeWidth="1.4" fill="none" strokeLinecap="round" />
      <circle cx="21.6" cy="17.6" r="1.3" fill={INK} />
      <circle cx="30.4" cy="17.6" r="1.3" fill={INK} />
      <path d="M21.5 14 q-0.5 -2.5 1 -3 M22.8 13 q0 -2.2 1.4 -2.6" stroke={INK} strokeWidth="1" fill="none" strokeLinecap="round" />
      <path d="M30.5 14 q0.5 -2.5 -1 -3 M29.2 13 q0 -2.2 -1.4 -2.6" stroke={INK} strokeWidth="1" fill="none" strokeLinecap="round" />
      <circle cx="16.8" cy="20.5" r="1.3" fill="#f5c542" />
      <circle cx="35.2" cy="20.5" r="1.3" fill="#f5c542" />
    </>
  ),
  "person-4": (
    <>
      {LEFT_ARM}
      {RIGHT_ARM}
      {LONG_HAIR_LEFT(HAIR_BROWN)}
      {LONG_HAIR_RIGHT(HAIR_BROWN)}
      {FEMALE_TORSO("#ef4444")}
      <circle cx="26" cy="11.5" r="13" fill={HAIR_BROWN} />
      <circle cx="26" cy="17.5" r="9.5" fill={SKIN} />
      <path d="M15.5 16 Q14 10 18.5 9 Q23 8 26 9.5 Q29 8 33.5 9 Q38 10 36.5 16 Q34 12.5 30 12.8 Q26 13 23 12.8 Q19 12.5 15.5 16 Z" fill={HAIR_BROWN} />
      {EYE_LEFT}
      {EYE_RIGHT}
      <path d="M23 21.5 q2.2 2.2 6 0" stroke={INK} strokeWidth="1.4" fill="none" strokeLinecap="round" />
    </>
  ),
  "person-5": (
    <>
      {LEFT_ARM}
      {RIGHT_ARM}
      {FEMALE_TORSO("#a78bfa")}
      <circle cx="26" cy="11" r="13.5" fill={HAIR_CHESTNUT} />
      <circle cx="26" cy="17.5" r="9.5" fill={SKIN} />
      <path d="M13.5 17.5 Q12 9 19.5 8 Q23 7.5 26 9 Q29 7.5 32.5 8 Q40 9 38.5 17.5 Q36 13.5 31 13.5 Q26 13.5 21 13.5 Q16 13.5 13.5 17.5 Z" fill={HAIR_CHESTNUT} />
      {EYE_LEFT}
      {EYE_RIGHT}
      <path d="M22.5 21.5 q1.8 2.4 3.5 2.4 t3.5 -2.4" stroke={INK} strokeWidth="1.4" fill="none" strokeLinecap="round" />
    </>
  ),
  "person-6": (
    <>
      {LEFT_ARM}
      {RIGHT_ARM}
      {LONG_HAIR_LEFT(HAIR_BLACK)}
      {LONG_HAIR_RIGHT(HAIR_BLACK)}
      {FEMALE_TORSO("#f472b6")}
      <circle cx="12" cy="5" r="3.2" fill={HAIR_BLACK} />
      <circle cx="26" cy="11" r="13.2" fill={HAIR_BLACK} />
      <circle cx="26" cy="17.5" r="9.5" fill={SKIN} />
      <path d="M15 14 Q15.5 9.5 20 9.5 Q23.5 9.5 26 10.5 Q28.5 9.5 32 9.5 Q36.5 9.5 37 14 Q33.5 11.5 26 12 Q18.5 11.5 15 14 Z" fill={HAIR_BLACK} />
      <circle cx="21.8" cy="17.5" r="3.2" fill="#d7e9f7" stroke={INK} strokeWidth="1.3" />
      <circle cx="30.2" cy="17.5" r="3.2" fill="#d7e9f7" stroke={INK} strokeWidth="1.3" />
      <circle cx="21.8" cy="17.5" r="1.15" fill={INK} />
      <circle cx="30.2" cy="17.5" r="1.15" fill={INK} />
      <path d="M26 21.5 q1.5 1.2 3 0" stroke={INK} strokeWidth="1.3" fill="none" strokeLinecap="round" />
    </>
  ),
};

export const AVATAR_TEMPLATE_IDS = Object.keys(PERSON_TEMPLATES) as AvatarTemplate[];

/**
 * Renders an agent's avatar everywhere in the UI: a cartoon person SVG when an
 * avatar_template is set, otherwise 2-letter initials on a colored disc. The
 * background comes from avatar_color (or a stable name hash when unset).
 */
export function AgentAvatar({
  name,
  template,
  color,
  size = 24,
  className,
  initialsClassName,
}: {
  name: string;
  template?: AvatarTemplate | null;
  color?: string | null;
  size?: number;
  className?: string;
  initialsClassName?: string;
}) {
  const bg = avatarColorOf(name, color);
  if (template && PERSON_TEMPLATES[template]) {
    return (
      <span
        className={cn(
          "flex shrink-0 items-center justify-center overflow-hidden rounded-full ring-1 ring-black/10 ring-inset dark:ring-white/15",
          className,
        )}
        style={{ width: size, height: size, backgroundColor: bg }}
        aria-label={`Avatar ${template}`}
      >
        <svg viewBox="0 0 52 52" width={size * 0.78} height={size * 0.78} role="img">
          {PERSON_TEMPLATES[template]}
        </svg>
      </span>
    );
  }
  return (
    <span
      className={cn("flex shrink-0 items-center justify-center rounded-full font-semibold text-white", className)}
      style={{ width: size, height: size, backgroundColor: bg }}
      aria-label={name}
    >
      <span
        className={cn("leading-none font-semibold", initialsClassName)}
        style={{ fontSize: size * 0.4 }}
      >
        {avatarInitials(name)}
      </span>
    </span>
  );
}
