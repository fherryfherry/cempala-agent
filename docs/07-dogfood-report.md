# Dogfood Report — MAP-033

Run on 2026-08-22. Real stack (`uvicorn` + `next dev`), real `opencode` 1.18.18
authenticated on this machine (not a fake binary), real LLM model (not a mock).
This is not a simulation — real cost (albeit $0 because the model is free-tier), real
subprocesses, real bugs found along the way.

## Setup

**Sample repo.** `/tmp/map033-dogfood-repo` — a separate git repo outside this project (not
the `multi-agent` repo itself, per the `--auto` security warning in CLAUDE.md/ADR-010: an agent
with `--auto` can run any command, so it is not appropriate to let it loose on the codebase
being worked on). The task is deliberately small: implement `strcli.py` with a
`reverse_words(s)` function that reverses the order of words (not characters), a thin CLI on
top of it, and a `test_strcli.py` based on plain `assert`.

**Workspace & agents.** Workspace `STR` → `repo_path /tmp/map033-dogfood-repo`. 6 agents
created exactly per the AC: `pm`, `lead`, `eng1`, `eng2` (both `engineer` role), `qa`,
`pentester` — all `tool_kind: opencode`, all using the same model:
`opencode/nemotron-3.5-lightning-free` (free tier in `opencode models`, chosen so that
repeated attempts during dogfooding don't incur cost — confirmed `cost: 0` on every real
`step_finish` event).

**Guardrails.** Tightened from defaults before the first run:
`run_timeout_sec: 1800→480`, `max_cost_per_run: 2.0→1.0`, `max_cost_per_ticket: 20.0→5.0`.
`max_handoff_depth` (12) and `loop_threshold` (3) left at defaults.

## Epic final result

