"""The step loop with a fake guest, fake controller and scripted agents:
status transitions, action dispatch, feedback and the trace/meta files."""
import json
from pathlib import Path

import pytest

from osworld_science.agents.base import ActionResult, AgentBase, Observation
from osworld_science.runner.episode import run_episode
from osworld_science.tasks.model import Task


class FakeGuest:
    port = 5999

    def __init__(self):
        self.commands = []

    def windows(self, timeout=20):
        return "0x1 0 gnome-terminal-server.Gnome-terminal host user@host: ~"

    def execute(self, command, timeout=300):
        self.commands.append(command)
        return {"output": "hello", "error": "", "returncode": 0}


class FakeController:
    def __init__(self, shots=99, fail_rc_on=()):
        self.shots, self.executed, self.fail_rc_on = shots, [], fail_rc_on

    def get_screenshot(self):
        if self.shots <= 0:
            return None
        self.shots -= 1
        return b"\x89PNG fake"

    def execute_python_command(self, command):
        self.executed.append(command)
        if command in self.fail_rc_on:
            return {"returncode": 1, "error": "boom"}
        return {"returncode": 0, "error": ""}


class Scripted(AgentBase):
    name = "scripted"

    def __init__(self, script, attempts=2, stop_on_empty=True):
        self.script = list(script)
        self.predict_attempts, self.stop_on_empty_response = attempts, stop_on_empty
        self.results, self.rollbacks = [], 0

    def predict(self, instruction, obs: Observation):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def rollback_failed_prediction(self):
        self.rollbacks += 1

    def observe_result(self, result: ActionResult):
        self.results.append(result)


def task(tmp_path: Path) -> Task:
    raw = {"id": "t", "snapshot": "s", "instruction": "Do it.", "config": [],
           "deliverables": ["/home/user/out.txt"], "evaluator": {"func": []}, "budget": {"max_steps": 4}}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(raw))
    return Task.load(p, domain="d")


def run(tmp_path, agent, controller=None, guest=None, **kw):
    return run_episode(agent, task(tmp_path), controller or FakeController(), guest or FakeGuest(),
                       tmp_path / "agent", 4, window_classes={"gnome-terminal": "terminal"}, log=lambda *_: None, **kw)


def test_done_after_code(tmp_path, monkeypatch):
    monkeypatch.setattr("osworld_science.runner.episode.time.sleep", lambda *_: None)
    ctrl = FakeController(fail_rc_on={"bad()"})
    agent = Scripted([("r1", ["pyautogui.click(1,1)", "bad()"]), ("r2", ["DONE"])])
    meta = run(tmp_path, agent, ctrl)
    assert meta["status"] == "done" and meta["steps_used"] == 2 and meta["n_failed_actions"] == 1
    assert ctrl.executed == ["pyautogui.click(1,1)", "bad()"]
    assert [r.kind for r in agent.results] == ["code", "code"] and agent.results[1].success is False
    assert meta["apps_seen"] == {"terminal": 2}
    shots = sorted(p.name for p in (tmp_path / "agent" / "shots").iterdir())
    assert shots == ["step_001.png", "step_002.png"]
    kinds = [json.loads(line)["kind"] for line in (tmp_path / "agent" / "trace.jsonl").read_text().splitlines()]
    assert kinds == ["windows", "response", "action", "exec_result", "action", "exec_result",
                     "windows", "response", "special"]
    assert (tmp_path / "agent" / "prompt_sent.txt").read_text().endswith("Deliverables:\n  - /home/user/out.txt")


def test_wait_stops_the_rest_of_the_step(tmp_path, monkeypatch):
    monkeypatch.setattr("osworld_science.runner.episode.time.sleep", lambda *_: None)
    ctrl = FakeController()
    agent = Scripted([("r", ["WAIT", "never()"]), ("r", ["FAIL"])])
    meta = run(tmp_path, agent, ctrl)
    assert meta["status"] == "fail" and ctrl.executed == [] and agent.results[0].kind == "wait"


def test_max_steps_and_no_screenshot(tmp_path, monkeypatch):
    monkeypatch.setattr("osworld_science.runner.episode.time.sleep", lambda *_: None)
    meta = run(tmp_path, Scripted([("r", ["a()"])] * 4))
    assert meta["status"] == "max_steps" and meta["steps_used"] == 4
    meta = run(tmp_path, Scripted([("r", ["a()"])] * 4), FakeController(shots=1))
    assert meta["status"] == "no_screenshot" and meta["steps_used"] == 2


def test_predict_retry_then_error_and_empty_response(tmp_path, monkeypatch):
    monkeypatch.setattr("osworld_science.runner.episode.time.sleep", lambda *_: None)
    agent = Scripted([RuntimeError("a"), RuntimeError("b")])
    meta = run(tmp_path, agent)
    assert meta["status"] == "predict_error:b" and agent.rollbacks == 2
    agent = Scripted([RuntimeError("a"), ("ok", ["DONE"])])
    assert run(tmp_path, agent)["status"] == "done"
    assert run(tmp_path, Scripted([("", [])]))["status"] == "empty_response"
    # a Kimi-style agent does not stop on an empty reply
    meta = run(tmp_path, Scripted([("", []), ("x", ["DONE"])], attempts=1, stop_on_empty=False))
    assert meta["status"] == "done" and meta["steps_used"] == 2


def test_run_command_and_wait_for_state_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr("osworld_science.runner.episode.time.sleep", lambda *_: None)
    guest = FakeGuest()
    agent = Scripted([("r", [{"action_type": "RUN_COMMAND", "command": "ls", "cwd": "/tmp"},
                             {"action_type": "WAIT_FOR_STATE", "seconds": 3},
                             {"action_type": "ASK_USER"}]), ("r", ["DONE"])], attempts=1, stop_on_empty=False)
    run(tmp_path, agent, guest=guest)
    assert guest.commands[0] == ["bash", "-lc", "cd /tmp && ls"]
    assert [r.kind for r in agent.results] == ["run_command", "wait_for_state", "unsupported"]
    assert agent.results[0].stdout == "hello" and agent.results[1].extra == {"waited_seconds": 3}


@pytest.mark.parametrize("act", ["DONE", {"action_type": "DONE"}])
def test_special_dict_and_string(tmp_path, monkeypatch, act):
    monkeypatch.setattr("osworld_science.runner.episode.time.sleep", lambda *_: None)
    assert run(tmp_path, Scripted([("r", [act])]))["status"] == "done"


def test_actions_per_step_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("osworld_science.runner.episode.time.sleep", lambda *_: None)
    ctrl = FakeController()
    many = [f"a{i}()" for i in range(15)]
    agent = Scripted([("r", list(many)), ("r", ["DONE"])])
    meta = run(tmp_path, agent, ctrl, max_actions_per_step=10)
    assert ctrl.executed == many[:10] and meta["n_truncated_steps"] == 1 and meta["max_actions_per_step"] == 10
    kinds = [json.loads(line)["kind"] for line in (tmp_path / "agent" / "trace.jsonl").read_text().splitlines()]
    assert "actions_truncated" in kinds
    # 0 = unlimited (the legacy behaviour)
    ctrl2 = FakeController()
    meta = run(tmp_path, Scripted([("r", list(many)), ("r", ["DONE"])]), ctrl2, max_actions_per_step=0)
    assert ctrl2.executed == many and meta["n_truncated_steps"] == 0 and meta["max_actions_per_step"] is None
