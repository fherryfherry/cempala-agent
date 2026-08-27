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
    title="Build the login page",
    status="in_progress",
    priority="high",
    description="Add a login form with email validation.",
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
    assert "a Engineer on the software team" in prompt
    assert "/repo" in prompt
    assert "eng-1" in prompt
    assert "pm-1 (Project Manager)" in prompt


def test_base_prompt_forbids_working_non_active_sprint_tickets():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "may ONLY work tickets that are in the CURRENTLY active sprint" in prompt
    assert "backlog tickets or tickets in an inactive sprint must NOT be worked" in prompt
    assert "status: blocked" in prompt


def test_sprint_creator_role_gets_triage_exemption():
    prompt = build_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        TICKET,
        sprint_creator_roles={"pm"},
    )
    assert "EXCEPTION for you (you plan sprints)" in prompt
    assert "triage/planning" in prompt


def test_non_sprint_creator_role_gets_no_exemption():
    for role in ("lead", "engineer", "designer", "qa", "pentester"):
        prompt = build_prompt(
            _agent(f"{role}-1", role),
            "/repo",
            ROSTER,
            TICKET,
            sprint_creator_roles={"pm"},
        )
        assert "EXCEPTION for you" not in prompt


def test_ticket_context_shows_active_sprint():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TicketInfo(
            key="MAP-001",
            title="Build the login page",
            status="in_progress",
            priority="high",
            description="x",
            sprint_name="Sprint 1",
            sprint_active=True,
        ),
    )
    assert "Sprint: Sprint 1 (ACTIVE)" in prompt


def test_ticket_context_shows_non_active_sprint():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TicketInfo(
            key="MAP-001",
            title="Build the login page",
            status="in_progress",
            priority="high",
            description="x",
            sprint_name="Sprint 3",
            sprint_active=False,
        ),
    )
    assert "Sprint: Sprint 3 (NOT ACTIVE)" in prompt


def test_ticket_context_shows_backlog_when_no_sprint():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "Sprint: (none — backlog)" in prompt


def test_default_role_block_used_when_no_system_prompt():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert DEFAULT_ROLE_PROMPTS["engineer"] in prompt


def test_custom_system_prompt_replaces_role_block_not_base_or_contract():
    custom = "You are a custom Engineer. Do only X."
    prompt = build_prompt(_agent("eng-1", "engineer", custom), "/repo", ROSTER, TICKET)
    assert custom in prompt
    assert DEFAULT_ROLE_PROMPTS["engineer"] not in prompt
    # BASE still present
    assert "a Engineer on the software team" in prompt
    # map contract still present
    assert "```map" in prompt
    assert "status: <one of:" in prompt


def test_role_prompt_fallback_via_agent_info():
    """Dynamic roles: the orchestrator passes the role row's system_prompt as
    AgentInfo.system_prompt when the agent's own is null. A custom role's prompt
    (absent from DEFAULT_ROLE_PROMPTS) must be used as the role block, and the
    roster/base block shows the role's label."""
    role_prompt = "You are the Scrum Master. Facilitate the sprint."
    agent = AgentInfo(
        name="scrum-1",
        role="scrum_master",
        system_prompt=role_prompt,
        label="Scrum Master",
    )
    prompt = build_prompt(agent, "/repo", ROSTER, TICKET)
    assert role_prompt in prompt
    assert "a Scrum Master on the software team" in prompt
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
    assert "This is review round" not in prompt


