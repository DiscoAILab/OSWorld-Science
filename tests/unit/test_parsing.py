"""Pins the reply-parsing behaviour the recorded sweeps ran with."""
import pytest

from osworld_science.agents.prompt_agent.parsing import parse_code_from_string


def test_bare_special_tokens():
    assert parse_code_from_string("DONE") == ["DONE"]
    assert parse_code_from_string("  WAIT  ") == ["WAIT"]
    assert parse_code_from_string("```FAIL```") == ["FAIL"]


def test_fenced_code_block():
    r = "Reflection.\n```python\npyautogui.click(10, 20)\ntime.sleep(0.5)\n```"
    assert parse_code_from_string(r) == ["pyautogui.click(10, 20)\ntime.sleep(0.5)"]


def test_single_line_semicolons_are_split():
    # upstream behaviour for one-line replies: a(); b() -> two lines inside the fence
    r = "```python\npyautogui.click(1, 2); pyautogui.press('enter')\n```"
    assert "\n" in r.strip()
    # multi-line reply: not split, so a ';' inside a string literal survives
    r2 = "```python\npyautogui.typewrite(\"a { b; c; }\")\ntime.sleep(1)\n```"
    assert parse_code_from_string(r2) == ["pyautogui.typewrite(\"a { b; c; }\")\ntime.sleep(1)"]
    # a genuinely single-line reply IS split on ';'
    assert parse_code_from_string("```a(); b()```") == ["a()\nb()"]


def test_signal_swallowed_by_language_tag_slot():
    # ```DONE\n``` — the (?:\w+\s+)? slot eats "DONE\n" and the capture is empty
    assert parse_code_from_string("All done.\n```DONE\n```") == ["DONE"]
    assert parse_code_from_string("```python\n```") == []  # an empty block is skipped, not ""


def test_code_followed_by_signal_in_same_fence():
    r = "```python\npyautogui.click(1, 1)\nDONE\n```"
    assert parse_code_from_string(r) == ["pyautogui.click(1, 1)", "DONE"]


def test_none_raises():
    with pytest.raises(ValueError):
        parse_code_from_string(None)


def test_unfenced_reply_yields_nothing():
    assert parse_code_from_string("I will now click the button.\nThen wait.") == []
