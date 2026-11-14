"""A judge model that answers in JSON and leaves a readable transcript.

Built on the agents' ModelCaller, so the judge speaks every backend the
solvers do and its usage is accounted the same way. Three things are added: the
reply must parse as JSON (one repair round with the parse error quoted back),
every exchange is written to `<dir>/transcript.jsonl` with images replaced
by their size, so a reviewer of the reviewer can read what the model was shown,
and a request carries the judge's whole output allowance.

The allowance. A reasoning model counts its hidden reasoning against
`max_tokens`, and how much it reasons is not something the asker can size: the
same request was still reasoning at 16 000 tokens on one attempt and ended after
9 000 on the next. A judge request therefore goes out with the model's
declared `max_out` (configs/models.yaml) from the first attempt, whatever
budget the task's judge module names for its visible answer. The allowance is
a ceiling, not a target; only generated tokens are billed.

A reply that still runs into it is not short of tokens: the model is going
round a "decided ... but wait, let me reconsider" loop (some three hundred
distinct sentences, the commonest fifty times over, in the transcripts that
showed it) and would use any allowance. The request is therefore asked once more, on OpenRouter with
a reasoning budget, which ended the loop in every case tried; only if that
attempt is cut off as well does the stage fail, as JudgeTruncated.
"""
from __future__ import annotations

import base64
import io
import json
import pathlib
import re
from typing import Any

from ..llm.caller import ModelCaller
from ..llm.models import ResolvedModel


class JudgeError(RuntimeError):
    """The judge could not produce a usable answer (empty reply, no JSON after repair)."""


class JudgeTruncated(JudgeError):
    """The judge spent its whole output allowance, reasoning included, without finishing an
    answer, also when asked again. A judge module can catch it and ask for less at a time."""


