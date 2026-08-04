"""Shared prompt fragments. The system prompt and user-context block are designed
to be reused across the nightly daily-brief job AND the chat endpoint, so we
get Anthropic prompt-caching hits on both."""

SYSTEM_PROMPT = """You are Coach, an evidence-based endurance and strength training coach
who helps athletes train smart and recover well. You see the user's data from
multiple trackers (Oura, Apple Health, etc.). Be specific and actionable. Cite
metrics by name when relevant.

Hard rules:
- HRV values from Oura are rMSSD; from Apple Health they are SDNN. Never compare
  the two units directly — they are not interchangeable.
- If data is missing for a metric, say "no data" rather than guessing.
- Keep daily briefs to 3-4 short paragraphs. Keep chat replies tight.
- Never give medical advice; if a user describes symptoms (chest pain, syncope,
  injury), point them to a clinician.
"""


def user_context_block(profile: dict) -> str:
    """Profile block — kept stable across calls so it benefits from caching."""
    parts = [f"User: {profile.get('display_name') or '(anonymous)'}"]
    goal = profile.get("goal")
    if goal:
        parts.append(_format_goal(goal))
    tz = profile.get("timezone")
    if tz:
        parts.append(f"Timezone: {tz}")
    return "\n".join(parts)


def _format_goal(goal: dict) -> str:
    """Render the goal as prose.

    `goal` is the dict built by `ai/data.py::_goal`. Interpolating it directly
    put a Python dict repr — braces, quotes, `None`s — into the prompt, which is
    both noise and a weaker signal than plain language.
    """
    kind = goal.get("kind") or "adaptive"
    sport = goal.get("sport")
    headline = {
        "race": f"training for a {sport} race" if sport else "training for a race",
        "improvement": f"working on {sport} performance" if sport else "working on performance",
        "adaptive": f"training adaptively in {sport}" if sport else "training adaptively",
    }.get(kind, kind)

    lines = [f"Goal: {headline}"]
    if goal.get("target_date"):
        lines.append(f"Target date: {goal['target_date']}")
    if goal.get("description"):
        lines.append(f"Athlete's own words: {goal['description']}")
    params = goal.get("params")
    if params:
        detail = ", ".join(f"{k}={v}" for k, v in params.items())
        lines.append(f"Targets: {detail}")
    return "\n".join(lines)
