/** Parses the optional `~~~choices` fenced block a PM chat reply may end with
 * (see backend/app/agents/prompts.py `_chat_contract_block`) — a lightweight,
 * hand-rolled format (not full YAML, no dependency needed):
 *
 * ~~~choices
 * single
 * - Option A
 * - Option B
 * ~~~
 *
 * Tildes, not backticks: this block lives inside `summary: |` in the agent's
 * ```map block, and a nested ``` fence would get mistaken for the outer block's
 * own closing marker by report.py's regex — silently truncating everything
 * after it (sprints/tickets, in the exact case this exists for: an approval
 * question bundled with the proposal it's approving). See test_report.py
 * `test_choices_block_nested_in_summary_does_not_truncate_map_block`.
 *
 * First line is `single` or `multiple`; each following `- ` line is one option.
 * Returns the message body with the block stripped (so it isn't shown twice —
 * once as raw fenced text, once as pills) plus the parsed group, if any. */
export interface ChoiceGroup {
  type: "single" | "multiple";
  options: string[];
}

const CHOICES_BLOCK_RE = /~~~choices\n([\s\S]*?)~~~/;

export function parseChoices(body: string): { cleanedBody: string; group: ChoiceGroup | null } {
  const match = body.match(CHOICES_BLOCK_RE);
  if (!match) return { cleanedBody: body, group: null };

  const lines = match[1].split("\n").map((l) => l.trim()).filter(Boolean);
  const type = lines[0] === "multiple" ? "multiple" : "single";
  const options = lines
    .slice(1)
    .filter((l) => l.startsWith("- "))
    .map((l) => l.slice(2).trim())
    .filter(Boolean);

  const cleanedBody = (body.slice(0, match.index) + body.slice((match.index ?? 0) + match[0].length)).trim();

  if (options.length === 0) return { cleanedBody, group: null };
  return { cleanedBody, group: { type, options } };
}
