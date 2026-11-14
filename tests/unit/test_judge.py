"""The LLM-judge machinery without a network: registry section, JSON parsing, the
client's repair round and output allowance, same-model refusal and the pending-cell scan."""
import json
from pathlib import Path

import pytest

from osworld_science.config import Settings
from osworld_science.judge.client import JudgeClient, JudgeError, JudgeTruncated, parse_json
from osworld_science.judge.runner import _same_model, judge_cell, pending_cells
from osworld_science.llm.models import MissingCredential, ModelRegistry, ResolvedModel
from osworld_science.tasks.model import Task

REPO = Path(__file__).resolve().parents[2]


def registry() -> ModelRegistry:
    return ModelRegistry(Settings.load(REPO).configs_dir / "models.yaml")


def test_judges_are_a_separate_section():
    reg = registry()
    assert reg.default_judge == "deepseek-v4.1-flash" and "deepseek-v4.1-flash" in reg.judge_names()
    assert "deepseek-v4.1-flash" not in reg.names()          # never a solver column
    spec = reg.get_judge(None)
    assert spec.model_id == "deepseek/deepseek-v4.1-flash" and spec.backend.name == "openrouter"
    assert spec.provider_order == ("deepseek",) and spec.extra.get("vision") is True
    assert reg.get_judge("default") is spec
    with pytest.raises(KeyError):
        reg.get_judge("no-such-judge")
    with pytest.raises(MissingCredential):
        reg.resolve_judge(None, {})                          # no OPENROUTER_API_KEY


def test_parse_json_accepts_fences_and_surrounding_prose():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('Here you go:\n```json\n{"a": [1, 2]}\n```\nthanks') == {"a": [1, 2]}
    assert parse_json('noise [1, 2, 3] more') == [1, 2, 3]
    assert parse_json('{"a": 1} trailing words with a } brace') == {"a": 1}      # text after the value
    assert parse_json('{"a": {"b": [1, 2]}}\n{"second": true}') == {"a": {"b": [1, 2]}}  # two values: the largest
    assert parse_json('{"a": {"b": [1, 2]}') == {"a": {"b": [1, 2]}}        # one closer short
    assert parse_json('{"a": {"b": [1, 2]}}}') == {"a": {"b": [1, 2]}}       # one closer too many
    assert parse_json('{"a": "unterminated') == {"a": "unterminated"}        # cut inside a string
    with pytest.raises(ValueError):
        parse_json("not json at all")


class _Stub:
    """Stands in for ModelCaller: scripted replies, token stats. A reply of TRUNCATED is the empty
    string a caller returns when the model spent its whole allowance, with `last` saying so."""
    TRUNCATED = object()

    def __init__(self, replies):
        self.replies = list(replies)
        self.payloads = []
        self.last = {}

    def __call__(self, payload):
        self.payloads.append(payload)
        reply = self.replies.pop(0)
        cut = reply is self.TRUNCATED
        self.last = {"finish": "length" if cut else "stop", "truncated": cut, "max_tokens": payload["max_tokens"],
                     "output_tokens": payload["max_tokens"] if cut else 10,
                     "reasoning_tokens": payload["max_tokens"] if cut else 0}
        return "" if cut else reply

    def token_stats(self):
        return {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "n_llm_calls": len(self.payloads),
                "cost_usd": 0.0, "cached_input_tokens": 0, "upstream_cost_usd": None}


def _client(tmp_path, replies) -> JudgeClient:
    spec = registry().get_judge(None)
    client = JudgeClient(ResolvedModel(spec=spec, base_url="http://unused.invalid", key="x"), tmp_path / "judge")
    client.caller = _Stub(replies)
    return client


def test_ask_json_repairs_once_and_keeps_a_transcript(tmp_path):
    client = _client(tmp_path, ["I think the answer is 42", '{"answer": 42}'])
    got = client.ask_json("stage", "system", [client.text("question")])
    assert got == {"answer": 42}
    assert len(client.caller.payloads) == 2 and "could not be parsed" in client.caller.payloads[1]["messages"][1]["content"][-1]["text"]
    lines = (tmp_path / "judge" / "transcript.jsonl").read_text().splitlines()
    assert [json.loads(line)["stage"] for line in lines] == ["stage", "stage:repair"]
    client = _client(tmp_path, ["nope", "still nope"])
    with pytest.raises(JudgeError):
        client.ask_json("stage", "system", [client.text("q")])


def test_a_judge_request_carries_the_whole_output_allowance(tmp_path):
    """The module's `max_tokens` sizes the visible answer; a reasoning model's hidden reasoning counts
    against the same field, so the request goes out with the model's declared max_out at once."""
    client = _client(tmp_path, ['{"ok": 1}', '{"ok": 2}'])
    cap = client.model.spec.max_out
    assert cap and cap >= 65536
    client.ask_json("small", "system", [client.text("q")], max_tokens=2000)
    client.ask_json("default", "system", [client.text("q")])
    assert [p["max_tokens"] for p in client.caller.payloads] == [cap, cap]
    assert all("reasoning" not in p for p in client.caller.payloads)      # first attempts: the model's own default
    # a judge that declares no cap starts high and leaves the doubling to the caller
    import dataclasses
    spec = dataclasses.replace(registry().get_judge(None), max_out=None)
    bare = JudgeClient(ResolvedModel(spec=spec, base_url="http://unused.invalid", key="x"), tmp_path / "bare")
    assert bare.allowance(4000) == JudgeClient.DEFAULT_ALLOWANCE == 32000 and bare.allowance(50000) == 50000


