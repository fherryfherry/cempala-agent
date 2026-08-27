# Role Dinamis — Design Spec

Date: 2026-08-27
Status: approved (brainstorming)

## Problem

Roles are hardcoded today. `agent.role` is a DB `Enum` of 8 values, and role
logic is scattered as string checks across the codebase:

- `app/core/state_machine.py` — `ALL_ROLES` frozenset; unknown-role rejection
- `app/core/report.py` — `ROLES_ALLOWED_TICKETS` (tickets[]/updates[]), `actor_role == "pm"`
  (artifact_updates, owner-approval gate)
- `app/agents/prompts.py` — `REVIEWER_ROLES` (anti-loop), `ROLE_LABELS`,
  `DEFAULT_ROLE_PROMPTS`, per-role contract blocks
- `app/core/orchestrator.py` — `role == "pm"` heuristics (~10 sites), role-mention
  fallback, reviewer sets
- frontend `lib/api.ts` `Role` literal union + `lib/agent-templates.ts` labels

Goal: users can define new roles with their own system prompt and permissions.
PM stays special — its heuristics (owner approval, sprint planning, auto-check
lookup) are key-based and PM is undeletable.

## Decisions (from brainstorming)

1. **Global, not per-workspace.** One `role` table shared by all workspaces.
   UI lives in global `/settings`, not in a workspace page.
2. **Permission flags per role**, not just label+prompt:
   - `may_declare_tickets` — may emit `tickets[]`/`updates[]` (was
     `ROLES_ALLOWED_TICKETS`: pm, qa, pentester, business_analyst)
   - `may_manage_artifacts` — may emit `artifact_updates[]` (was PM-only)
   - `is_reviewer` — gets the anti-loop block (was `REVIEWER_ROLES`:
     lead, qa, pentester, system_architect)
3. **PM protection.** Key `"pm"` undeletable, flags immutable, prompt editable.
   The 7 other builtin roles are undeletable but fully editable. Custom roles
   can be created/edited/deleted freely; deletion is rejected (409) while any
   agent still uses the role.
4. **Role key immutable** after creation (edits change name/description/prompt/
   flags only). This keeps the `"pm"` heuristics safe and avoids rewriting
   every agent row.
5. **Role prompt is the default; per-agent override stays.** An agent with a
   null `system_prompt` falls back to its role's prompt (instead of
   `DEFAULT_ROLE_PROMPTS`). Existing behavior/data stays compatible.
6. **UI:** dialog from global `/settings` page (a "Roles" card), consistent
   with the existing one-column card layout.

## Data model

New global table `role`:

```
role:
  id                 uuid PK
  key                String, unique, immutable slug (e.g. "scrum_master", "pm")
  name               String        # display label ("Scrum Master")
  description        String|null
  system_prompt      Text|null     # default prompt; agents fall back to this
  is_builtin         Boolean       # 8 seed roles
  may_declare_tickets  Boolean
  may_manage_artifacts Boolean
  is_reviewer          Boolean
  created_at         UTCDateTime
```

`agent.role` changes from SQLAlchemy `Enum(8 values)` to plain `String`
(foreign-key-free — agents may reference any existing role key; orphaned keys
are prevented at the API/parser level via role lookup, and role deletion is
blocked while agents use it).

## Migration

1. Create `role` table.
2. Backfill the 8 builtin roles from the current constants:
   - keys/labels: `pm`, `lead`, `engineer`, `designer`, `qa`, `pentester`,
     `business_analyst`, `system_architect` (`ROLE_LABELS`)
   - `system_prompt` ← `DEFAULT_ROLE_PROMPTS[key]`
   - `may_declare_tickets` ← key in `ROLES_ALLOWED_TICKETS`
   - `may_manage_artifacts` ← key == "pm"
   - `is_reviewer` ← key in `REVIEWER_ROLES`
   - `is_builtin` = true
3. Alter `agent.role` Enum → String. The existing column values are already
   exactly the builtin keys, so no data rewrite is needed.

## Backend

### API — `/api/roles`

- `GET /api/roles` → list, each with `agent_count` (number of agents using it)
- `POST /api/roles` → create (`key`, `name`, `description?`, `system_prompt?`,
  flags). `key` validated as `[a-z][a-z0-9_]*`, no spaces; 409 on duplicate key.
- `PATCH /api/roles/{key}` → edit `name`, `description`, `system_prompt`, flags.
  - 403 if editing flags on `pm` (flags immutable); key itself is never editable
    (not even accepted in the body).
- `DELETE /api/roles/{key}` → 403 if `is_builtin`; 409 if `agent_count > 0`.

Schemas: `RoleOut`, `RoleCreate`, `RoleUpdate` in `app/schemas/role.py`.

### Core de-hardcoding

The principle: **pure modules stay DB-free** — callers pass role data in.

- `state_machine.py`: drop `ALL_ROLES`; `can_transition` gains no role check
  (it currently only rejects unknown roles — the parser/API layer now validates
  the role key against the known roles before calling). Concretely:
  `can_transition(from, to, actor_role)` drops the `actor_role not in ALL_ROLES`
  branch (the unknown-role branch) — the parser already rejected unknown roles
  before the orchestrator ever calls `can_transition`, so removing the branch
  changes no observable behavior. The owner (`actor_role=None`) and
  unrestricted-role paths stay identical.
