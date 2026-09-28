"""Reply parsing for the pyautogui action space — a verbatim port of the
vendored `parse_code_from_string`, including its two registered deviations
from upstream OSWorld:

  1. Semicolons are split into lines ONLY for single-line replies. Upstream
     splits unconditionally, which breaks string literals containing ';'
     (26.5 % of the OpenFOAM-era replies became SyntaxErrors that way).
  2. A fence like ```DONE\\n``` where the language-tag slot swallows the
     signal still yields the signal instead of an empty action.
"""
from __future__ import annotations

import re

SPECIAL = ("WAIT", "DONE", "FAIL")
_FENCE = re.compile(r"```(?:\w+\s+)?(.*?)```", re.DOTALL)
_SIGNAL_FENCE = re.compile(r"```\s*(?:\w+\s+)?(WAIT|DONE|FAIL)\s*```")


def parse_code_from_string(input_string: str | None) -> list[str]:
    if input_string is None:
        raise ValueError("model returned no content (None)")
    if "\n" not in input_string.strip():
        input_string = "\n".join([line.strip() for line in input_string.split(";") if line.strip()])
    if input_string.strip() in SPECIAL:
        return [input_string.strip()]

    codes: list[str] = []
    for _m in _FENCE.finditer(input_string):
        match = _m.group(1).strip()
        if not match:
            _sig = _SIGNAL_FENCE.fullmatch(_m.group(0))
            if _sig:
                codes.append(_sig.group(1))
            continue
        if match in SPECIAL:
            codes.append(match.strip())
        elif match.split("\n")[-1] in SPECIAL:
            if len(match.split("\n")) > 1:
                codes.append("\n".join(match.split("\n")[:-1]))
            codes.append(match.split("\n")[-1])
        else:
            codes.append(match)
    return codes
