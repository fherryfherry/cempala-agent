from app.agents.prompts import (
    DEFAULT_ROLE_PROMPTS,
    AgentInfo,
    CommentInfo,
    TicketInfo,
    WorkspaceTicketSummary,
    build_prompt,
)
from app.core.state_machine import STATUSES

ROSTER = [
    AgentInfo(name="pm-1", role="pm", label="Project Manager", may_declare_tickets=True, may_manage_artifacts=True),
    AgentInfo(name="lead-1", role="lead", label="Lead Engineer", is_reviewer=True),
    AgentInfo(name="eng-1", role="engineer", label="Engineer"),
    AgentInfo(name="designer-1", role="designer", label="Designer"),
    AgentInfo(name="qa-1", role="qa", label="QA", may_declare_tickets=True, is_reviewer=True),
    AgentInfo(name="pentester-1", role="pentester", label="Security Reviewer", may_declare_tickets=True, is_reviewer=True),
]

TICKET = TicketInfo(
    key="MAP-001",
    title="Bikin halaman login",
    status="in_progress",
    priority="high",
    description="Tambah form login dengan validasi email.",
)


def _agent(name: str, role: str, system_prompt: str | None = None) -> AgentInfo:
    """AgentInfo with the builtin role's default flags/label filled in, mirroring
    the role seed — tests override flags explicitly where the behavior under
    test differs."""
    flags = {
        "pm": {"label": "Project Manager", "may_declare_tickets": True, "may_manage_artifacts": True},
        "lead": {"label": "Lead Engineer", "is_reviewer": True},
        "engineer": {"label": "Engineer"},
        "designer": {"label": "Designer"},
        "qa": {"label": "QA", "may_declare_tickets": True, "is_reviewer": True},
        "pentester": {"label": "Security Reviewer", "may_declare_tickets": True, "is_reviewer": True},
        "business_analyst": {"label": "Business Analyst", "may_declare_tickets": True},
        "system_architect": {"label": "System Architect", "is_reviewer": True},
        # Custom roles get no flags unless the test sets them.
    }
    return AgentInfo(
        name=name, role=role, system_prompt=system_prompt, **flags.get(role, {})
    )


def test_base_always_present():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "seorang Engineer di tim software" in prompt
    assert "/repo" in prompt
    assert "eng-1" in prompt
    assert "pm-1 (Project Manager)" in prompt


def test_base_prompt_forbids_working_non_active_sprint_tickets():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "HANYA boleh mengerjakan tiket yang berada di sprint AKTIF" in prompt
    assert "tiket backlog atau sprint yang belum aktif TIDAK boleh dikerjakan" in prompt
    assert "status: blocked" in prompt


def test_sprint_creator_role_gets_triage_exemption():
    prompt = build_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        TICKET,
        sprint_creator_roles={"pm"},
    )
    assert "PENGECUALIAN untukmu (kamu penyusun sprint)" in prompt
    assert "triase/planning" in prompt


def test_non_sprint_creator_role_gets_no_exemption():
    for role in ("lead", "engineer", "designer", "qa", "pentester"):
        prompt = build_prompt(
            _agent(f"{role}-1", role),
            "/repo",
            ROSTER,
            TICKET,
            sprint_creator_roles={"pm"},
        )
        assert "PENGECUALIAN untukmu" not in prompt


def test_ticket_context_shows_active_sprint():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TicketInfo(
            key="MAP-001",
            title="Bikin halaman login",
            status="in_progress",
            priority="high",
            description="x",
            sprint_name="Sprint 1",
            sprint_active=True,
        ),
    )
    assert "Sprint: Sprint 1 (AKTIF)" in prompt


def test_ticket_context_shows_non_active_sprint():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TicketInfo(
            key="MAP-001",
            title="Bikin halaman login",
            status="in_progress",
            priority="high",
            description="x",
            sprint_name="Sprint 3",
            sprint_active=False,
        ),
    )
    assert "Sprint: Sprint 3 (BELUM AKTIF)" in prompt


