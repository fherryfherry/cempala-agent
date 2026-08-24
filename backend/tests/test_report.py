"""Tests for the ```map block parser (MAP-018). See docs/02-tsd.md §4.3/§10.1
and docs/03-agent-design.md §3/§6 for the rules under test.
"""

from app.core.report import parse_report

VALID_AGENTS = {"lead-1", "eng-1", "eng-2", "pm-1", "qa-1", "pentester-1"}


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
    result = parse_report(text, "pm", VALID_AGENTS, ticket_approved=False)
    assert result.ok is True
    assert result.tickets == []
    assert result.tickets_dropped is True
    assert "menyetujui" in result.tickets_dropped_reason


def test_pm_tickets_allowed_with_approval():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  approved plan\n"
        "tickets:\n"
        "  - title: sub-task\n"
        "    assignee: eng-1\n"
    )
    result = parse_report(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.ok is True
    assert result.tickets_dropped is False
    assert len(result.tickets) == 1


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
    result = parse_report(text, "qa", VALID_AGENTS, ticket_approved=False)
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
    result = parse_report(text, "pm", VALID_AGENTS, ticket_approved=True)
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
    result = parse_report(text, "pm", VALID_AGENTS, ticket_approved=True)
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
    result = parse_report(text, "pm", VALID_AGENTS, ticket_approved=True)
    assert result.tickets[0].epic == "AUTH-001"
    assert result.tickets[1].epic is None


def test_missing_block():
    result = parse_report("no fenced block here at all", "engineer", VALID_AGENTS)
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
    result = parse_report(text, "engineer", VALID_AGENTS, actor_name="eng-1")
    assert result.ok is True
    assert result.status == "review"
    assert result.valid_mentions == ["lead-1"]
    assert result.unknown_mentions == []
    assert "validasi email" in result.summary
    assert result.tickets == []
    assert result.tickets_dropped is False


def test_malformed_yaml():
    text = _wrap("status: review\nmention: [lead-1\nsummary: broken\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is False
    assert "yaml" in result.reason.lower()


def test_multiple_blocks_last_wins():
    text = (
        "```map\nstatus: blocked\nsummary: |\n  first block, should be ignored\n```\n"
        "some more agent output in between\n"
        "```map\nstatus: review\nsummary: |\n  second block, should be used\n```"
    )
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.status == "review"
    assert "second block" in result.summary


def test_missing_summary():
    text = _wrap("status: review\nmention: [lead-1]\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is False
    assert "summary" in result.reason.lower()


def test_empty_summary():
    text = _wrap("status: review\nsummary: ''\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is False


def test_release_cannot_be_declared_by_any_role():
    for role in ("pm", "lead", "engineer", "designer", "qa", "pentester"):
        text = _wrap("status: release\nsummary: |\n  trying to release myself\n")
        result = parse_report(text, role, VALID_AGENTS)
        assert result.ok is False, role
        assert "release" in result.reason


def test_legal_status_variety():
    # Any role may now declare any non-release status (owner request: the old
    # per-role status matrix kept producing false blocks on the kanban board).
    r = parse_report(_wrap("status: review\nsummary: |\n  pm can set review too now\n"), "pm", VALID_AGENTS)
    assert r.ok and r.status == "review"
    r = parse_report(_wrap("status: done\nsummary: |\n  lead can close directly now\n"), "lead", VALID_AGENTS)
    assert r.ok and r.status == "done"
    r = parse_report(_wrap("status: done\nsummary: |\n  engineer can close directly now\n"), "engineer", VALID_AGENTS)
    assert r.ok and r.status == "done"
    # QA
    r = parse_report(_wrap("status: security\nsummary: |\n  all tests pass\n"), "qa", VALID_AGENTS)
    assert r.ok and r.status == "security"
    # Pentester
    r = parse_report(_wrap("status: done\nsummary: |\n  audit clean\n"), "pentester", VALID_AGENTS)
    assert r.ok and r.status == "done"


def test_unknown_status_value():
    text = _wrap("status: not_a_real_status\nsummary: |\n  whoops\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is False


def test_unknown_mention_dropped_and_recorded():
    text = _wrap("status: review\nmention: [lead-1, ghost-agent]\nsummary: |\n  done\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.valid_mentions == ["lead-1"]
    assert result.unknown_mentions == ["ghost-agent"]


def test_self_mention_dropped_not_unknown():
    text = _wrap("status: review\nmention: [eng-1, lead-1]\nsummary: |\n  done\n")
    result = parse_report(text, "engineer", VALID_AGENTS, actor_name="eng-1")
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
    result = parse_report(text, "engineer", VALID_AGENTS)
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
    result = parse_report(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.tickets_dropped is False
    assert len(result.tickets) == 2
    assert result.tickets[0].title == "Endpoint POST /auth/login"
    assert result.tickets[0].assignee == "eng-1"
    assert result.tickets[0].priority == "high"
    # second ticket has no explicit priority -> reasonable default
    assert result.tickets[1].priority == "medium"


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
    result = parse_report(text, "qa", VALID_AGENTS)
    assert result.ok is True
    assert len(result.tickets) == 1
    assert result.tickets[0].title == "valid one"


def test_not_a_mapping_yaml():
    text = _wrap("- just\n- a\n- list\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
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
    result = parse_report(text, "pm", VALID_AGENTS)
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
    result = parse_report(text, "pm", VALID_AGENTS)
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
    result = parse_report(text, "qa", VALID_AGENTS)
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
    result = parse_report(text, "engineer", VALID_AGENTS)
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
    result = parse_report(text, "pm", VALID_AGENTS, ticket_approved=True)
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
    result = parse_report(text, "pm", VALID_AGENTS, ticket_approved=False)
    assert result.ok is True
    assert result.sprints == []
    assert result.sprints_dropped is True
    assert "menyetujui" in result.sprints_dropped_reason


def test_sprints_from_unauthorized_role_dropped():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  implemented\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
    )
    result = parse_report(text, "engineer", VALID_AGENTS)
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
    result = parse_report(text, "qa", VALID_AGENTS, sprint_creator_roles={"pm", "qa"})
    assert result.ok is True
    assert result.sprints_dropped is False
    assert len(result.sprints) == 1
    assert result.sprints[0].name == "Sprint 1"


def test_sprints_dropped_for_role_not_in_sprint_creator_roles():
    text = _wrap(
        "status: in_progress\n"
        "summary: |\n"
        "  sprint plan\n"
        "sprints:\n"
        "  - name: Sprint 1\n"
    )
    result = parse_report(text, "qa", VALID_AGENTS, sprint_creator_roles={"pm"})
    assert result.ok is True
    assert result.sprints == []
    assert result.sprints_dropped is True
    assert "only pm" in result.sprints_dropped_reason or "allowed" in result.sprints_dropped_reason


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
    result = parse_report(text, "pm", VALID_AGENTS)
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
    result = parse_report(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert len(result.artifacts) == 1
    assert result.artifacts[0].path == "docs/TSD.md"


def test_artifacts_absent_defaults_to_empty_list():
    text = _wrap("status: in_progress\nsummary: |\n  nothing to publish\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
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
    result = parse_report(text, "pm", VALID_AGENTS)
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
    result = parse_report(text, "pm", VALID_AGENTS)
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
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.artifact_updates == []
    assert result.artifact_updates_dropped is True
    assert "only pm" in result.artifact_updates_dropped_reason


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
    result = parse_report(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert len(result.artifact_updates) == 1
    assert result.artifact_updates[0].op == "rename"
    assert result.artifact_updates[0].to == "C"


def test_artifact_updates_absent_defaults_to_empty_list():
    text = _wrap("status: in_progress\nsummary: |\n  nothing to organize\n")
    result = parse_report(text, "pm", VALID_AGENTS)
    assert result.ok is True
    assert result.artifact_updates == []
    assert result.artifact_updates_dropped is False


def test_routine_mode_rejects_status():
    text = _wrap(
        "status: done\n"
        "summary: |\n"
        "  routine work\n"
    )
    result = parse_report(text, "pm", VALID_AGENTS, routine_mode=True)
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
    result = parse_report(text, "pm", VALID_AGENTS, routine_mode=True)
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
    result = parse_report(text, "pm", VALID_AGENTS, routine_mode=True)
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
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.comments == []
    assert result.comments_dropped is True
    assert "rutinitas" in result.comments_dropped_reason


def test_memory_parsed_for_any_role():
    text = _wrap(
        "status: review\n"
        "summary: |\n"
        "  implemented\n"
        "memory:\n"
        "  - jangan lupa jalankan migrasi sebelum test\n"
        "  - repo ini pakai uv, bukan pip langsung\n"
    )
    result = parse_report(text, "engineer", VALID_AGENTS)
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
    result = parse_report(text, "engineer", VALID_AGENTS)
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
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.memories == ["valid note"]


def test_memory_note_truncated_to_max_length():
    long_note = "x" * 1000
    text = _wrap(f"status: review\nsummary: |\n  ok\nmemory:\n  - {long_note}\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert len(result.memories[0]) == 500


def test_memory_absent_defaults_to_empty_list():
    text = _wrap("status: in_progress\nsummary: |\n  nothing to remember\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.memories == []
    assert result.memories_dropped is False


def test_memory_non_list_dropped_and_recorded():
    text = _wrap(
        "status: in_progress\nsummary: |\n  malformed memory\nmemory:\n  foo: bar\n"
    )
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is True
    assert result.memories == []
    assert result.memories_dropped is True
    assert "list" in result.memories_dropped_reason
