"""The PromptAgent must build exactly the message structure the sweeps sent."""
import base64
import io

from PIL import Image

from osworld_science.agents.base import Observation
from osworld_science.agents.prompt_agent.agent import STEP_PROMPT, PromptAgent


def png(w=64, h=32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


class FakeCaller:
    def __init__(self, replies):
        self.replies, self.payloads = list(replies), []

    def __call__(self, payload):
        self.payloads.append(payload)
        return self.replies.pop(0)

    def token_stats(self):
        return {"n_llm_calls": len(self.payloads)}


def test_system_prompt_resolution_and_task_line():
    caller = FakeCaller(["```python\npyautogui.click(1,1)\n```"])
    agent = PromptAgent(caller, "m", max_tokens=123)
    agent.predict("Do X", Observation(png(640, 480)))
    p = caller.payloads[0]
    sys_text = p["messages"][0]["content"][0]["text"]
    assert "original resolution is 640x480" in sys_text and sys_text.endswith("You are asked to complete the following task: Do X")
    assert "My computer's password is 'password'" in sys_text
    assert p["max_tokens"] == 123 and p["top_p"] == 0.9 and p["temperature"] == 0.5
    assert p["messages"][-1]["content"][0]["text"] == STEP_PROMPT
    assert p["messages"][-1]["content"][1]["image_url"]["detail"] == "high"


def test_sliding_window_of_five_and_reflection_turns():
    replies = [f"```python\npyautogui.click({i},{i})\n```" for i in range(7)] + [""]
    caller = FakeCaller(replies)
    agent = PromptAgent(caller, "m", max_trajectory_length=5)
    for _ in range(8):
        agent.predict("t", Observation(png()))
    last = caller.payloads[-1]["messages"]
    # system + 5 (user, assistant) pairs + current user turn
    assert len(last) == 1 + 5 * 2 + 1
    images = [c for m in last for c in m["content"] if c["type"] == "image_url"]
    assert len(images) == 6
    # 8th call: history holds calls 3..7; the assistant turns carry the raw replies
    assert last[2]["content"][0]["text"] == replies[2]
    # an empty previous reply is rendered as "No valid action" and parses to WAIT
    caller2 = FakeCaller(["", "DONE"])
    a2 = PromptAgent(caller2, "m")
    _, acts = a2.predict("t", Observation(png()))
    assert acts == []  # empty reply -> no fenced code -> no actions (runner stops: empty_response)
    a2.predict("t", Observation(png()))
    assert caller2.payloads[1]["messages"][2]["content"][0]["text"] == "No valid action"


def test_rollback_after_failed_prediction():
    class Boom(FakeCaller):
        def __call__(self, payload):
            raise RuntimeError("x")
    agent = PromptAgent(Boom([]), "m")
    # the caller's exception is swallowed by predict (response ""), matching the vendored agent
    resp, acts = agent.predict("t", Observation(png()))
    assert resp == "" and acts == [] and len(agent.observations) == len(agent.actions) == 1
    # a failure *before* parse leaves observations > actions; rollback restores the invariant
    agent.observations.append({"screenshot": base64.b64encode(png()).decode()})
    agent.rollback_failed_prediction()
    assert len(agent.observations) == len(agent.actions)