def test_a_reply_cut_off_at_the_allowance_is_asked_again_with_a_reasoning_budget(tmp_path):
    logged = []
    client = _client(tmp_path, [_Stub.TRUNCATED, '{"answer": 42}'])
    client.log = logged.append
    assert client.ask_json("acoustics:10", "system", [client.text("q")], max_tokens=4000) == {"answer": 42}
    first, second = client.caller.payloads
    assert "reasoning" not in first and second["reasoning"] == {"max_tokens": JudgeClient.RETRY_REASONING_BUDGET}
    assert second["max_tokens"] == first["max_tokens"] == client.model.spec.max_out
    assert "asking again with a reasoning budget" in logged[0] and "65536 of 65536 output tokens used" in logged[0]
    stages = [json.loads(line)["stage"] for line in (tmp_path / "judge" / "transcript.jsonl").read_text().splitlines()]
    assert stages == ["acoustics:10", "acoustics:10:again1"]
    # cut off twice: the stage fails as JudgeTruncated (a JudgeError), and says what to change
    client = _client(tmp_path, [_Stub.TRUNCATED, _Stub.TRUNCATED])
    with pytest.raises(JudgeTruncated, match="max_out"):
        client.ask("s", "system", [client.text("q")])
    assert len(client.caller.payloads) == 2
    # an empty reply that was not a truncation (endpoint down) is not asked again here: the caller already retried
    client = _client(tmp_path, [""])
    with pytest.raises(JudgeError) as err:
        client.ask("s", "system", [client.text("q")])
    assert not isinstance(err.value, JudgeTruncated) and len(client.caller.payloads) == 1


class _Response:
    def __init__(self, body: dict, status: int = 200):
        self._body, self.status_code, self.headers, self.text = body, status, {}, json.dumps(body)

    def json(self):
        return self._body


def test_caller_reports_how_the_last_request_ended(tmp_path, monkeypatch):
    """ModelCaller.last tells a truncation from a transport failure, the doubling stops at the
    model's cap, and a payload's `reasoning` reaches an OpenRouter body (and no other)."""
    from osworld_science.llm import caller as caller_mod
    bodies, script, logged = [], [], []

    def post(url, json=None, **kw):                                         # noqa: A002 - requests' keyword
        bodies.append(json)
        return script.pop(0)

    monkeypatch.setattr(caller_mod.requests, "post", post)
    monkeypatch.setattr(caller_mod.time, "sleep", lambda s: None)

    def cut(n):
        return _Response({"choices": [{"message": {"content": None}, "finish_reason": "length"}],
                          "usage": {"prompt_tokens": 5, "completion_tokens": n,
                                    "completion_tokens_details": {"reasoning_tokens": n}}})

    done = _Response({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 7}})
    judge = ResolvedModel(spec=registry().get_judge(None), base_url="http://unused.invalid", key="x")
    call = caller_mod.ModelCaller(judge, tmp_path / "raw.jsonl", log=logged.append)
    payload = {"messages": [{"role": "user", "content": "q"}], "max_tokens": 65536,
               "reasoning": {"max_tokens": 8000}}
    script[:] = [cut(65536)]
    assert call(payload) == "" and len(bodies) == 1                         # at the cap: nothing to double to
    assert call.last["truncated"] and call.last["max_tokens"] == 65536 and call.last["reasoning_tokens"] == 65536
    assert "truncated at the output cap 65536" in logged[-1]
    assert bodies[0]["reasoning"] == {"max_tokens": 8000} and bodies[0]["max_tokens"] == 65536
    script[:] = [done]
    assert call({"messages": payload["messages"], "max_tokens": 65536}) == "{}"
    assert call.last == {"finish": "stop", "truncated": False, "max_tokens": 65536, "output_tokens": 7,
                         "reasoning_tokens": None} and "reasoning" not in bodies[-1]
    # below the cap the doubling runs out of attempts first, and the log says which of the two it was
    script[:] = [cut(4000), cut(8000), cut(16000), cut(32000)]
    assert call({"messages": payload["messages"], "max_tokens": 4000}) == ""
    assert [b["max_tokens"] for b in bodies[-4:]] == [4000, 8000, 16000, 32000]
    assert "still truncated at max_tokens 32000 after 4 attempts (the model's cap is 65536)" in logged[-1]
    # a request that never got an answer is not a truncation
    script[:] = [_Response({"error": {"message": "upstream down"}}, status=502)] * 4
    assert call({"messages": payload["messages"], "max_tokens": 65536}) == "" and not call.last["truncated"]
    # the Azure judge has no token budget for reasoning: the field is dropped, not forwarded
    azure = ResolvedModel(spec=registry().get_judge("gpt-5.6-luna"), base_url="http://unused.invalid", key="x")
    script[:] = [done]
    caller_mod.ModelCaller(azure, tmp_path / "raw2.jsonl", log=logged.append)(payload)
    assert "reasoning" not in bodies[-1] and "reasoning_effort" not in bodies[-1]