- `report.py`: `parse_report` replaces `ROLES_ALLOWED_TICKETS`/`== "pm"`
  checks with parameters:
  - `valid_roles: set[str]` (replaces `actor_role not in ALL_ROLES`)
  - `may_declare_tickets: bool`
  - `may_manage_artifacts: bool`
  - `is_pm: bool` (owner-approval gate + artifact_updates stay pm-only in
    behavior — only pm may hit the `ticket_approved` gate, and artifacts gate
    switches from `== "pm"` to `may_manage_artifacts`)
  - `actor_role` stays (message text). `ROLES_ALLOWED_TICKETS` constant is
    removed; `AGENT_DECLARABLE_STATUSES` unchanged.
- `prompts.py`:
  - `AgentInfo` gains `label: str` (resolved by the caller from the role row),
    `is_reviewer: bool`, `may_declare_tickets: bool`, `may_manage_artifacts: bool`;
    `ROLE_LABELS` removed; `REVIEWER_ROLES` removed.
  - `DEFAULT_ROLE_PROMPTS` stays only as seed fallback when a role row has a
    null prompt (defensive; backfilled rows won't hit it).
  - Contract blocks: `agent.role in ROLES_ALLOWED_TICKETS` → `agent.may_declare_tickets`;
    `agent.role == "pm"` (artifact_updates) → `agent.may_manage_artifacts`;
    `agent.role in allowed_sprint_roles` unchanged (per-workspace setting,
    already key-based strings).
- `orchestrator.py`:
  - Load roles once per run from DB; pass flags/labels into the prompt builders
    and `parse_report`.
  - `_resolve_role_agent` (mention-by-role) validates the name against role keys
    from the DB (replaces `ALL_ROLES`).
  - The `"pm"` heuristics stay key-based: `role == "pm"` for the owner-approval
    gate, PM mention instructions, auto-check PM lookup, and the
    `sprint_creator_roles` default. Safe because the key is immutable and pm
    is undeletable.
  - `auto_check.py` PM lookup: unchanged (`role == "pm"`).

### Prompt fallback semantics

`build_prompt`/`build_chat_prompt`/`build_routine_prompt` currently use
`agent.system_prompt` if set, else `DEFAULT_ROLE_PROMPTS[role]`. New: else the
role row's `system_prompt` (the caller passes it as `system_prompt` default via
`AgentInfo`). `AgentInfo.system_prompt` already exists — the orchestrator now
fills it with the role prompt when the agent's own is null.

## Frontend

- `lib/api.ts`: `Role`/`AgentRole` literal unions → `string` (kept as an
  exported type alias for compatibility). Add `Role`-related API functions
  (`listRoles`, `createRole`, `updateRole`, `deleteRole`) + types.
- `lib/agent-templates.ts`: `ROLE_LABELS` / `AVATARS` stay for template slots
  (they only reference builtin keys); `TemplateSlot.role` → `string`.
- `app/settings/page.tsx`: new "Roles" card + `RolesDialog`:
  - List: name (label), key, builtin badge, agent count, Edit/Delete.
  - Create/edit form: key (create only, immutable display after), name,
    description, system prompt textarea, three permission checkboxes
    (disabled + "built-in" note for pm; flags disabled for pm).
  - Delete: confirm dialog; server 409/403 surfaced via toast.
- `app/w/[key]/agents/page.tsx`: role dropdown options from `/api/roles`; agent
  card shows the role label (fall back to raw key). Keep per-agent system
  prompt field as-is (override semantics unchanged).

## Error handling

- 409 `role_in_use` on delete while agents reference it; 409 `duplicate_key`
  on create; 403 `builtin_role` on delete/flag-edit; 403 `pm_flags_locked`.
  All through the existing `AppError`/`errors.py` mechanism.

## Testing

- `tests/test_roles_api.py`: CRUD, 403s, 409s, key immutability, flag lock on pm.
- `tests/test_report.py`: parser accepts custom role keys + flags; pm gate
  behavior unchanged; `may_manage_artifacts` replaces the pm-only check;
  dropped-reason messages updated.
- `tests/test_prompts.py`: custom label in roster/base block; anti-loop gated
  on `is_reviewer`; contract tickets[] gated on `may_declare_tickets`;
  artifact_updates gated on `may_manage_artifacts`; role-prompt fallback.
- `tests/test_orchestrator.py` + `tests/test_agents_api.py`: update existing
  assertions that assumed the 8-role enum / `ALL_ROLES`; role-by-mention
  resolves custom roles.
- Migration test: backfill creates 8 builtin roles; `agent.role` string column
  keeps existing values.

## Out of scope

- Renaming role keys (immutable).
- Per-workspace role visibility (roles are global).
- MCP tool permission scoping (unchanged; MCP tools are role-agnostic except
  delete_ticket which the prompt already restricts to PM).
- `sprint_creator_roles` remains a workspace setting of role keys (unchanged).
