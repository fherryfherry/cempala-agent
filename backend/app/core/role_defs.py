"""Seed definitions of the 8 builtin roles (docs/superpowers/specs/
2026-08-27-dynamic-roles-design.md).

The migration backfills the `role` table from these; prompts.py uses the same
definitions so code and DB can't drift. `system_prompt` for each role lives in
`app.agents.prompts.DEFAULT_ROLE_PROMPTS` (verbatim from
docs/03-agent-design.md §4) — imported by the migration, not duplicated here.
"""

BUILTIN_ROLES: list[dict] = [
    {
        "key": "pm",
        "name": "Project Manager",
        "may_declare_tickets": True,
        "may_manage_artifacts": True,
        "is_reviewer": False,
    },
    {
        "key": "lead",
        "name": "Lead Engineer",
        "may_declare_tickets": False,
        "may_manage_artifacts": False,
        "is_reviewer": True,
    },
    {
        "key": "engineer",
        "name": "Engineer",
        "may_declare_tickets": False,
        "may_manage_artifacts": False,
        "is_reviewer": False,
    },
    {
        "key": "designer",
        "name": "Designer",
        "may_declare_tickets": False,
        "may_manage_artifacts": False,
        "is_reviewer": False,
    },
    {
        "key": "qa",
        "name": "QA",
        "may_declare_tickets": True,
        "may_manage_artifacts": False,
        "is_reviewer": True,
    },
    {
        "key": "pentester",
        "name": "Security Reviewer",
        "may_declare_tickets": True,
        "may_manage_artifacts": False,
        "is_reviewer": True,
    },
    {
        "key": "business_analyst",
        "name": "Business Analyst",
        "may_declare_tickets": True,
        "may_manage_artifacts": False,
        "is_reviewer": False,
    },
    {
        "key": "system_architect",
        "name": "System Architect",
        "may_declare_tickets": False,
        "may_manage_artifacts": False,
        "is_reviewer": True,
    },
]
