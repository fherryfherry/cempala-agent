"""Loop detector — docs/02-tsd.md §6, MAP-028.

Detects a ping-pong pattern: two specific agents alternating (A->B->A->B->A->...) on the
same ticket for more than `loop_threshold` round-trips. A "round-trip" is one A->B->A hop
back to the agent that started the alternating run. For a strictly-alternating run of
`run_len` agents, the starting agent reappears `(run_len - 1) // 2` times — that's the
round-trip count.

Calibration against the MAP-028 AC:
- A->B->A->B->A (run_len=5) -> round_trips = (5-1)//2 = 2 -> trips at threshold=2
  (comparison is `>=`, so "threshold 2" means "2 round-trips is already too many").
- A->B->A->B (run_len=4, i.e. one run short of the above) -> round_trips = 1 -> does not
  trip at threshold=2. Confirms the boundary sits exactly where the AC's example does.
- A->B->C->A -> the third leg (C) breaks strict 2-agent alternation, so the alternating
  suffix considered is just "C->A" (run_len=2) -> never trips, regardless of threshold.

This is a pure detection function. It does not mutate the ticket; the caller
(`orchestrator.schedule()`) decides what to do with a non-None result.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.guardrails import guardrail_limit
from app.db.models import Agent, Run, Ticket


async def detect_loop(
    session, ticket: Ticket, guardrails: dict, next_agent_id: str
) -> str | None:
    """Returns a cycle description if scheduling `next_agent_id` would extend a ping-pong
    loop past `loop_threshold` round-trips, else None.

    `next_agent_id` is the agent about to be scheduled (schedule() calls this before the
    new Run row exists, so the candidate run isn't in history yet -- it's appended here).
    """
    threshold = guardrail_limit(guardrails, "loop_threshold")

    result = await session.execute(
        select(Run.agent_id)
        .where(Run.ticket_id == ticket.id)
        .order_by(Run.started_at)
    )
    sequence = [row[0] for row in result.all()] + [next_agent_id]

    if len(sequence) < 2:
        return None

    # Walk backward from the end, extending the window one element at a time while it
    # keeps strict 2-agent alternation (each newly-included element must differ from its
    # neighbor and match the element two spots to its right, preserving the period-2
    # pattern all the way to the window's current right edge).
    n = len(sequence)
    i = n - 1
    while i - 1 >= 0:
        prev = i - 1
        if sequence[prev] == sequence[i]:
            break
        if i + 1 <= n - 1 and sequence[prev] != sequence[i + 1]:
            break
        i = prev
    run_len = n - i

    if run_len < 2:
        return None

    round_trips = (run_len - 1) // 2
    if round_trips < threshold:
        return None

    agent_a_id, agent_b_id = sequence[-1], sequence[-2]
    name_a = await _agent_label(session, agent_a_id)
    name_b = await _agent_label(session, agent_b_id)
    return (
        f"Loop terdeteksi: {name_a} ↔ {name_b} bergantian {round_trips} kali "
        f"pada tiket ini (melebihi ambang loop_threshold={threshold})"
    )


async def _agent_label(session, agent_id: str) -> str:
    agent = await session.get(Agent, agent_id)
    return agent.name if agent is not None else agent_id