def test_ticket_context_shows_backlog_when_no_sprint():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "Sprint: (tidak ada — backlog)" in prompt


def test_default_role_block_used_when_no_system_prompt():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert DEFAULT_ROLE_PROMPTS["engineer"] in prompt


def test_custom_system_prompt_replaces_role_block_not_base_or_contract():
    custom = "Kamu Engineer khusus. Lakukan X saja."
    prompt = build_prompt(_agent("eng-1", "engineer", custom), "/repo", ROSTER, TICKET)
    assert custom in prompt
    assert DEFAULT_ROLE_PROMPTS["engineer"] not in prompt
    # BASE still present
    assert "seorang Engineer di tim software" in prompt
    # map contract still present
    assert "```map" in prompt
    assert "status: <salah satu dari:" in prompt


def test_role_prompt_fallback_via_agent_info():
    """Dynamic roles: the orchestrator passes the role row's system_prompt as
    AgentInfo.system_prompt when the agent's own is null. A custom role's prompt
    (absent from DEFAULT_ROLE_PROMPTS) must be used as the role block, and the
    roster/base block shows the role's label."""
    role_prompt = "Kamu Scrum Master. Fasilitasi sprint."
    agent = AgentInfo(
        name="scrum-1",
        role="scrum_master",
        system_prompt=role_prompt,
        label="Scrum Master",
    )
    prompt = build_prompt(agent, "/repo", ROSTER, TICKET)
    assert role_prompt in prompt
    assert "seorang Scrum Master di tim software" in prompt
    # Roster member labels come from the role rows too.
    roster_prompt = build_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        [*ROSTER, agent],
        TICKET,
    )
    assert "scrum-1 (Scrum Master)" in roster_prompt


def test_custom_role_prompt_fallback_contracts_gated_by_flags():
    """A custom role with no flags gets no tickets[]/artifact_updates/anti-loop
    blocks even though the parser would gate those on the same flags."""
    agent = AgentInfo(name="scrum-1", role="scrum_master", system_prompt="x", label="Scrum Master")
    prompt = build_prompt(agent, "/repo", ROSTER, TICKET, review_round=2)
    assert "tickets:" not in prompt
    assert "artifact_updates:" not in prompt
    assert "Ini review ke-" not in prompt