def test_map_contract_status_list_is_unrestricted():
    expected = ", ".join(sorted(STATUSES))
    for role in ("pm", "lead", "engineer", "designer", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert f"status: <one of: {expected}>" in prompt


def test_tickets_instruction_present_for_pm_qa_pentester():
    for role in ("pm", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "tickets:" in prompt


def test_tickets_title_guidance_present_for_pm_qa_pentester():
    for role in ("pm", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "do NOT include file paths" in prompt


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
    assert "This is review round" not in prompt


def test_anti_loop_present_for_reviewer_roles_with_review_round():
    for role in ("lead", "qa", "pentester"):
        prompt = build_prompt(
            _agent(f"{role}-x", role),
            "/repo",
            ROSTER,
            TICKET,
            review_round=2,
            previous_review_feedback=["Email validation missing on the login form."],
        )
        assert "This is review round 2 for this ticket." in prompt
        assert "Email validation missing on the login form." in prompt
        assert (
            "If the same problem still exists after being asked to fix it twice, "
            "DON'T ask again." in prompt
        )
        assert "status: blocked, and explain why the fix isn't landing." in prompt


def test_anti_loop_present_for_system_architect_with_review_round():
    prompt = build_prompt(
        _agent("arch-1", "system_architect"),
        "/repo",
        ROSTER,
        TICKET,
        review_round=2,
        previous_review_feedback=["The design doesn't handle retries yet."],
    )
    assert "This is review round 2 for this ticket." in prompt


def test_anti_loop_absent_for_business_analyst_even_with_review_round():
    prompt = build_prompt(
        _agent("ba-1", "business_analyst"),
        "/repo",
        ROSTER,
        TICKET,
        review_round=2,
        previous_review_feedback=["x"],
    )
    assert "This is review round" not in prompt


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
        assert "This is review round" not in prompt


def test_ticket_context_fields_present():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        attachments=["spec.png"],
        recent_comments=[CommentInfo(author="pm-1", body="Please prioritize this", created_at="t1")],
    )
    assert "MAP-001 — Build the login page" in prompt
    assert "Status: in_progress | Priority: high" in prompt
    assert "Add a login form with email validation." in prompt
    assert "spec.png" in prompt
    assert "pm-1 (t1): Please prioritize this" in prompt


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
        previous_summaries=["Added the login endpoint.", "Added email validation."],
    )
    assert "Added the login endpoint." in prompt
    assert "Added email validation." in prompt


def test_workspace_tickets_block_omitted_when_none():
    prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "Other tickets in this workspace" not in prompt


def test_workspace_tickets_block_included_when_given():
    tickets = [
        WorkspaceTicketSummary(
            key="MAP-002", title="Article feature", status="done", priority="medium",
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
    assert "Other tickets in this workspace" in prompt
    assert "MAP-002 [done] (sprint: Sprint 1) — Article feature" in prompt
    assert "MAP-003 [blocked] (sprint: no sprint) — Fix bug login" in prompt


def test_updates_example_has_sprint_and_duration_fields_for_pm_qa_pentester():
    for role in ("pm", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "sprint: <optional, move this ticket to a different sprint>" in prompt
        assert "duration: <optional, PLAIN NUMBER in" in prompt


def test_duration_fields_warn_against_unit_words():
    """A real run wrote `duration: 2 minggu` and crashed report.py's float()
    conversion (fixed separately — see test_report.py's duration regression
    tests). The prompt itself must also spell out "plain number, no unit word"
    with a concrete example, so the agent doesn't have to guess the format."""
    from app.agents.prompts import build_chat_prompt

    prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "PLAIN NUMBER" in prompt
    assert "2 weeks" in prompt or "2 minggu" in prompt
    chat_prompt = build_chat_prompt(
        _agent("pm-1", "pm"), "/repo", ROSTER, conversation_title="x", messages=[]
    )
    assert "PLAIN NUMBER" in chat_prompt


def test_existing_artifact_groups_listed_in_contract():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        existing_artifact_groups=["Technical Docs", "Test Results", "Sprint Reports"],
    )
    assert "Groups that ALREADY EXIST in this workspace's Artifacts menu:" in prompt
    assert "- Technical Docs" in prompt
    assert "- Test Results" in prompt
    assert "- Sprint Reports" in prompt
    assert "You MUST use one of the groups above if it's relevant" in prompt
    assert "Do NOT create a new name if a relevant one exists" in prompt


def test_no_artifact_groups_tells_agent_they_may_create_first():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "you may create the first one" in prompt
    assert "Groups that ALREADY EXIST" not in prompt


def test_existing_epics_listed_in_contract_for_pm():
    prompt = build_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        TICKET,
        existing_epics=["AUTH-001 — Authentication", "BILLING-002 — Billing"],
    )
    assert "Epics that ALREADY EXIST (top-level tickets) in this workspace:" in prompt
    assert "AUTH-001 — Authentication" in prompt
    assert "BILLING-002 — Billing" in prompt
    assert "You MUST fill `epic:`" in prompt
    assert "epic: <optional, target epic key from the list above" in prompt


def test_no_existing_epics_tells_agent_first_one_becomes_epic():
    prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "will become the first epic" in prompt
    assert "Epics that ALREADY EXIST" not in prompt


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
    assert "Sprints that ALREADY EXIST:" in prompt
    assert "- Sprint 1" in prompt
    assert "- Sprint 2" in prompt
    assert "A sprint is ONLY a timebox" in prompt
    assert "do NOT put a feature/scope name in the sprint name" in prompt


def test_no_existing_sprints_tells_agent_pure_timebox_naming():
    prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "There are no sprints in this workspace yet" in prompt
    assert "Sprints that ALREADY EXIST" not in prompt


def test_artifact_catalog_block_included_when_given():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        artifact_catalog=[
            "[Technical Docs] PRD.md (MAP-001) — initial PRD",
            "[Test Results] evidence.md (MAP-002)",
        ],
    )
    assert "Artifacts in this workspace (Artifacts menu)" in prompt
    assert "- [Technical Docs] PRD.md (MAP-001) — initial PRD" in prompt
    assert "- [Test Results] evidence.md (MAP-002)" in prompt


def test_artifact_catalog_block_omitted_when_empty():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "Artifacts in this workspace" not in prompt


def test_artifact_updates_contract_present_for_pm_only():
    pm_prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "artifact_updates:" in pm_prompt
    assert "op: rename" in pm_prompt
    assert "op: merge" in pm_prompt
    assert "op: move" in pm_prompt
    assert "op: delete" in pm_prompt
    assert "ONLY roles with artifact-management permission" in pm_prompt
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
        _agent("pm-1", "pm"), "/repo", ROSTER, routine_prompt="check stuck tickets"
    )
    assert "list_tickets" in prompt
    assert "update_ticket" in prompt
    assert "updates:" in prompt


