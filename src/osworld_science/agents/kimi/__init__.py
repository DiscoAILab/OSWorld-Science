"""Kimi tool-calling agent: `adapter` (registered as "kimi") around the
vendored upstream implementation in `upstream/`."""
from .adapter import KIMI_BUDGET_MATCHED, KimiAgentAdapter, build_kimi_agent

__all__ = ["KIMI_BUDGET_MATCHED", "KimiAgentAdapter", "build_kimi_agent"]