def test_map_contract_status_list_is_unrestricted():
    expected = ", ".join(sorted(STATUSES))
    for role in ("pm", "lead", "engineer", "designer", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert f"status: <salah satu dari: {expected}>" in prompt


def test_tickets_instruction_present_for_pm_qa_pentester():
    for role in ("pm", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "tickets:" in prompt


def test_tickets_title_guidance_present_for_pm_qa_pentester():
    for role in ("pm", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "JANGAN cantumkan path file" in prompt


def test_tickets_instruction_absent_for_lead_engineer_designer():
    for role in ("lead", "engineer", "designer"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "tickets:" not in prompt


def test_tickets_instruction_present_for_business_analyst():
    prompt = build_prompt(_agent("ba-1", "business_analyst"), "/repo", ROSTER, TICKET)
    assert "tickets:" in prompt


def test_tickets_instruction_absent_for_system_architect():
    prompt = build_prompt(_agent("arch-1", "system_architect"), "/repo", ROSTER, TICKET)
    assert "tickets:" not in prompt
    assert "tickets[]" not in prompt


def test_default_role_block_present_for_new_roles():
    prompt_ba = build_prompt(_agent("ba-1", "business_analyst"), "/repo", ROSTER, TICKET)
    assert DEFAULT_ROLE_PROMPTS["business_analyst"] in prompt_ba
    prompt_arch = build_prompt(_agent("arch-1", "system_architect"), "/repo", ROSTER, TICKET)
    assert DEFAULT_ROLE_PROMPTS["system_architect"] in prompt_arch


def test_engineer_prompt_never_contains_tickets_instruction_default_and_custom():
    default_prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    custom_prompt = build_prompt(
        _agent("eng-1", "engineer", "Custom role text with no ticket mention."),
        "/repo",
        ROSTER,
        TICKET,
    )
    for prompt in (default_prompt, custom_prompt):
        assert "tickets:" not in prompt
        assert "tickets[]" not in prompt


def test_anti_loop_absent_when_review_round_zero():
    prompt = build_prompt(_agent("lead-1", "lead"), "/repo", ROSTER, TICKET, review_round=0)
    assert "Ini review ke-" not in prompt


def test_anti_loop_present_for_reviewer_roles_with_review_round():
    for role in ("lead", "qa", "pentester"):
        prompt = build_prompt(
            _agent(f"{role}-x", role),
            "/repo",
            ROSTER,
            TICKET,
            review_round=2,
            previous_review_feedback=["Validasi email hilang di form login."],
        )
        assert "Ini review ke-2 untuk tiket ini." in prompt
        assert "Validasi email hilang di form login." in prompt
        assert (
            "Kalau masalah yang sama masih ada setelah dua kali diminta perbaiki, "
            "JANGAN meminta lagi." in prompt
        )
        assert "status: blocked, dan jelaskan kenapa perbaikannya tidak berhasil." in prompt


def test_anti_loop_present_for_system_architect_with_review_round():
    prompt = build_prompt(
        _agent("arch-1", "system_architect"),
        "/repo",
        ROSTER,
        TICKET,
        review_round=2,
        previous_review_feedback=["Rancangan belum menangani retry."],
    )
    assert "Ini review ke-2 untuk tiket ini." in prompt


def test_anti_loop_absent_for_business_analyst_even_with_review_round():
    prompt = build_prompt(
        _agent("ba-1", "business_analyst"),
        "/repo",
        ROSTER,
        TICKET,
        review_round=2,
        previous_review_feedback=["x"],
    )
    assert "Ini review ke-" not in prompt


def test_anti_loop_absent_for_non_reviewer_roles_even_with_review_round():
    for role in ("pm", "engineer", "designer"):
        prompt = build_prompt(
            _agent(f"{role}-x", role),
            "/repo",
            ROSTER,
            TICKET,
            review_round=2,
            previous_review_feedback=["some feedback"],
        )
        assert "Ini review ke-" not in prompt


def test_ticket_context_fields_present():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        attachments=["spec.png"],
        recent_comments=[CommentInfo(author="pm-1", body="Tolong prioritaskan", created_at="t1")],
    )
    assert "MAP-001 — Bikin halaman login" in prompt
    assert "Status: in_progress | Prioritas: high" in prompt
    assert "Tambah form login dengan validasi email." in prompt
    assert "spec.png" in prompt
    assert "pm-1 (t1): Tolong prioritaskan" in prompt


def test_extra_instructions_included_when_provided():
    prompt = build_prompt(
        _agent("pm-1", "pm"), "/repo", ROSTER, TICKET, extra_instructions="foo marker text"
    )
    assert "foo marker text" in prompt


def test_extra_instructions_absent_by_default_output_unchanged():
    with_none = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET, extra_instructions=None)
    without_param = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert with_none == without_param


def test_updates_instruction_present_for_pm_qa_pentester():
    for role in ("pm", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "updates:" in prompt


def test_updates_instruction_absent_for_lead_engineer_designer():
    for role in ("engineer", "designer"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        contract_start = prompt.rfind("```map")
        contract_block = prompt[contract_start:]
        assert "updates:" not in contract_block


def test_previous_run_summaries_included():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        previous_summaries=["Menambah endpoint login.", "Menambah validasi email."],
    )
    assert "Menambah endpoint login." in prompt
    assert "Menambah validasi email." in prompt


def test_workspace_tickets_block_omitted_when_none():
    prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "Tiket lain di workspace ini" not in prompt


def test_workspace_tickets_block_included_when_given():
    tickets = [
        WorkspaceTicketSummary(
            key="MAP-002", title="Fitur artikel", status="done", priority="medium",
            sprint_name="Sprint 1",
        ),
        WorkspaceTicketSummary(
            key="MAP-003", title="Fix bug login", status="blocked", priority="high",
            sprint_name=None,
        ),
    ]
    prompt = build_prompt(
        _agent("pm-1", "pm"), "/repo", ROSTER, TICKET, workspace_tickets=tickets,
    )
    assert "Tiket lain di workspace ini" in prompt
    assert "MAP-002 [done] (sprint: Sprint 1) — Fitur artikel" in prompt
    assert "MAP-003 [blocked] (sprint: tanpa sprint) — Fix bug login" in prompt


def test_updates_example_has_sprint_and_duration_fields_for_pm_qa_pentester():
    for role in ("pm", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "sprint: <opsional, pindahkan tiket ini ke sprint lain>" in prompt
        assert "duration: <opsional, perbaiki estimasi durasi tiket ini dalam" in prompt


def test_existing_artifact_groups_listed_in_contract():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        existing_artifact_groups=["Dokumen Teknis", "Hasil Testing", "Sprint Reports"],
    )
    assert "Kelompok yang SUDAH ADA di menu Artifacts workspace ini:" in prompt
    assert "- Dokumen Teknis" in prompt
    assert "- Hasil Testing" in prompt
    assert "- Sprint Reports" in prompt
    assert "WAJIB pakai salah satu kelompok di atas yang relevan" in prompt
    assert "JANGAN bikin nama baru kalau ada yang relevan" in prompt


def test_no_artifact_groups_tells_agent_they_may_create_first():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "kamu boleh membuat kelompok pertama" in prompt
    assert "Kelompok yang SUDAH ADA" not in prompt


def test_existing_epics_listed_in_contract_for_pm():
    prompt = build_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        TICKET,
        existing_epics=["AUTH-001 — Authentication", "BILLING-002 — Billing"],
    )
    assert "Epic yang SUDAH ADA (tiket top-level) di workspace ini:" in prompt
    assert "AUTH-001 — Authentication" in prompt
    assert "BILLING-002 — Billing" in prompt
    assert "WAJIB isi `epic:`" in prompt
    assert "epic: <opsional, key epic tujuan dari daftar di atas" in prompt


def test_no_existing_epics_tells_agent_first_one_becomes_epic():
    prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "akan jadi epic pertama" in prompt
    assert "Epic yang SUDAH ADA" not in prompt


def test_existing_epics_hidden_for_roles_without_tickets():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        existing_epics=["AUTH-001 — Authentication"],
    )
    assert "AUTH-001" not in prompt
    assert "epic:" not in prompt


def test_existing_sprints_listed_in_contract_for_pm():
    prompt = build_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        TICKET,
        existing_sprints=["Sprint 1", "Sprint 2"],
    )
    assert "Sprint yang SUDAH ADA:" in prompt
    assert "- Sprint 1" in prompt
    assert "- Sprint 2" in prompt
    assert "Sprint HANYA timebox" in prompt
    assert "JANGAN taruh nama fitur/scope di nama sprint" in prompt


