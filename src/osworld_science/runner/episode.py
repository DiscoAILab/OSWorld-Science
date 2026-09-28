"""The step loop: screenshot → predict → execute → feedback.

One loop serves every agent; what differs between the PromptAgent and the
Kimi tool-calling agent (retry policy, empty-reply handling, tool feedback)
is expressed through the agent protocol. Timing and the trace/meta files
match the legacy sweeps.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..agents.base import ActionResult, AgentBase, Observation
from ..guest.client import Guest
from ..guest.controller import DesktopController
from ..tasks.model import Task

SPECIAL = ("WAIT", "DONE", "FAIL")
HARNESS_VERSION = "osci-0.1.0"


class TraceLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.write_text("")

    def __call__(self, kind: str, payload) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, "payload": payload},
                               ensure_ascii=False) + "\n")


def _special_of(act) -> str | None:
    if isinstance(act, str) and act in SPECIAL:
        return act
    if isinstance(act, dict) and act.get("action_type") in SPECIAL:
        return act["action_type"]
    return None


def tally_windows(window_list: str, window_classes: dict[str, str], apps_seen: dict[str, int]) -> None:
    low = window_list.lower()
    for key, tag in window_classes.items():
        if key.lower() in low:
            apps_seen[tag] = apps_seen.get(tag, 0) + 1


def run_episode(agent: AgentBase, task: Task, controller: DesktopController, guest: Guest,
                run_dir: Path, max_steps: int, window_classes: dict[str, str] | None = None,
                track_windows: bool = True, stop_event: threading.Event | None = None,
                max_actions_per_step: int = 10, log=print) -> dict:
    """`max_actions_per_step` (0 = unlimited) caps how many parsed actions one
    reply may execute. Healthy replies carry one code block: across 11,565
    recorded steps of ten models, 99.95 % had at most 9 actions and every step
    above that came from one degenerate model emitting hundreds of fenced
    snippets at once. Beyond the cap the rest of the reply is dropped, the
    step is logged and counted (`n_truncated_steps`); nothing is said to the
    model — the next screenshot shows it what actually happened."""
    run_dir = Path(run_dir)
    shots = run_dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    instruction = task.prompt()
    (run_dir / "prompt_sent.txt").write_text(instruction, encoding="utf-8")
    trace = TraceLog(run_dir / "trace.jsonl")
    apps_seen: dict[str, int] = {}

    status, step, n_failed, n_truncated = "max_steps", 0, 0, 0
    t_start = time.time()
    for step in range(1, max_steps + 1):
        if stop_event is not None and stop_event.is_set():
            status = "interrupted"
            break
        png = controller.get_screenshot()
        if not png:
            status = "no_screenshot"
            break
        (shots / f"step_{step:03d}.png").write_bytes(png)
        if track_windows and window_classes:
            try:
                wl = guest.windows()
                trace("windows", wl[:1500])
                tally_windows(wl, window_classes, apps_seen)
            except Exception:  # noqa: BLE001
                pass

        obs = Observation(screenshot=png)
        response = actions = None
        for attempt in range(1, agent.predict_attempts + 1):
            try:
                response, actions = agent.predict(instruction, obs)
                break
            except Exception as e:  # noqa: BLE001
                log(f"[{step:02d}] predict raised (attempt {attempt}): {e}")
                agent.rollback_failed_prediction()
                if attempt == agent.predict_attempts:
                    status = f"predict_error:{e}"
                else:
                    time.sleep(5)
        if actions is None:
            break
        if agent.stop_on_empty_response and not response:
            status = "empty_response"
            break
        trace("response", str(response)[:4000])
        if max_actions_per_step and len(actions) > max_actions_per_step:
            n_truncated += 1
            trace("actions_truncated", {"parsed": len(actions), "executed": max_actions_per_step})
            log(f"[{step:02d}] !! reply parsed into {len(actions)} actions; executing the first "
                f"{max_actions_per_step} (max_actions_per_step) and dropping the rest")
            actions = actions[:max_actions_per_step]
        log(f"[{step:02d}] {len(actions)} action(s): {[str(x)[:60] for x in actions]}")

        stop = False
        for act in actions:
            special = _special_of(act)
            if special:
                trace("special", special)
                if special == "WAIT":
                    time.sleep(2)
                    agent.observe_result(ActionResult(act, "wait", True))
                else:
                    status = special.lower()
                    stop = True
                break
            if isinstance(act, str):  # pyautogui code
                trace("action", act[:2000])
                res = controller.execute_python_command(act)
                rc = res.get("returncode") if isinstance(res, dict) else None
                if rc not in (0, None):
                    n_failed += 1
                err = (res.get("error") or "") if isinstance(res, dict) else ""
                trace("exec_result", json.dumps({"returncode": rc, "stderr": err[:2000]},
                                                ensure_ascii=False))
                agent.observe_result(ActionResult(act, "code", rc in (0, None), rc, "", err))
                time.sleep(1.0)
                continue
            kind = act.get("action_type") if isinstance(act, dict) else None
            if kind == "RUN_COMMAND":
                command = str(act.get("command") or "")
                cwd = str(act.get("cwd") or "") or None
                trace("run_command", json.dumps({"command": command[:2000], "cwd": cwd},
                                                ensure_ascii=False))
                shell = f"cd {cwd} && {command}" if cwd else command
                r = guest.execute(["bash", "-lc", shell], int(act.get("timeout") or 300))
                rc = r.get("returncode")
                if rc not in (0, None):
                    n_failed += 1
                result = ActionResult(act, "run_command", rc in (0, None), rc,
                                      r.get("output") or "", r.get("error") or "")
                trace("exec_result", json.dumps({"success": result.success, "returncode": rc,
                                                 "stdout": result.stdout[:8000],
                                                 "stderr": result.stderr[:4000]},
                                                ensure_ascii=False)[:4000])
                agent.observe_result(result)
                time.sleep(1.0)
                continue
            if kind == "WAIT_FOR_STATE":
                seconds = int(act.get("seconds") or 20)
                trace("wait_for_state", json.dumps(act, ensure_ascii=False)[:500])
                time.sleep(seconds)
                agent.observe_result(ActionResult(act, "wait_for_state", True,
                                                  extra={"waited_seconds": seconds}))
                continue
            trace("unhandled_action", json.dumps(act, ensure_ascii=False)[:1000])
            agent.observe_result(ActionResult(act, "unsupported", False))
        if stop:
            break

    return {"status": status, "steps_used": step, "max_steps": max_steps,
            "n_failed_actions": n_failed, "n_truncated_steps": n_truncated,
            "max_actions_per_step": max_actions_per_step or None,
            "duration_s": round(time.time() - t_start, 1),
            "apps_seen": apps_seen, "harness_version": HARNESS_VERSION}
