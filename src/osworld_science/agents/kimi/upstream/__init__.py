"""Moonshot's Kimi computer-use agent, vendored from xlang-ai/OSWorld
(`mm_agents/kimi/`, commit b138d348, Apache-2.0 — see LICENSE next to this file).

`kimi_agent.py` and `utils.py` are byte-identical to the upstream files and
to the copy the recorded sweeps ran (verify with `cmp`; `tests/unit/
test_kimi_vendored.py` pins their sha256). This `__init__` is the one
deliberate deviation, inherited from the sweep copy: upstream's `__init__`
also imports KimiAgentLegacy / KimiAgentNewFormat / KimiAgentToolCallNewFormat
from sibling modules that were never committed to xlang-ai/OSWorld (absent
at b138d348), so importing upstream's package raises ModuleNotFoundError.
This file re-exports the names that exist and additionally exports
`build_system_prompt` (not in upstream's `__all__`), exactly as the sweep
copy did. Nothing about KimiAgent's behaviour changes.

Do not edit the two vendored modules; adapt in `..adapter` instead.
"""
from .kimi_agent import (
    GUI_MODE_SYS_PROMPT,
    HYBRID_MODE_SYS_PROMPT,
    KimiAgent,
    build_system_prompt,
)

__all__ = [
    "KimiAgent",
    "GUI_MODE_SYS_PROMPT",
    "HYBRID_MODE_SYS_PROMPT",
    "build_system_prompt",
]
