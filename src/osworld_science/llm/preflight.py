"""Endpoint probe: one text request and one image request per model."""
from __future__ import annotations

import base64
import io
import os
import pathlib

from .caller import ModelCaller
from .models import ResolvedModel


def preflight(model: ResolvedModel, reasoning_effort: str = "", log=print) -> tuple[bool, str]:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "PNG")  # 1×1 is rejected by Azure/Anthropic
    png_b64 = base64.b64encode(buf.getvalue()).decode()
    caller = ModelCaller(model, pathlib.Path(os.devnull), reasoning_effort=reasoning_effort, log=log)
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
