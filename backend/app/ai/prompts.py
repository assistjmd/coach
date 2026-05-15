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
        parts.append(f"Goal: {goal}")
    tz = profile.get("timezone")
    if tz:
        parts.append(f"Timezone: {tz}")
    return "\n".join(parts)