class JudgeClient:
    # Starting allowance of a judge that declares no `max_out`; the caller doubles it on a
    # truncated reply (three times at most), so such a judge can still reach 256 000.
    DEFAULT_ALLOWANCE = 32000
    # How often a reply cut off at the full allowance is asked for again.
    TRUNCATION_RETRIES = 1
    # Reasoning budget of that second request (OpenRouter `reasoning.max_tokens`). Hosts treat it as
    # a target rather than a hard limit (3 000 to 12 000 gave 1 300 to 12 800 reasoning tokens), but
    # every request that looped without a budget ended with one (8 of 8, 2026-11-11).
    RETRY_REASONING_BUDGET = 8000

    def __init__(self, model: ResolvedModel, log_dir: pathlib.Path, log=print, request_timeout: int = 1800):
        # request_timeout: a full allowance of 65 536 tokens takes up to a quarter of an hour on a slow host
        self.model = model
        self.dir = pathlib.Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.caller = ModelCaller(model, self.dir / "llm_raw.jsonl", request_timeout=request_timeout, log=log)
        self.log = log
        self.vision = bool(model.spec.extra.get("vision", False))

    def allowance(self, answer_tokens: int = 0) -> int:
        """`max_tokens` of a judge request: the model's declared output cap, or, for a judge that
        declares none, DEFAULT_ALLOWANCE (more if the module expects a longer answer than that)."""
        cap = self.model.spec.max_out
        return int(cap) if cap else max(int(answer_tokens or 0), self.DEFAULT_ALLOWANCE)

    # ── message parts ───────────────────────────────────────────────────────
    @staticmethod
    def text(s: str) -> dict:
        return {"type": "text", "text": s}

    @staticmethod
    def image(data: bytes, mime: str = "image/jpeg", detail: str = "high") -> dict:
        b64 = base64.b64encode(data).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": detail}}

    @staticmethod
    def image_file(path: pathlib.Path, max_width: int = 1280, quality: int = 70, detail: str = "high") -> dict:
        """A PNG/JPEG file as a JPEG part, downscaled to `max_width`."""
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if im.width > max_width:
            im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return JudgeClient.image(buf.getvalue(), "image/jpeg", detail)

    # ── asking ──────────────────────────────────────────────────────────────
    def ask(self, stage: str, system: str, parts: list[dict], max_tokens: int = 8000) -> str:
        """One request, one reply. `max_tokens` is the size of the visible answer the module
        expects; it does not bound the request, which carries the judge's allowance (module
        docstring). Raises JudgeTruncated when the allowance itself was not enough, JudgeError
        when the model could not be reached or answered nothing."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": parts}]
        payload = {"messages": messages, "max_tokens": self.allowance(max_tokens)}
        if self.model.spec.extra.get("json_mode") and self.model.backend.kind == "openai_chat":
            payload["response_format"] = {"type": "json_object"}
        bounded = self.model.backend.reasoning_style == "openrouter"
        last: dict = {}
        for attempt in range(1 + self.TRUNCATION_RETRIES):
            reply = self.caller(payload)
            self._transcribe(stage if attempt == 0 else f"{stage}:again{attempt}", messages, reply)
            if reply:
                return reply
            last = getattr(self.caller, "last", None) or {}
            if not last.get("truncated"):
                break
            if attempt < self.TRUNCATION_RETRIES:
                self.log(f"     .. {stage}: {self._spent(last)} and no answer; asking again"
                         + (f" with a reasoning budget of {self.RETRY_REASONING_BUDGET}" if bounded else ""))
                if bounded:
                    payload = {**payload, "reasoning": {"max_tokens": self.RETRY_REASONING_BUDGET}}
        if last.get("truncated"):
            raise JudgeTruncated(f"{stage}: the judge model used its whole output allowance without finishing an "
                                 f"answer, {1 + self.TRUNCATION_RETRIES} times ({self._spent(last)}); raise the "
                                 f"judge's max_out in configs/models.yaml or ask for less in one request "
                                 f"(see {self.dir / 'llm_raw.jsonl'})")
        raise JudgeError(f"{stage}: the judge model returned nothing (see {self.dir / 'llm_raw.jsonl'})")

    @staticmethod
    def _spent(last: dict) -> str:
        reasoning = last.get("reasoning_tokens")
        return (f"{last.get('output_tokens') or last.get('max_tokens')} of {last.get('max_tokens')} output tokens used"
                + (f", {reasoning} of them on reasoning" if reasoning else ""))

    def ask_json(self, stage: str, system: str, parts: list[dict], max_tokens: int = 8000) -> Any:
        """Ask, parse the reply as JSON, and on a parse failure ask once more with the error."""
        reply = self.ask(stage, system, parts, max_tokens)
        try:
            return parse_json(reply)
        except ValueError as err:
            self.log(f"     .. {stage}: reply was not JSON ({err}); asking for a repair")
            repair = parts + [self.text(
                "Your previous reply could not be parsed as JSON: " + str(err)[:300]
                + "\nPrevious reply:\n" + reply[:6000]
                + "\nReply again with the complete answer as one JSON value and nothing else.")]
            reply2 = self.ask(stage + ":repair", system, repair, max_tokens)
            try:
                return parse_json(reply2)
            except ValueError as err2:
                raise JudgeError(f"{stage}: no JSON after a repair round ({err2})") from None

    def stats(self) -> dict:
        return {"judge": self.model.name, "judge_model_id": self.model.model_id, **self.caller.token_stats()}

    def _transcribe(self, stage: str, messages: list[dict], reply: str) -> None:
        def plain(m: dict) -> dict:
            c = m["content"]
            if isinstance(c, str):
                return {"role": m["role"], "content": c}
            out = []
            for p in c:
                if p.get("type") == "image_url":
                    url = p["image_url"]["url"]
                    out.append({"type": "image", "bytes": len(url.split("base64,", 1)[-1]) * 3 // 4})
                else:
                    out.append(p)
            return {"role": m["role"], "content": out}
        with (self.dir / "transcript.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"stage": stage, "messages": [plain(m) for m in messages], "reply": reply},
                               ensure_ascii=False) + "\n")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(reply: str) -> Any:
    """The JSON value in a model reply: the whole reply, a fenced block, or the first
    complete object/array found by scanning (text before or after it is ignored, and
    when several values are present the largest complete one wins). Raises ValueError
    with the decoder's message."""
    text = reply.strip()
    if not text:
        raise ValueError("empty reply")
    candidates = [text] + [m.group(1).strip() for m in _FENCE.finditer(text)]
    last = None
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError as e:
            last = e
    # a reply cut short, or one closing brace too few/many: balance the brackets outside strings
    for c in candidates:
        fixed = _balance(c)
        if fixed != c:
            try:
                return json.loads(fixed)
            except ValueError as e:
                last = e
    decoder = json.JSONDecoder()
    best = None
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            value, end = decoder.raw_decode(text, i)
        except ValueError as e:
            last = e
            continue
        if isinstance(value, (dict, list)) and (best is None or end - i > best[0]):
            best = (end - i, value)
    if best is not None:
        return best[1]
    raise ValueError(str(last) if last else "no JSON value found")


def _balance(text: str) -> str:
    """Append the closers a truncated JSON text lacks, or drop surplus closers at its end."""
    stack, in_str, esc = [], False, False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
            else:
                return text.rstrip()[:-1] if text.rstrip().endswith(ch) else text  # one surplus closer at the end
    if in_str:
        text += '"'
    return text.rstrip().rstrip(",") + "".join(reversed(stack))
