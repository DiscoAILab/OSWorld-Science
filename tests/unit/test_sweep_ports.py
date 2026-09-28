"""Port allocation skips busy or locked ports with a warning; a worker whose VM
slot fails before staging hands the cell back and retires after repeated failures."""
import queue
import threading

from osworld_science.runner import sweep
from osworld_science.runner.summary import Summary
from osworld_science.vm import docker as d


def test_allocate_ports_skips_busy_and_locked(tmp_path, monkeypatch):
    busy = {5040, 5043}
    monkeypatch.setattr(sweep, "port_is_free", lambda p: p not in busy)
    monkeypatch.setattr(sweep, "port_lock_held", lambda lock_dir, p: p == 5041)
    warnings = []
    ports = sweep.allocate_ports(5040, 3, tmp_path, warnings.append)
    assert ports == [5042, 5044, 5045]
    # a port bound only by one of our own leftover containers is reused
    ports = sweep.allocate_ports(5040, 3, tmp_path, warnings.append, owned={5043: "osci_ling_5043"})
    assert ports == [5042, 5043, 5044] and any("reusing our container osci_ling_5043" in w for w in warnings)
    assert any("5040" in w and "in use" in w for w in warnings)
    assert any("5041" in w and "locked" in w for w in warnings)


def test_allocate_ports_gives_up(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "port_is_free", lambda p: False)
    monkeypatch.setattr(sweep, "port_lock_held", lambda lock_dir, p: False)
    try:
        sweep.allocate_ports(6000, 1, tmp_path, lambda *_: None, span=5)
    except SystemExit as e:
        assert "free control ports" in str(e)
    else:
        raise AssertionError("must fail when no port is free")


def test_port_is_free_detects_a_bound_port():
    import socket
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert d.port_is_free(port) is False
    assert d.port_is_free(port) is True


class FakeTask:
    def __init__(self, tid):
        self.id = tid


def test_worker_loop_requeues_reset_failures_and_retires(tmp_path):
    q: queue.Queue = queue.Queue()
    for tid in ("t1", "t2", "t3"):
        q.put(("m", FakeTask(tid), 0))
    summary = Summary(tmp_path / "summary.json")
    calls = []

    def bad_slot(port, m, task, wlog):          # this VM slot can never boot
        calls.append((port, task.id))
        return {"finished": False, "error": "VMError: port is already allocated", "infra_stage": "reset"}
    sweep.worker_loop(5040, q, threading.Event(), bad_slot, summary, log=lambda *_: None, prefix="[:5040]")
    assert [c[1] for c in calls] == ["t1", "t2"]          # retired after two consecutive failures
    assert q.qsize() == 3                                 # t1 and t2 went back, t3 untouched
    assert summary.data["results"] == {}                  # nothing recorded as a result

    def good_slot(port, m, task, wlog):
        return {"finished": True, "status": "done", "verdict": "PASS", "score": 1.0}
    sweep.worker_loop(5041, q, threading.Event(), good_slot, summary, log=lambda *_: None)
    assert q.empty() and set(summary.data["results"]["m"]) == {"t1", "t2", "t3"}


def test_worker_loop_records_after_max_requeues(tmp_path):
    q: queue.Queue = queue.Queue()
    q.put(("m", FakeTask("t1"), 2))                       # already handed back twice
    summary = Summary(tmp_path / "summary.json")
    sweep.worker_loop(5040, q, threading.Event(), lambda *a: {"finished": False, "error": "x", "infra_stage": "reset"},
                      summary, log=lambda *_: None)
    assert q.empty() and summary.data["results"]["m"]["t1"]["error"] == "x"


def test_worker_loop_stops_on_event(tmp_path):
    q: queue.Queue = queue.Queue()
    q.put(("m", FakeTask("t1"), 0))
    ev = threading.Event()
    ev.set()
    sweep.worker_loop(5040, q, ev, lambda *a: (_ for _ in ()).throw(AssertionError("must not run")),
                      Summary(tmp_path / "s.json"), log=lambda *_: None)
    assert q.qsize() == 1
