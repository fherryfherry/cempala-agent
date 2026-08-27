import type { AvatarTemplate } from "@/lib/api";

/** Slot definition inside a squad template: which role, which avatar, and a
 * default name suggestion derived from NAME_POOL. Templates only reference
 * builtin role keys; custom roles aren't in templates. */
export interface TemplateSlot {
  role: string;
  label: string;
  avatar_template: AvatarTemplate | null;
  avatar_color: string | null;
}

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  slots: TemplateSlot[];
}

const ROLE_LABELS: Record<string, string> = {
  pm: "Project Manager",
  lead: "Lead Engineer",
  engineer: "Engineer",
  designer: "Designer",
  qa: "QA",
  pentester: "Security Reviewer",
  business_analyst: "Business Analyst",
  system_architect: "System Architect",
};

// Common Indonesian first names, used to suggest a natural agent name instead of a
// role-number slug (e.g. "Budi" instead of "eng-1").
export const NAME_POOL = [
  "Budi", "Siti", "Andi", "Dewi", "Rian", "Putri", "Agus", "Rina", "Dedi", "Yuni",
  "Fajar", "Wulan", "Hendra", "Lestari", "Bayu", "Sari", "Eka", "Novi", "Wahyu", "Indah",
  "Rizky", "Ayu", "Teguh", "Fitri", "Arif", "Maya", "Dimas", "Ratna", "Yoga", "Citra",
];

/** First NAME_POOL entry not already taken, else numbered variants of the first
 * name ("Budi2", "Budi3", …). */
export function suggestAgentName(existingNames: string[]): string {
  const taken = new Set(existingNames);
  const free = NAME_POOL.find((n) => !taken.has(n));
  if (free) return free;
  let n = 2;
  while (taken.has(`${NAME_POOL[0]}${n}`)) n++;
  return `${NAME_POOL[0]}${n}`;
}

/** Suggest distinct names for a list of slots, each unique against the existing
 * workspace names and against the other slots. */
export function suggestSlotNames(
  slots: TemplateSlot[],
  existingNames: string[],
): string[] {
  const taken = new Set(existingNames);
  return slots.map(() => {
    const free = NAME_POOL.find((n) => !taken.has(n));
    const name = free ?? `${NAME_POOL[0]}${taken.size + 1}`;
    taken.add(name);
    return name;
  });
}

// Six person avatar templates exist; the two newest roles reuse a template with a distinct
// color rather than needing a 7th/8th bust drawn.
const AVATARS: Record<string, { avatar_template: AvatarTemplate; avatar_color: string }> = {
  pm: { avatar_template: "person-1", avatar_color: "#10b981" },
  lead: { avatar_template: "person-2", avatar_color: "#0ea5e9" },
  engineer: { avatar_template: "person-3", avatar_color: "#8b5cf6" },
  designer: { avatar_template: "person-4", avatar_color: "#f59e0b" },
  qa: { avatar_template: "person-5", avatar_color: "#f43f5e" },
  pentester: { avatar_template: "person-6", avatar_color: "#14b8a6" },
  business_analyst: { avatar_template: "person-3", avatar_color: "#6366f1" },
  system_architect: { avatar_template: "person-4", avatar_color: "#f97316" },
};

export const AGENT_TEMPLATES: AgentTemplate[] = [
  {
    id: "agent-squad",
    name: "Agent Squad",
    description:
      "Satu tim lengkap: PM, Lead Engineer, Engineer, Designer, QA, dan Security Reviewer.",
    slots: [
      { role: "pm", label: ROLE_LABELS.pm, ...AVATARS.pm },
      { role: "lead", label: ROLE_LABELS.lead, ...AVATARS.lead },
      { role: "engineer", label: ROLE_LABELS.engineer, ...AVATARS.engineer },
      { role: "designer", label: ROLE_LABELS.designer, ...AVATARS.designer },
      { role: "qa", label: ROLE_LABELS.qa, ...AVATARS.qa },
      { role: "pentester", label: ROLE_LABELS.pentester, ...AVATARS.pentester },
    ],
  },
];
