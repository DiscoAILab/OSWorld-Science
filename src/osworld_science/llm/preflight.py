"""Endpoint probe: one text request and one image request per model, through the
API the model's agent will use (chat/completions, or /responses for `openai-cua`)."""
from __future__ import annotations

import base64
import io
import os
import pathlib

from .caller import ModelCaller
from .models import ResolvedModel


def _red_square_b64() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "PNG")  # 1×1 is rejected by Azure/Anthropic
    return base64.b64encode(buf.getvalue()).decode()


def preflight(model: ResolvedModel, reasoning_effort: str = "", log=print,
              api: str = "chat_completions") -> tuple[bool, str]:
    if model.backend.kind != "anthropic_messages" and api == "responses":
        return _preflight_responses(model, reasoning_effort, log)
    png_b64 = _red_square_b64()
    caller = ModelCaller(model, pathlib.Path(os.devnull), reasoning_effort=reasoning_effort, log=log, api=api)
    txt = caller({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "Reply with exactly: OK"}]}], "max_tokens": 512})
    if not txt:
        return False, "text request failed (key, endpoint or model id is wrong; see the log above)"
    img = caller({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "What colour is this square? One word."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}}]}],
        "max_tokens": 512})
    if not img:
        return False, "text ok, IMAGE request failed — the agent only sends screenshots, this model cannot run"
    return True, f"text+image ok (image answer: {img.strip()[:24]!r})"


def _preflight_responses(model: ResolvedModel, reasoning_effort: str, log=print) -> tuple[bool, str]:
    """The same two requests against <base_url>/responses with the official client, the
    way the Agents SDK will send them."""
    from openai import OpenAI
    client = OpenAI(**model.openai_client_kwargs(timeout=120.0))
    extra = {"provider": {"order": list(model.spec.provider_order), "allow_fallbacks": True}} \
        if model.spec.provider_order else None
    kwargs = {"model": model.model_id, "max_output_tokens": 512}
    if extra:
        kwargs["extra_body"] = extra
    if model.spec.reasoning and reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}

    def ask(content: list) -> str:
        r = client.responses.create(input=[{"role": "user", "content": content}], **kwargs)
        return (getattr(r, "output_text", "") or "").strip()

    try:
        txt = ask([{"type": "input_text", "text": "Reply with exactly: OK"}])
    except Exception as e:  # noqa: BLE001 — the probe reports, the run decides
        log(f"     .. /responses text request failed: {type(e).__name__}: {str(e)[:160]}")
        return False, f"/responses text request failed ({type(e).__name__}: {str(e)[:80]})"
    if not txt:
        return False, "/responses text request returned nothing (endpoint or model id is wrong)"
    try:
        img = ask([{"type": "input_text", "text": "What colour is this square? One word."},
                   {"type": "input_image", "image_url": f"data:image/png;base64,{_red_square_b64()}",
                    "detail": "high"}])
    except Exception as e:  # noqa: BLE001
        log(f"     .. /responses image request failed: {type(e).__name__}: {str(e)[:160]}")
        return False, f"/responses text ok, IMAGE request failed ({type(e).__name__}: {str(e)[:80]})"
    if not img:
        return False, "/responses text ok, IMAGE request returned nothing — the agent only sends screenshots"
    return True, f"/responses text+image ok (image answer: {img[:24]!r})"
