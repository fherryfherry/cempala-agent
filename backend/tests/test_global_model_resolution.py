"""Unit tests for resolve_agent_model — pure, no DB, no app startup."""

from app.core.orchestrator import resolve_agent_model


def test_agent_model_wins():
    assert resolve_agent_model("opencode/a", "ollama/b") == "opencode/a"


def test_global_used_when_agent_model_none():
    assert resolve_agent_model(None, "ollama/b") == "ollama/b"


def test_none_when_both_unset():
    assert resolve_agent_model(None, None) is None


def test_empty_string_agent_model_falls_back():
    assert resolve_agent_model("", "ollama/b") == "ollama/b"
