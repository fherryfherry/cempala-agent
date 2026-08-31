"""Tests for the ```map block parser (MAP-018). See docs/02-tsd.md §4.3/§10.1
and docs/03-agent-design.md §3/§6 for the rules under test.
"""

from app.core.role_defs import BUILTIN_ROLES
from app.core.report import parse_report

VALID_AGENTS = {"lead-1", "eng-1", "eng-2", "pm-1", "qa-1", "pentester-1"}

BUILTIN_FLAGS = {r["key"]: r for r in BUILTIN_ROLES}


def _flags(role: str) -> dict:
    """Default permission flags for a role, mirroring the builtin seed — tests
    that exercise the gates pass explicit overrides where the behavior under
    test differs."""
    r = BUILTIN_FLAGS.get(role, {})
    return {
        "may_declare_tickets": r.get("may_declare_tickets", False),
        "may_manage_artifacts": r.get("may_manage_artifacts", False),
        "is_pm": role == "pm",
    }


def _parse(text: str, role: str, valid_agents=VALID_AGENTS, **kwargs):
    """parse_report with the role's default permission flags filled in —
    override any flag by passing it explicitly."""
    merged = {**_flags(role), **kwargs}
    return parse_report(text, role, valid_agents, **merged)


def _wrap(body: str) -> str:
    return f"some assistant text before\n```map\n{body}```\nmore text after"


def test_tickets_dropped_for_pm_without_approval():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  plan proposal\n"
        "tickets:\n"
        "  - title: sub-task\n"
        "    assignee: eng-1\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=False)
    assert result.ok is True
    assert result.tickets == []
    assert result.tickets_dropped is True
    assert "not approved" in result.tickets_dropped_reason


def test_pm_tickets_allowed_with_approval():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  approved plan\n"
        "tickets:\n"
        "  - title: sub-task\n"
        "    assignee: eng-1\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.ok is True
    assert result.tickets_dropped is False
    assert len(result.tickets) == 1


def test_tickets_capped_by_max_tickets_per_report():
    body = "status: in_progress\nsummary: |\n  big breakdown\ntickets:\n"
    for i in range(10):
        body += f"  - title: sub-task {i}\n    assignee: eng-1\n"
    text = _wrap(body)
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True, max_tickets_per_report=5)
    assert result.ok is True
    assert len(result.tickets) == 5
    assert result.tickets_dropped_reason is not None
    assert "max_tickets_per_report" in result.tickets_dropped_reason


def test_tickets_not_capped_when_within_limit():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  small breakdown\n"
        "tickets:\n"
        "  - title: sub-task\n"
        "    assignee: eng-1\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True, max_tickets_per_report=5)
    assert result.ok is True
    assert len(result.tickets) == 1
    assert result.tickets_dropped_reason is None


def test_qa_tickets_unaffected_by_approval_gate():
    # Approval gate is PM-only; QA tickets[] still work without approval.
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  bug findings\n"
        "tickets:\n"
        "  - title: fix bug A\n"
        "    assignee: eng-1\n"
    )
    result = _parse(text, "qa", VALID_AGENTS, ticket_approved=False)
    assert result.ok is True
    assert result.tickets_dropped is False
    assert len(result.tickets) == 1


def test_ticket_category_parsed_when_valid():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  breakdown\n"
        "tickets:\n"
        "  - title: login page\n"
        "    assignee: eng-1\n"
        "    category: feature\n"
        "  - title: perf tweak\n"
        "    category: performance\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.tickets[0].category == "feature"
    assert result.tickets[1].category == "performance"


def test_ticket_category_invalid_dropped_to_none():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  breakdown\n"
        "tickets:\n"
        "  - title: weird\n"
        "    category: not-a-real-category\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.tickets[0].category is None


def test_ticket_epic_parsed_when_given():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  breakdown\n"
        "tickets:\n"
        "  - title: login page\n"
        "    epic: AUTH-001\n"
        "  - title: unrelated\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.tickets[0].epic == "AUTH-001"
    assert result.tickets[1].epic is None


