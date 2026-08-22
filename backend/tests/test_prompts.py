from app.agents.prompts import (
    DEFAULT_ROLE_PROMPTS,
    AgentInfo,
    CommentInfo,
    TicketInfo,
    build_prompt,
)
from app.core.report import ROLE_ALLOWED_STATUSES

ROSTER = [
    AgentInfo(name="pm-1", role="pm"),
    AgentInfo(name="lead-1", role="lead"),
    AgentInfo(name="eng-1", role="engineer"),
    AgentInfo(name="designer-1", role="designer"),
    AgentInfo(name="qa-1", role="qa"),
    AgentInfo(name="pentester-1", role="pentester"),
]

TICKET = TicketInfo(
    key="MAP-001",
    title="Bikin halaman login",
    status="in_progress",
    priority="high",
    description="Tambah form login dengan validasi email.",
)


def _agent(name: str, role: str, system_prompt: str | None = None) -> AgentInfo:
    return AgentInfo(name=name, role=role, system_prompt=system_prompt)


def test_base_always_present():
    prompt = build_prompt(_agent("eng-1", "engineer"), "/repo", ROSTER, TICKET)
    assert "seorang Engineer di tim software" in prompt
    assert "/repo" in prompt
    assert "eng-1" in prompt
    assert "pm-1 (Project Manager)" in prompt


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


def test_map_contract_status_list_matches_role_allowed_statuses():
    for role, allowed in ROLE_ALLOWED_STATUSES.items():
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        expected = ", ".join(sorted(allowed))
        assert f"status: <salah satu dari: {expected}>" in prompt


def test_tickets_instruction_present_for_pm_qa_pentester():
    for role in ("pm", "qa", "pentester"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "tickets:" in prompt


def test_tickets_instruction_absent_for_lead_engineer_designer():
    for role in ("lead", "engineer", "designer"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "tickets:" not in prompt


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
    for role in ("lead", "engineer", "designer"):
        prompt = build_prompt(_agent(f"{role}-x", role), "/repo", ROSTER, TICKET)
        assert "updates:" not in prompt


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