def test_no_existing_sprints_tells_agent_pure_timebox_naming():
    prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "Belum ada sprint di workspace ini" in prompt
    assert "Sprint yang SUDAH ADA" not in prompt


def test_artifact_catalog_block_included_when_given():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        artifact_catalog=[
            "[Dokumen Teknis] PRD.md (MAP-001) — initial PRD",
            "[Hasil Testing] evidence.md (MAP-002)",
        ],
    )
    assert "Artifacts di workspace ini (menu Artifacts)" in prompt
    assert "- [Dokumen Teknis] PRD.md (MAP-001) — initial PRD" in prompt
    assert "- [Hasil Testing] evidence.md (MAP-002)" in prompt


def test_artifact_catalog_block_omitted_when_empty():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "Artifacts di workspace ini" not in prompt


def test_artifact_updates_contract_present_for_pm_only():
    pm_prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "artifact_updates:" in pm_prompt
    assert "op: rename" in pm_prompt
    assert "op: merge" in pm_prompt
    assert "op: move" in pm_prompt
    assert "op: delete" in pm_prompt
    assert "HANYA PM" in pm_prompt
    for role in ("lead", "engineer", "designer", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "artifact_updates:" not in prompt


def test_sprints_contract_taught_only_to_sprint_creator_roles():
    # Default: PM only.
    pm_prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "sprints:" in pm_prompt
    qa_prompt = build_prompt(_agent("qa-1", "qa"), "/repo", ROSTER, TICKET)
    assert "sprints:" not in qa_prompt

    # Workspace setting widens it: QA gets the sprints: contract too.
    qa_prompt2 = build_prompt(
        _agent("qa-1", "qa"), "/repo", ROSTER, TICKET, sprint_creator_roles={"pm", "qa"}
    )
    assert "sprints:" in qa_prompt2
    # And a role outside the allowed set still doesn't see it.
    eng_prompt = build_prompt(
        _agent("eng-1", "engineer"), "/repo", ROSTER, TICKET, sprint_creator_roles={"pm", "qa"}
    )
    assert "sprints:" not in eng_prompt


def test_mcp_tools_block_in_normal_prompt():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "list_tickets" in prompt
    assert "post_comment" in prompt
    assert "update_ticket" in prompt
    assert "updates:" in prompt


def test_mcp_tools_block_in_routine_prompt():
    from app.agents.prompts import build_routine_prompt

    prompt = build_routine_prompt(
        _agent("pm-1", "pm"), "/repo", ROSTER, routine_prompt="cek tiket macet"
    )
    assert "list_tickets" in prompt
    assert "update_ticket" in prompt
    assert "updates:" in prompt


def test_routine_prompt_teaches_at_mention_syntax_in_comments():
    from app.agents.prompts import build_routine_prompt

    prompt = build_routine_prompt(
        _agent("pm-1", "pm"), "/repo", ROSTER, routine_prompt="cek tiket macet"
    )
    assert '"@lead-1"' in prompt
    assert "JANGAN pernah memanggil agent dengan `@` di dalam blok" in prompt


def test_agent_memory_block_absent_by_default():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "Catatan dari pekerjaanmu sebelumnya" not in prompt


def test_agent_memory_block_present_when_given():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        agent_memories=["jangan lupa jalankan migrasi", "repo ini pakai uv"],
    )
    assert "Catatan dari pekerjaanmu sebelumnya" in prompt
    assert "- jangan lupa jalankan migrasi" in prompt
    assert "- repo ini pakai uv" in prompt


def test_agent_memory_block_appears_before_ticket_context():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        agent_memories=["catatan lama"],
    )
    assert prompt.index("Catatan dari pekerjaanmu sebelumnya") < prompt.index("Tiket saat ini:")


def test_chat_prompt_teaches_at_mention_syntax_in_comments():
    from app.agents.prompts import build_chat_prompt

    prompt = build_chat_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        conversation_title="Diskusi fitur",
        messages=[],
    )
    assert '"@lead-1"' in prompt
    assert "JANGAN pernah memanggil agent dengan `@` di dalam blok" in prompt


def test_map_contract_mentions_memory_field():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "memory:" in prompt


def test_base_block_teaches_at_mention_syntax_in_text():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "@lead-1" in prompt
    assert "tanpa `@` itu cuma teks biasa" in prompt


def test_map_contract_clarifies_no_at_in_mention_field():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "mention: [<nama agent dari daftar tim:" in prompt
    assert "NAMA SAJA, tanpa @" in prompt
    assert "TANPA tanda `@`" in prompt
