"""Tests for the ```map block parser (MAP-018). See docs/02-tsd.md §4.3/§10.1
and docs/03-agent-design.md §3/§6 for the rules under test.
"""

from app.core.report import parse_report

VALID_AGENTS = {"lead-1", "eng-1", "eng-2", "pm-1", "qa-1", "pentester-1"}


def _wrap(body: str) -> str:
    return f"some assistant text before\n```map\n{body}```\nmore text after"


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


def test_illegal_status_for_engineer():
    text = _wrap("status: done\nsummary: |\n  trying to close it myself\n")
    result = parse_report(text, "engineer", VALID_AGENTS)
    assert result.ok is False
    assert "engineer" in result.reason
    assert "done" in result.reason


def test_illegal_status_for_pm():
    text = _wrap("status: review\nsummary: |\n  pm cannot set review\n")
    result = parse_report(text, "pm", VALID_AGENTS)
    assert result.ok is False
    assert "pm" in result.reason
    assert "review" in result.reason


def test_illegal_status_for_lead():
    text = _wrap("status: done\nsummary: |\n  lead cannot close directly\n")
    result = parse_report(text, "lead", VALID_AGENTS)
    assert result.ok is False
    assert "lead" in result.reason
    assert "done" in result.reason


def test_legal_status_variety():
    # PM
    r = parse_report(_wrap("status: in_progress\nsummary: |\n  breakdown done\n"), "pm", VALID_AGENTS)
    assert r.ok and r.status == "in_progress"
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
