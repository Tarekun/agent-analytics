"""Harness adapters.

Each harness module exposes:
  - find_latest_session() -> str | None
  - collect(session_id: str) -> record.SessionRecord   (raises SessionNotFound)
"""

from types import ModuleType

from agent_analytics.errors import SessionNotFound  # noqa: F401  (re-export)
from agent_analytics.harnesses import claude_code, pi

HARNESSES: dict[str, ModuleType] = {
    "claude-code": claude_code,
    "pi": pi,
}

DEFAULT_HARNESS = "claude-code"


def get_harness(name: str) -> ModuleType:
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    if key not in HARNESSES:
        raise ValueError(f"unknown harness {name!r}; available: {', '.join(HARNESSES)}")
    return HARNESSES[key]