def test_routine_prompt_teaches_at_mention_syntax_in_comments():
    from app.agents.prompts import build_routine_prompt

    prompt = build_routine_prompt(
        _agent("pm-1", "pm"), "/repo", ROSTER, routine_prompt="check stuck tickets"
    )
    assert '"@lead-1"' in prompt
    assert "NEVER call an agent with `@` inside a" in prompt


def test_agent_memory_block_absent_by_default():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "Notes from your previous work" not in prompt


def test_agent_memory_block_present_when_given():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        agent_memories=["don't forget to run migrations", "this repo uses uv"],
    )
    assert "Notes from your previous work" in prompt
    assert "- don't forget to run migrations" in prompt
    assert "- this repo uses uv" in prompt


def test_agent_memory_block_appears_before_ticket_context():
    prompt = build_prompt(
        _agent("eng-1", "engineer"),
        "/repo",
        ROSTER,
        TICKET,
        agent_memories=["old note"],
    )
    assert prompt.index("Notes from your previous work") < prompt.index("Current ticket:")


def test_chat_prompt_teaches_at_mention_syntax_in_comments():
    from app.agents.prompts import build_chat_prompt

    prompt = build_chat_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        conversation_title="Feature discussion",
        messages=[],
    )
    assert '"@lead-1"' in prompt
    assert "NEVER call an agent with `@` inside a" in prompt


def test_chat_prompt_teaches_choices_block_syntax():
    from app.agents.prompts import build_chat_prompt

    prompt = build_chat_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        conversation_title="Feature discussion",
        messages=[],
    )
    # `choices:` is a normal top-level YAML field (like tickets:/sprints:), NOT text
    # embedded inside `summary: |` — that was the original design and it broke in
    # practice: an agent that doesn't keep perfect indentation on every line of a
    # nested block inside a YAML literal scalar produces invalid YAML entirely
    # (confirmed against a real run). Must never re-teach the embedded-text form.
    assert "choices:" in prompt
    assert "type: single" in prompt
    assert "options:" in prompt
    assert "```choices" not in prompt
    assert "~~~choices" not in prompt
    assert "ASK ONLY ONE" in prompt
    assert "QUESTION per reply" in prompt
    assert "don't ask several things at once in a single `summary`" in prompt


def test_chat_prompt_teaches_approval_choices_keywords():
    from app.agents.prompts import build_chat_prompt

    prompt = build_chat_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        conversation_title="Feature discussion",
        messages=[],
    )
    assert "ASKING FOR APPROVAL" in prompt
    # Every word here must stay in sync with APPROVAL_RE (app/api/comments.py) — the
    # instruction is only correct if the PM's "yes" pill actually matches the gate.
    for word in ["Oke", "Lanjut", "Setuju", "Sip", "Gas", "Boleh", "Silakan"]:
        assert word in prompt


def test_chat_prompt_forbids_mentioning_unlisted_options():
    from app.agents.prompts import build_chat_prompt

    prompt = build_chat_prompt(
        _agent("pm-1", "pm"),
        "/repo",
        ROSTER,
        conversation_title="Feature discussion",
        messages=[],
    )
    assert 'never refer to "the options above"' in prompt


def test_chat_prompt_teaches_epic_then_breakdown_separately():
    """A real run had the PM flatten a whole feature into 6 sibling tickets, each
    assigned straight to a specialist, instead of one epic (assigned to PM) with
    sub-tickets broken down in a later run. The chat/routine contracts can't let
    the PM declare epic + children in one batch anyway — the epic has no ticket
    key yet at write time (see _resolve_epic_target, matched by key only) — so the
    prompt must say so explicitly rather than let the agent guess."""
    from app.agents.prompts import build_chat_prompt, build_routine_prompt

    chat_prompt = build_chat_prompt(
        _agent("pm-1", "pm"), "/repo", ROSTER, conversation_title="x", messages=[]
    )
    routine_prompt = build_routine_prompt(
        _agent("pm-1", "pm"), "/repo", ROSTER, routine_prompt="check stuck tickets"
    )
    for prompt in (chat_prompt, routine_prompt):
        assert "declare ONLY the epic ticket itself" in prompt
        assert "assignee" in prompt and "YOURSELF (the PM)" in prompt
        assert "Do NOT also declare its sub-tickets in this same batch" in prompt
        assert "auto-scheduled if it has an assignee" in prompt


def test_pm_role_prompt_has_expert_proactive_persona():
    prompt = build_prompt(_agent("pm-1", "pm"), "/repo", ROSTER, TICKET)
    assert "EXPERIENCED (expert)" in prompt
    assert "concrete suggestions/recommendations" in prompt


def test_map_contract_mentions_memory_field():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "memory:" in prompt


def test_base_block_teaches_at_mention_syntax_in_text():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "@lead-1" in prompt
    assert "without `@` it's just plain text" in prompt


def test_map_contract_clarifies_no_at_in_mention_field():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "mention: [<agent name from the team list:" in prompt
    assert "NAME ONLY, no @" in prompt
    assert "WITHOUT an `@`" in prompt