def test_missing_block():
    result = _parse("no fenced block here at all", "engineer", VALID_AGENTS)
    assert result.ok is False
    assert result.reason is not None
    assert "map" in result.reason.lower()


def test_valid_full_block():
    text = _wrap(
        "status: review\n"
        "mention: [lead-1]\n"
        "summary: |\n"
        "  Menambah validasi email di form login.\n"
        "  File: src/auth/login.tsx\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS, actor_name="eng-1")
    assert result.ok is True
    assert result.status == "review"
    assert result.valid_mentions == ["lead-1"]
    assert result.unknown_mentions == []
    assert "validasi email" in result.summary
    assert result.tickets == []
    assert result.tickets_dropped is False


def test_choices_block_nested_in_summary_does_not_truncate_map_block():
    """A ~~~choices block (frontend/lib/parse-choices.ts) embedded in `summary: |`
    must not be mistaken for the outer ```map fence's own closing marker — it uses
    tildes precisely so it can't collide, but this guards the contract itself: any
    fields declared after it (sprints/tickets here) must still parse."""
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  Proposal saya, setuju?\n"
        "  ~~~choices\n"
        "  single\n"
        "  - Oke, lanjutkan\n"
        "  - Saya mau ubah dulu\n"
        "  ~~~\n"
        "tickets:\n"
        "  - title: Setup project\n"
        "    assignee: eng-1\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, actor_name="pm-1")
    assert result.ok is True
    assert "~~~choices" in result.summary
    assert len(result.tickets) == 1
    assert result.tickets[0].title == "Setup project"


def test_choices_field_parses_type_and_options():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  Pick one\n"
        "choices:\n"
        "  type: multiple\n"
        "  options:\n"
        "    - A\n"
        "    - B\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.choices is not None
    assert result.choices.type == "multiple"
    assert result.choices.options == ["A", "B"]
    assert result.choices_dropped is False


def test_choices_field_defaults_to_single_for_invalid_type():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  Pick one\n"
        "choices:\n"
        "  type: yesno\n"
        "  options:\n"
        "    - A\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.choices.type == "single"


def test_choices_field_dropped_when_options_missing():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  Pick one\n"
        "choices:\n"
        "  type: single\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.choices is None
    assert result.choices_dropped is True
    assert "choices.options" in result.choices_dropped_reason


def test_choices_field_dropped_when_not_a_mapping():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  Pick one\n"
        "choices: not-a-mapping\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.choices is None
    assert result.choices_dropped is True


def test_ticket_duration_with_unit_text_dropped_not_crashed():
    """Agents sometimes write "2 minggu"/"3 days" for duration instead of a plain
    number — float() on that used to raise ValueError and crash the whole parse.
    Must drop just the duration field, not the ticket or the report."""
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  plan proposal\n"
        "tickets:\n"
        "  - title: sub-task\n"
        "    assignee: eng-1\n"
        "    duration: 2 minggu\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.ok is True
    assert len(result.tickets) == 1
    assert result.tickets[0].duration is None


def test_sprint_duration_with_unit_text_dropped_not_crashed():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  plan proposal\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    duration: 2 weeks\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.ok is True
    assert len(result.sprints) == 1
    assert result.sprints[0].duration is None


def test_update_duration_with_unit_text_dropped_not_crashed():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  plan proposal\n"
        "updates:\n"
        "  - ticket: KEY-1\n"
        "    duration: three days\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.ok is True
    assert len(result.updates) == 1
    assert result.updates[0].duration is None


def test_malformed_yaml():
    text = _wrap("status: review\nmention: [lead-1\nsummary: broken\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is False
    assert "yaml" in result.reason.lower()


def test_multiple_blocks_last_wins():
    text = (
        "```map\nstatus: blocked\nsummary: |\n  first block, should be ignored\n```\n"
        "some more agent output in between\n"
        "```map\nstatus: review\nsummary: |\n  second block, should be used\n```"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.status == "review"
    assert "second block" in result.summary


def test_missing_summary():
    text = _wrap("status: review\nmention: [lead-1]\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is False
    assert "summary" in result.reason.lower()


def test_empty_summary():
    text = _wrap("status: review\nsummary: ''\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is False


def test_legal_status_variety():
    # Any role may now declare any status (owner request: the old per-role
    # status matrix kept producing false blocks on the kanban board).
    r = _parse(_wrap("status: review\nsummary: |\n  pm can set review too now\n"), "pm", VALID_AGENTS)
    assert r.ok and r.status == "review"
    r = _parse(_wrap("status: done\nsummary: |\n  lead can close directly now\n"), "lead", VALID_AGENTS)
    assert r.ok and r.status == "done"
    r = _parse(_wrap("status: done\nsummary: |\n  engineer can close directly now\n"), "engineer", VALID_AGENTS)
    assert r.ok and r.status == "done"
    # QA
    r = _parse(_wrap("status: security\nsummary: |\n  all tests pass\n"), "qa", VALID_AGENTS)
    assert r.ok and r.status == "security"
    # Pentester
    r = _parse(_wrap("status: done\nsummary: |\n  audit clean\n"), "pentester", VALID_AGENTS)
    assert r.ok and r.status == "done"


def test_unknown_status_value():
    text = _wrap("status: not_a_real_status\nsummary: |\n  whoops\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is False


def test_unknown_mention_dropped_and_recorded():
    text = _wrap("status: review\nmention: [lead-1, ghost-agent]\nsummary: |\n  done\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.valid_mentions == ["lead-1"]
    assert result.unknown_mentions == ["ghost-agent"]


def test_self_mention_dropped_not_unknown():
    text = _wrap("status: review\nmention: [eng-1, lead-1]\nsummary: |\n  done\n")
    result = _parse(text, "engineer", VALID_AGENTS, actor_name="eng-1")
    assert result.ok is True
    assert result.valid_mentions == ["lead-1"]
    assert result.unknown_mentions == []


def test_tickets_from_unauthorized_role_dropped_and_recorded():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  implemented feature\n"
        "tickets:\n"
        "  - title: follow-up task\n"
        "    description: extra work\n"
        "    assignee: eng-2\n"
        "    priority: low\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.tickets == []
    assert result.tickets_dropped is True
    assert result.tickets_dropped_reason is not None
    assert "engineer" in result.tickets_dropped_reason


def test_tickets_from_authorized_role_parsed():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  breakdown into subtasks\n"
        "tickets:\n"
        "  - title: Endpoint POST /auth/login\n"
        "    description: |\n"
        "      Implement login endpoint\n"
        "    assignee: eng-1\n"
        "    priority: high\n"
        "  - title: Login form UI\n"
        "    assignee: eng-2\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.tickets_dropped is False
    assert len(result.tickets) == 2
    assert result.tickets[0].title == "Endpoint POST /auth/login"
    assert result.tickets[0].assignee == "eng-1"
    assert result.tickets[0].priority == "high"
    # second ticket has no explicit priority -> reasonable default
    assert result.tickets[1].priority == "medium"


def test_business_analyst_may_create_tickets():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  backlog captured\n"
        "tickets:\n"
        "  - title: New reporting requirement\n"
        "    description: from stakeholder discussion\n"
    )
    result = _parse(text, "business_analyst", VALID_AGENTS)
    assert result.ok is True
    assert result.tickets_dropped is False
    assert len(result.tickets) == 1


def test_system_architect_cannot_create_tickets():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  design ready\n"
        "tickets:\n"
        "  - title: follow-up spike\n"
    )
    result = _parse(text, "system_architect", VALID_AGENTS)
    assert result.ok is True
    assert result.tickets == []
    assert result.tickets_dropped is True


def test_ticket_missing_title_is_skipped():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  breakdown\n"
        "tickets:\n"
        "  - description: no title here\n"
        "    assignee: eng-1\n"
        "  - title: valid one\n"
        "    assignee: eng-2\n"
    )
    result = _parse(text, "qa", VALID_AGENTS)
    assert result.ok is True
    assert len(result.tickets) == 1
    assert result.tickets[0].title == "valid one"


def test_not_a_mapping_yaml():
    text = _wrap("- just\n- a\n- list\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is False


def test_updates_from_authorized_role_parsed():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  found cross-ticket impact\n"
        "updates:\n"
        "  - ticket: MAP-002\n"
        "    status: blocked\n"
        "    priority: high\n"
        "  - ticket: MAP-003\n"
        "    assignee: eng-2\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.updates_dropped is False
    assert len(result.updates) == 2
    assert result.updates[0].ticket_key == "MAP-002"
    assert result.updates[0].status == "blocked"
    assert result.updates[0].priority == "high"
    assert result.updates[0].assignee is None
    assert result.updates[1].ticket_key == "MAP-003"
    assert result.updates[1].assignee == "eng-2"
    assert result.updates[1].status is None


def test_update_sprint_and_duration_parsed():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  rapikan sprint tiket lama\n"
        "updates:\n"
        "  - ticket: MAP-002\n"
        "    sprint: Sprint 2\n"
        "    duration: 1.5\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.updates_dropped is False
    assert result.updates[0].sprint == "Sprint 2"
    assert result.updates[0].duration == 1.5


def test_update_missing_ticket_key_is_skipped():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  breakdown\n"
        "updates:\n"
        "  - priority: high\n"
        "  - ticket: MAP-004\n"
        "    priority: low\n"
    )
    result = _parse(text, "qa", VALID_AGENTS)
    assert result.ok is True
    assert len(result.updates) == 1
    assert result.updates[0].ticket_key == "MAP-004"


def test_updates_from_unauthorized_role_dropped_and_recorded():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  implemented feature\n"
        "updates:\n"
        "  - ticket: MAP-002\n"
        "    priority: high\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.updates == []
    assert result.updates_dropped is True
    assert result.updates_dropped_reason is not None


def test_ticket_sprint_and_duration_parsed():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  breakdown\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    goal: ship login\n"
        "    duration: 2\n"
        "tickets:\n"
        "  - title: login page\n"
        "    assignee: eng-1\n"
        "    sprint: Sprint 1\n"
        "    duration: 0.5\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.ok is True
    assert result.sprints_dropped is False
    assert len(result.sprints) == 1
    assert result.sprints[0].name == "Sprint 1"
    assert result.sprints[0].goal == "ship login"
    assert result.sprints[0].duration == 2.0
    assert result.tickets[0].sprint == "Sprint 1"
    assert result.tickets[0].duration == 0.5


def test_sprints_dropped_for_pm_without_approval():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  plan proposal\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    goal: explore\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, ticket_approved=False)
    assert result.ok is True
    assert result.sprints == []
    assert result.sprints_dropped is True
    assert "not approved" in result.sprints_dropped_reason


def test_sprints_from_unauthorized_role_dropped():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  implemented\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.sprints == []
    assert result.sprints_dropped is True


def test_sprints_allowed_for_role_in_sprint_creator_roles():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  sprint plan\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    goal: ship login\n"
    )
    result = _parse(text, "qa", VALID_AGENTS, sprint_creator_roles={"pm", "qa"})
    assert result.ok is True
    assert result.sprints_dropped is False
    assert len(result.sprints) == 1
    assert result.sprints[0].name == "Sprint 1"


def test_sprints_parse_start_end_dates():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  sprint plan\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    start_date: 2026-08-25\n"
        "    end_date: 2026-08-29\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert len(result.sprints) == 1
    assert result.sprints[0].start_date == "2026-08-25"
    assert result.sprints[0].end_date == "2026-08-29"


def test_sprints_ignore_missing_dates():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  sprint plan\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    goal: ship\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.sprints[0].start_date is None
    assert result.sprints[0].end_date is None


def test_sprints_status_active_parsed():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  activating sprint\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    status: active\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.sprints[0].status == "active"


def test_sprints_status_completed_parsed():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  completing sprint\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    status: completed\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.sprints[0].status == "completed"


def test_sprints_status_invalid_value_ignored():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  bogus status\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "    status: launched\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.sprints[0].status is None


def test_sprints_status_absent_defaults_to_none():
    text = _wrap(
        "status: in_progress\nsummary: |\n  plain sprint\nsprints:\n  - name: Sprint 1\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.sprints[0].status is None


def test_sprints_dropped_for_role_not_in_sprint_creator_roles():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  sprint plan\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
    )
    result = _parse(text, "qa", VALID_AGENTS, sprint_creator_roles={"pm"})
    assert result.ok is True
    assert result.sprints == []
    assert result.sprints_dropped is True
    assert "only pm" in result.sprints_dropped_reason or "allowed" in result.sprints_dropped_reason


def test_sprints_literal_block_mistake_gets_diagnostic_hint():
    """Real-world failure mode: the agent writes `sprints: |` (YAML literal block)
    instead of `sprints:` followed by a plain list — this turns the value into one
    string instead of a list of mappings. The drop reason must name the mistake,
    not just say "must be a list", so a retry (or a human) can actually fix it."""
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  sprint plan\n"
        "sprints: |\n"
        "  - name: Sprint 6\n"
        "    goal: test\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.sprints == []
    assert result.sprints_dropped is True
    assert "list" in result.sprints_dropped_reason
    assert "sprints: |" in result.sprints_dropped_reason


def test_tickets_literal_block_mistake_gets_diagnostic_hint():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  breakdown\n"
        "tickets: |\n"
        "  - title: sub-task\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.tickets_dropped is True
    assert "tickets: |" in result.tickets_dropped_reason


def test_artifacts_parsed_for_any_role():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  wrote the PRD\n"
        "artifacts:\n"
        "  - path: docs/PRD.md\n"
        "    group: Dokumen Teknis\n"
        "    description: initial PRD\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert len(result.artifacts) == 1
    assert result.artifacts[0].path == "docs/PRD.md"
    assert result.artifacts[0].group == "Dokumen Teknis"
    assert result.artifacts[0].description == "initial PRD"


def test_artifacts_missing_path_or_group_skipped():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  partial artifacts\n"
        "artifacts:\n"
        "  - group: Dokumen Teknis\n"
        "  - path: docs/PRD.md\n"
        "  - path: docs/TSD.md\n"
        "    group: Dokumen Teknis\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert len(result.artifacts) == 1
    assert result.artifacts[0].path == "docs/TSD.md"


def test_artifacts_absent_defaults_to_empty_list():
    text = _wrap("status: in_progress\nsummary: |\n  nothing to publish\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.artifacts == []
    assert result.artifacts_dropped is False


def test_artifacts_non_list_dropped_and_recorded():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  malformed artifacts\n"
        "artifacts: not-a-list\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.artifacts == []
    assert result.artifacts_dropped is True
    assert "list" in result.artifacts_dropped_reason


def test_artifact_updates_parsed_for_pm():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  organized artifacts\n"
        "artifact_updates:\n"
        "  - op: rename\n"
        "    group: Dokumen Teknis\n"
        "    to: Docs\n"
        "  - op: merge\n"
        "    from: Hasil Testing\n"
        "    into: QA Reports\n"
        "  - op: move\n"
        "    group: Dokumen Teknis\n"
        "    file: PRD.md\n"
        "    to: Docs\n"
        "  - op: delete\n"
        "    group: Kelompok Kosong\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.artifact_updates_dropped is False
    assert len(result.artifact_updates) == 4
    assert result.artifact_updates[0].op == "rename"
    assert result.artifact_updates[0].group == "Dokumen Teknis"
    assert result.artifact_updates[0].to == "Docs"
    assert result.artifact_updates[1].op == "merge"
    assert result.artifact_updates[1].from_group == "Hasil Testing"
    assert result.artifact_updates[1].into == "QA Reports"
    assert result.artifact_updates[2].op == "move"
    assert result.artifact_updates[2].file == "PRD.md"
    assert result.artifact_updates[3].op == "delete"


def test_artifact_updates_from_non_pm_dropped_and_recorded():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  implemented\n"
        "artifact_updates:\n"
        "  - op: rename\n"
        "    group: A\n"
        "    to: B\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.artifact_updates == []
    assert result.artifact_updates_dropped is True
    assert "may_manage_artifacts" in result.artifact_updates_dropped_reason


def test_artifact_updates_malformed_and_unknown_ops_skipped():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  partial updates\n"
        "artifact_updates:\n"
        "  - op: explode\n"
        "    group: A\n"
        "  - group: B\n"
        "  - op: rename\n"
        "    group: A\n"
        "    to: C\n"
    )
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert len(result.artifact_updates) == 1
    assert result.artifact_updates[0].op == "rename"
    assert result.artifact_updates[0].to == "C"


def test_artifact_updates_absent_defaults_to_empty_list():
    text = _wrap("status: in_progress\nsummary: |\n  nothing to organize\n")
    result = _parse(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.artifact_updates == []
    assert result.artifact_updates_dropped is False


def test_routine_mode_rejects_status():
    text = _wrap(
        "status: done\n"
        "summary: |\n"
        "  routine work\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, no_ticket_mode=True)
    assert result.ok is False
    assert "status" in result.reason


def test_routine_mode_comments_parsed():
    text = _wrap(
        "summary: |\n"
        "  checked tickets\n"
        "comments:\n"
        "  - ticket: MAP-002\n"
        "    body: |\n"
        "      Tiket ini tidak bergerak, tolong dicek.\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, no_ticket_mode=True)
    assert result.ok is True
    assert len(result.comments) == 1
    assert result.comments[0].ticket_key == "MAP-002"
    assert "tidak bergerak" in result.comments[0].body


def test_routine_mode_comments_malformed_skipped():
    text = _wrap(
        "summary: |\n"
        "  partial\n"
        "comments:\n"
        "  - ticket: MAP-002\n"
        "  - body: no ticket key\n"
        "  - ticket: MAP-003\n"
        "    body: valid one\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, no_ticket_mode=True)
    assert result.ok is True
    assert len(result.comments) == 1
    assert result.comments[0].ticket_key == "MAP-003"


def test_comments_rejected_in_normal_ticket_run():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  implemented\n"
        "comments:\n"
        "  - ticket: MAP-002\n"
        "    body: hi\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.comments == []
    assert result.comments_dropped is True
    assert "routine/chat" in result.comments_dropped_reason


def test_memory_parsed_for_any_role():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  implemented\n"
        "memory:\n"
        "  - jangan lupa jalankan migrasi sebelum test\n"
        "  - repo ini pakai uv, bukan pip langsung\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.memories == [
        "jangan lupa jalankan migrasi sebelum test",
        "repo ini pakai uv, bukan pip langsung",
    ]
    assert result.memories_dropped is False


def test_memory_single_string_treated_as_list():
    text = _wrap(
        "status: review\nsummary: |\n  ok\nmemory: satu catatan saja\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.memories == ["satu catatan saja"]


def test_memory_empty_and_non_string_entries_skipped():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  ok\n"
        "memory:\n"
        "  - \"\"\n"
        "  - 42\n"
        "  - valid note\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.memories == ["valid note"]


def test_memory_note_truncated_to_max_length():
    long_note = "x" * 1000
    text = _wrap(f"status: review\nsummary: |\n  ok\nmemory:\n  - {long_note}\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert len(result.memories[0]) == 500


def test_memory_absent_defaults_to_empty_list():
    text = _wrap("status: in_progress\nsummary: |\n  nothing to remember\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.memories == []
    assert result.memories_dropped is False


def test_memory_non_list_dropped_and_recorded():
    text = _wrap(
        "status: in_progress\nsummary: |\n  malformed memory\nmemory:\n  foo: bar\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.memories == []
    assert result.memories_dropped is True
    assert "list" in result.memories_dropped_reason


def test_dropped_notes_collects_every_dropped_reason():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  multiple mistakes\n"
        "tickets: |\n"
        "  - title: x\n"
        "sprints: |\n"
        "  - name: Sprint 1\n"
    )
    result = _parse(text, "engineer", VALID_AGENTS)
    notes = result.dropped_notes()
    # engineer's role has may_declare_tickets=false, so tickets:/sprints: are
    # dropped for the role gate, not the malformed-shape branch — either way,
    # both reasons must show up here uncollapsed.
    assert len(notes) == 2
    assert result.tickets_dropped_reason in notes
    assert result.sprints_dropped_reason in notes


def test_dropped_notes_empty_when_nothing_dropped():
    text = _wrap("status: in_progress\nsummary: |\n  clean run\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.dropped_notes() == []


def test_no_ticket_mode_rejects_status():
    text = _wrap("status: in_progress\nsummary: |\n  x\n")
    result = _parse(text, "engineer", VALID_AGENTS, no_ticket_mode=True)
    assert result.ok is False
    assert "status" in result.reason


def test_no_ticket_mode_rejects_mention():
    text = _wrap("summary: |\n  x\nmention: [eng-1]\n")
    result = _parse(text, "engineer", VALID_AGENTS, no_ticket_mode=True)
    assert result.ok is False
    assert "mention" in result.reason


def test_missing_status_rejected():
    text = _wrap("summary: |\n  x\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is False
    assert "status" in result.reason


def test_unknown_role_rejected():
    text = _wrap("status: in_progress\nsummary: |\n  x\n")
    result = _parse(text, "engineer", VALID_AGENTS, valid_roles={"pm", "lead"})
    assert result.ok is False
    assert "role" in result.reason


def test_mention_as_string_normalized_to_list():
    text = _wrap("status: in_progress\nsummary: |\n  x\nmention: eng-1\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.valid_mentions == ["eng-1"]


def test_mention_non_list_rejected():
    text = _wrap("status: in_progress\nsummary: |\n  x\nmention: {a: 1}\n")
    result = _parse(text, "engineer", VALID_AGENTS)
    assert result.ok is False
    assert "mention" in result.reason


def test_sprints_malformed_entries_dropped():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n  x\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
        "  - not-a-mapping\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, may_declare_tickets=True)
    assert result.ok is True
    assert len(result.sprints) == 1
    assert result.sprints_dropped is True
    assert "malformed" in result.sprints_dropped_reason


def test_updates_non_list_dropped():
    text = _wrap(
        "status: in_progress\nsummary: |\n  x\nupdates: |\n  - ticket: MAP-1\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, may_declare_tickets=True)
    assert result.ok is True
    assert result.updates == []
    assert result.updates_dropped is True
    assert "list" in result.updates_dropped_reason


def test_artifact_updates_non_list_dropped():
    text = _wrap(
        "status: in_progress\nsummary: |\n  x\nartifact_updates: |\n  - op: rename\n"
    )
    result = _parse(text, "pm", VALID_AGENTS, may_manage_artifacts=True)
    assert result.ok is True
    assert result.artifact_updates == []
    assert result.artifact_updates_dropped is True
    assert "list" in result.artifact_updates_dropped_reason


def test_comments_non_list_dropped():
    text = _wrap("summary: |\n  x\ncomments: |\n  - ticket: MAP-1\n")
    result = _parse(text, "engineer", VALID_AGENTS, no_ticket_mode=True)
    assert result.ok is True
    assert result.comments == []
    assert result.comments_dropped is True
    assert "list" in result.comments_dropped_reason
