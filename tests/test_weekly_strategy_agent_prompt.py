from app.services.weekly_strategy_agent import build_weekly_strategy_prompt


def test_prompt_guardrails_bounded_and_no_secret():
    prompt = build_weekly_strategy_prompt(
        stats_snapshot={"x": "y"},
        context=[{"ref": "knowledge_note:1", "body": "ctx"}],
        max_chars=1000,
    )
    assert (
        "Agent proposes, human approves" in prompt
        or "agent proposes, human approves" in prompt
    )
    assert "no automatic mutations" in prompt
    assert "Stats snapshot is the source of truth" in prompt
    assert len(prompt) <= 1000
    assert "api_key" not in prompt.lower()
