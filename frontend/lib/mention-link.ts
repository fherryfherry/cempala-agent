/**
 * Mention linkification for chat messages and ticket comments.
 *
 * Mentions are stored as raw text; at render time we turn them into links:
 * - `MAP-003` (ticket key) -> link to the ticket page
 * - `@agent-name` -> link to the Agents page (when it matches an agent name)
 * - `@file.ext` -> link to the Artifacts page (when it matches an artifact filename)
 *
 * Returns markdown so it can be fed through the existing <Markdown> renderer.
 */

export interface MentionAgent {
  name: string;
}

export interface MentionArtifact {
  filename: string;
}

export interface MentionTicket {
  key: string;
}

export interface MentionCatalog {
  agents: MentionAgent[];
  artifacts: MentionArtifact[];
  tickets: MentionTicket[];
}

const TICKET_KEY_RE = /\b([A-Z][A-Z0-9]*-\d{3})\b/g;
const AT_MENTION_RE = /@([a-zA-Z0-9][a-zA-Z0-9-]*)/g;

export function linkifyMentions(
  body: string,
  workspaceKey: string,
  catalog: MentionCatalog,
): string {
  const agentNames = new Set(catalog.agents.map((a) => a.name.toLowerCase()));
  const artifactNames = new Set(catalog.artifacts.map((a) => a.filename.toLowerCase()));

  let out = body.replace(TICKET_KEY_RE, (key) => `[\`${key}\`](/w/${workspaceKey}/ticket/${key})`);

  out = out.replace(AT_MENTION_RE, (full, name: string) => {
    const lower = name.toLowerCase();
    if (agentNames.has(lower)) {
      return `[@${name}](/w/${workspaceKey}/agents)`;
    }
    if (artifactNames.has(lower)) {
      return `[@${name}](/w/${workspaceKey}/artifacts)`;
    }
    return full;
  });

  return out;
}