def test_image_parts_are_data_urls_and_logged_by_size(tmp_path):
    from PIL import Image
    png = tmp_path / "shot.png"
    Image.new("RGB", (64, 32), (10, 20, 30)).save(png)
    part = JudgeClient.image_file(png)
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    client = _client(tmp_path, ['{"ok": true}'])
    client.ask_json("s", "sys", [client.text("look"), part])
    rec = json.loads((tmp_path / "judge" / "transcript.jsonl").read_text().splitlines()[0])
    kinds = [p["type"] for p in rec["messages"][1]["content"]]
    assert kinds == ["text", "image"] and rec["messages"][1]["content"][1]["bytes"] > 0


def test_same_model_detection():
    assert _same_model("deepseek/deepseek-v4.1-flash", "deepseek-v4.1-flash")
    assert _same_model("claude-fable-5-1", "anthropic/claude-fable-5-1")
    assert not _same_model("gpt-6-astra", "deepseek/deepseek-v4.1-flash")
    assert not _same_model(None, "x")


def _task(tmp_path, with_judge=True) -> Task:
    raw = {"id": "t1", "snapshot": "s", "instruction": "do", "config": [],
           "evaluator": {"func": ["x"], "params": [{}], "labels": [""]}}
    if with_judge:
        raw["judge"] = {"module": "reference_private/t1/osci_judge.py", "func": "review"}
    p = tmp_path / "t1.json"
    p.write_text(json.dumps(raw))
    return Task.load(p, domain="d")


class _Tasks(dict):
    pass


def test_pending_cells_and_same_model_refusal(tmp_path):
    task = _task(tmp_path)
    run = tmp_path / "runs" / "r1"
    for model, pending in (("m-pending", True), ("m-scored", False), ("m-nofile", None)):
        cell = run / "t1" / model
        (cell / "agent").mkdir(parents=True)
        if pending is not None:
            res = {"score": None, "verdict": None, "pending": [{"index": 1, "reason": "review"}]} if pending \
                else {"score": 1.0, "verdict": "PASS"}
            (cell / "score.json").write_text(json.dumps(res))
    results = {"m-pending": {"t1": {}}, "m-scored": {"t1": {}}, "m-nofile": {"t1": {}}, "other": {"t2": {}}}
    tasks = _Tasks(t1=task)
    assert [(m, c.name) for m, t, c in pending_cells(run, tasks, results)] == [("m-pending", "m-pending")]
    assert sorted(m for m, t, c in pending_cells(run, tasks, results, force=True)) == ["m-pending", "m-scored"]
    assert list(pending_cells(run, _Tasks(t1=_task(tmp_path, with_judge=False)), results)) == []
    # the judge never reviews the model that produced the run
    cell = run / "t1" / "m-pending"
    (cell / "agent" / "meta.json").write_text(json.dumps({"model": "m-pending", "model_id": "deepseek/deepseek-v4.1-flash"}))
    from osworld_science.data.layout import DataLayout
    judge = ResolvedModel(spec=registry().get_judge(None), base_url="http://unused.invalid", key="x")
    verdict = judge_cell(task, cell, judge, DataLayout(tmp_path), solver="m-pending", log=lambda *_: None)
    assert verdict["valid"] is False and "produced this run" in verdict["reason"]
    assert json.loads((cell / "judge" / "judge.json").read_text())["judge_model_id"] == "deepseek/deepseek-v4.1-flash"


def test_select_judge_decides_from_the_tasks(tmp_path):
    from osworld_science.judge.runner import select_judge
    reg = registry()
    env = {"OPENROUTER_API_KEY": "x", "OPENAI_API_KEY": "y", "OPENAI_BASE_URL": "https://unused.invalid"}
    with_judge, without = _task(tmp_path), _task(tmp_path, with_judge=False)
    assert select_judge(reg, env, [without], None, False) == (None, "no selected task declares a judge")
    rm, why = select_judge(reg, env, [with_judge, without], None, False)
    assert rm.name == reg.default_judge and "t1" in why
    assert select_judge(reg, env, [with_judge], None, True)[0] is None
    rm, _ = select_judge(reg, env, [without], "gpt-5.6-luna", False)       # an explicit judge is kept
    assert rm.name == "gpt-5.6-luna"
    with pytest.raises(JudgeError):
        select_judge(reg, {}, [with_judge], None, False)                   # key missing: refuse to start
    with pytest.raises(JudgeError):
        select_judge(reg, env, [with_judge], "no-such-judge", False)