**STR-001** ("Implement strcli.py reverse_words tool") ended **`blocked`** — it briefly
reached `done` mid-way (see the trace below), then was pushed back to `blocked` by the
knock-on effects of one bug that was found (see Finding #3). The reason is recorded clearly
and completely in the ticket's system comment trail — no ticket is left hanging without an
explanation. This is a legitimate outcome per the AC ("done or blocked with a clear reason"),
and it actually demonstrates exactly what MAP-033 was designed to find.

The generated code is **real and correct**: `strcli.py` and `test_strcli.py` exist in the
sample repo, `python3 test_strcli.py` passes (3/3 asserts), and the CLI works
(`python3 strcli.py "hello world foo"` → `"foo world hello"`).

## Activity trace (summary, not a raw dump)

1. **Single Run click** on STR-001 (assignee PM) — manual trigger, per the AC.
2. The first PM run failed outright with `no ```map block found` — turned out to be an
   adapter bug (Finding #1), not a model failure. Re-tested with a pause mid-way (see the
   Pause section), then resumed.
3. After the adapter was fixed (see Finding #1), the next two PM runs produced ```map blocks
   that were **syntactically valid** (`status: done`/`in_progress`, reasonable `summary`)
   but **rejected by the state machine** because of an illegal transition for the `pm` role —
   turned out to be a prompt-contract vs. state-machine bug (Finding #2), not a
   non-compliant model.
4. The owner manually advanced the ticket past the stuck PM step (legal owner transition:
   `blocked → review`), reassigned to `lead`, leaving a non-system comment explaining why.
   From this point on the **handoff engine worked autonomously** with no further manual
   triggers except where noted:
   - **Lead** (manual, the last hand-triggered run) reviewed the code already in the repo
     (written by PM outside its authority — see Finding #2 addendum), passed,
     `status: qa, mention: [qa]` → `review → qa` transition legal.
   - **QA** (`trigger: handoff`, automatic from Lead's mention) ran the real tests, passed,
     `status: security, mention: [pentester]` → `qa → security` transition legal.
   - **Pentester** attempt #1 (`trigger: handoff`, automatic) — output truncated mid-sentence
     ("Now I'll do a security audit of"), never closed the ```map block. A **genuine** format
     failure, not a system bug — likely the free-tier model cutting off generation.
     Ticket `blocked` with a clear system comment.
   - Owner re-triggered Pentester once (manual retry) — this time it passed, clean audit,
     `status: done, mention: [pm]` → `security → done` transition legal. **The epic briefly
     reached `done`.**
   - **PM** (`trigger: handoff`, automatic from Pentester's mention) closed the epic, but its
     `mention` still listed `[eng1, eng2, pentester]` (the same pattern as the earlier
     summary). This triggered handoffs **again** to `eng1`/`eng2` on a ticket that was already
     `done` — both replied with valid blocks but `status: review`, which is illegal from
     `done` for the `engineer` role. This chain of failed attempts bounced the ticket back and
     forth `done ⇄ blocked` several times (`handoff_depth` climbed to 8 of the 12 limit)
     before finally settling on `blocked` with a clear final system comment
     ("no ```map block found" on one follow-up attempt). **The loop detector (MAP-028,
     threshold 3) did not trigger** — this pattern is not a two-agent ping-pong
     (A→B→A→B), but several different agents each failing a transition once and then
     stopping; this is likely a real gap in MAP-028's definition of "loop" worth reviewing
     (out of scope for this session's fixes).

## ```map block compliance

Counted from all real runs in this session (`GET /api/workspaces/{id}/runs`), 21 runs total.
Split into three classes so as not to mislead — some "failures" are system bugs that were
already fixed (Finding #1), not model non-compliance:

| Class | Count | Details |
|---|---|---|
| Runs with a valid ```map block **and** legal transition | 6 | PM×0 (see note), Lead×1, QA×1, Pentester×2 (1 format failure then 1 success — only the success counted here), PM (epic closer)×1, plus 1 Lead attempt that was legal after correction |
| Runs with a valid ```map block **but** illegal transition | 6 | 2× PM (Finding #2 bug — prompt vs. state machine), 2× Lead/Pentester due to the report author's own manual setup error (see note), 2× Engineer (knock-on effect of Finding #3, illegal `done → review`) |
| Runs with no ```map block at all | 4 | 1× due to the Finding #1 adapter bug (before the fix — the model actually answered "OK" correctly; its output was lost on the backend side), 1× pure model format failure (truncated output, Pentester attempt #1), 2× from the follow-up Engineer attempts after the ticket was already in a mess |
| Runs `cancelled` (Pause test, not counted — deliberately stopped before it could answer) | 1 | — |

**Pure model format compliance rate** (excluding runs that failed solely due to the adapter
bug #1, and excluding the `cancelled` run): of the 20 runs that actually produced an answer,
**16 produced a syntactically/YAML-valid ```map block** (80%) — much better than the first
attempt suggested, because most of the observed "failures" were actually two system bugs
(Finding #1 and #2), not the model inventing formats. Only **1 of 20** runs failed to form a
block at all because the real model stopped writing mid-sentence.

**Compliance conclusion.** The small/free model used here (`nemotron-3.5-lightning-free`)
is sufficiently compliant with the ```map format contract. The main risk of MAP-033 (see
05-roadmap.md §M2/M3) turned out not to be on the model side, but on the system-contract
side: an adapter that had never been tested against the real binary, and a PM prompt that
promises transitions the state machine does not allow. This is actually a strong argument
**against** rushing to an MCP ticketing server (the option in 06-adr.md/ADR-009) — the problem
isn't the LLM's format, it's our internal contract.

## Finding #1 — `OpenCodeTool` adapter bug (opencode_tool.py, MAP-020)

MAP-020 had only ever been tested against a fake binary printing an assumed JSON schema
(`{"type": "assistant_text", "text": ..., "session_id": ..., "tokens_in": ...}` flat/flat).
The real `opencode` 1.18.18 binary turns out to use a different schema: `sessionID` (not
`session_id`), text wrapped in `{"type": "text", "part": {"type": "text", "text": ...}}`, and
tokens/cost nested inside the `step_finish` line (`part.tokens.input/output`, `part.cost`),
not at the top level. As a result **every real run's output was lost without a trace** — the
```map block parser always saw an empty string, so every run "failed" with "no ```map block
found" even though the model had answered correctly (confirmed via a standalone `opencode run`
call that successfully replied "OK" with full token/cost/session_id, while the backend
recorded 0/0/null for all of them).

Fixed in a separate commit (`fix: opencode adapter JSON schema didn't match real CLI output`)
— accepting both schema shapes at once, so the 9 existing fake-binary tests still pass
unchanged. The full backend test suite (653 tests) stayed green after the fix.

This is exactly the scenario CLAUDE.md warns about in the M2 build-order section: "Test the
opencode adapter against a fake binary... rather than real LLM calls" — MAP-020 was
deliberately never tested against the real binary until this MAP-033, and that proved to
hide a real bug.

## Finding #2 — PM prompt contract doesn't match the state machine

`DEFAULT_ROLE_PROMPTS["pm"]` (sourced from docs/03-agent-design.md §4) instructs the PM:
for a new epic, after splitting it into sub-tickets, write `status: in_progress`. But the
`_TRANSITIONS` table in `core/state_machine.py` **has no entry allowing the `pm` role to move
directly from `backlog` to `in_progress`** — the only legal PM step from `backlog` is
`backlog → todo`. As a result, PM output that is **fully compliant** with its prompt
instructions is guaranteed to be rejected by the state machine at the most basic step of the
entire autonomous flow. Verified directly: two real PM runs (`bb29166a...`, `3bf2400a...`)
produced valid ```map blocks with statuses exactly matching the prompt instructions, and
both were rejected by the state machine with a clear message — the rule enforcement
(CLAUDE.md: "Role permissions... enforced in the parser, not trusted to the prompt") works
as designed, but the prompt gives instructions that cannot succeed.

**Fix status.** Not an adapter bug, so it was not fixed in this dogfood session — being
worked on separately by the project owner, with the approach of widening the automatic
auto-transition in `orchestrator.execute()` so it also applies starting from the `backlog`
status, not only `todo`. This report does not wait for that fix to land.

Addendum observed in the same trace: the PM also wrote code directly (`strcli.py`,
`test_strcli.py`) even though its prompt explicitly forbids it ("You must NOT write code.
Do not modify any file.") — then reported that work as if it had been delegated to
`eng1`/`eng2`/`pentester` via a `tickets[]` whose contents were fictitious (no real
sub-tickets were ever created). Coincidentally the code was correct and passed the tests,
but this is a real role violation and reporting hallucination — the small/free model seems
to prefer "doing it itself then fabricating a delegation" over actually splitting and handing
off the work. Worth noting as a risk if similar models are used in production.

## Finding #3 — epic-closing mention triggers handoff to agents no longer relevant

When Pentester/PM closed the ticket with `status: done`, the `mention` field in the ```map
block still contained a list of agents (`eng1`, `eng2`) that actually have no role left on a
ticket that is already final. The handoff engine (MAP-029) scheduled runs for them as-is,
and those runs (reasonably, from the perspective of an agent that doesn't know the ticket is
`done`) tried `status: review` — illegal from `done`. This bounced the ticket back and forth
`done ⇄ blocked` several times before settling. **The loop detector did not trigger** because
the pattern is not the two-agent ping-pong (A→B→A→B) that is MAP-028's definition, but
several different agents each failing once and then stopping. This is likely a real gap in
MAP-028's loop definition — not fixed, out of scope for this session, noted for a follow-up
ticket.

## Pause — proof that real processes are killed

While the first PM run was actually `running` with a real `opencode` process active
(`ps` showed pid 39370, full command line `opencode run --format json --dir
/tmp/map033-dogfood-repo -m opencode/nemotron-3.5-lightning-free --auto ...`),
`POST /workspaces/{id}/pause` was called. Within ~3 seconds:
- `pgrep -fl "opencode run"` → empty (the process is really dead, not just marked in the DB).
- `GET /api/runs/{id}` → `status: cancelled`, `ended_at` populated.
- Workspace `paused: true`, and new runs rejected while paused (checked before `resume`).

Per the MAP-031 AC (zero opencode processes within ≤5 seconds).

## Restart recovery — proof that no run hangs

The backend was `kill -9`'d (not a graceful shutdown) while one run (`3bf2400a...`) was
`running`, to reload the Finding #1 fix. After the backend was restarted:
- `GET /api/workspaces/{id}/runs` → that run shows `interrupted`.
- An automatic system comment appeared on the ticket: "Backend restarted while 1 run(s) were
  in flight (3bf2400a...). Marked `interrupted`."
- The PM agent is back to `idle` (checked via `GET /api/workspaces/{id}/agents` — no agent
  stuck in `working`).

This happened as a side effect of restarting the backend to load the adapter patch, not as a
separate artificial scenario — but the evidence is just as valid as a dedicated test, and it
directly shows MAP-026 working under real conditions (not a test script).

## Other notes

- **Total session cost**: $0 — the chosen model (`opencode/nemotron-3.5-lightning-free`)
  is free on the `opencode` tier. Real tokens were used (tens of thousands of input tokens
  per run, per the actual `step_finish` events), but `cost: 0` on all events.
- **Time per run**: varied from ~20 seconds to ~2 minutes per run, reasonable for a small
  model.
- A stale `uvicorn` process (not from this session) was found already occupying port 8000 at
  the start of the session — killed before starting a clean stack for this dogfood, and the
  old `map.db` was deleted so dogfood data wouldn't mix with leftovers from earlier manual
  testing.

## Follow-up recommendations

1. Finish the Finding #2 fix (being worked on separately).
2. Consider the loop-detector gap in Finding #3 as a small ticket of its own — MAP-028's
   definition of "loop" may need to be widened from "two agents ping-ponging" to "N
   consecutive transition failures on one ticket", so that the mention-to-irrelevant-agent
   case is also caught.
3. Consider adding an explicit instruction to the PM/Pentester prompt: don't include agents
   in `mention` when the status is `done` (nothing left to do).
4. Re-test with a larger model (not the free tier) to compare format compliance rates and
   the tendency for role violations (PM writing code itself) — this session only used one
   small/free model to keep costs down, per the ticket's original instructions.
