"""Guest control-plane client (the OSWorld server on guest port 5000).

Ported from the legacy harness.py (radiology/linguistics version, a superset
of the stat one). Two sharp edges, both learned the expensive way:

  * `/file` reads `request.form`, so a pull MUST be form-encoded. Sent as
    JSON it returns 400 and every deliverable "goes missing".
  * `/execute` defaults to `/bin/sh`. Anything needing bash has to say so.
  * `/execute` has a ~120 s server-side timeout that the client cannot raise;
    long installs must be detached (see scripts/vm_prep/common/guest_run.py).
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import shlex
import urllib.error
import urllib.parse
import urllib.request
import uuid

UPLOAD_CHUNK = 50_000  # 200k returns HTTP 500; 50k is the measured limit
# What `Guest.glob` runs in the guest: the regular files a pattern matches, as one JSON list
# (names with spaces or newlines survive; a shell `ls` would not carry them).
GLOB_SNIPPET = ("import glob, json, os, sys\n"
                "print(json.dumps(sorted(p for p in glob.glob(sys.argv[1]) if os.path.isfile(p))))")


class GuestUnreachable(RuntimeError):
    pass


def q(s: str) -> str:
    return shlex.quote(s)


class Guest:
    def __init__(self, port: int, host: str = "localhost"):
        self.port = int(port)
        self.hostname = host

    @property
    def host(self) -> str:
        return f"http://{self.hostname}:{self.port}"

    # ── execute ────────────────────────────────────────────────────────────
    def execute(self, command, timeout: int = 300) -> dict:
        req = urllib.request.Request(
            f"{self.host}/execute",
            data=json.dumps({"command": command}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.URLError as e:
            raise GuestUnreachable(f"guest on :{self.port} is not answering: {e}") from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"output": raw.decode("utf-8", "replace"), "error": "", "returncode": -1}

    def bash(self, script: str, timeout: int = 300, login: bool = True) -> dict:
        """Run a shell snippet under bash (`-lc` by default: a desktop terminal
        is a login shell, and that is where ~/.profile puts toolchains on PATH)."""
        return self.execute(["bash", "-lc" if login else "-c", script], timeout)

    def output(self, command, timeout: int = 300) -> str:
        r = self.execute(command, timeout)
        return ((r.get("output") or "") + (r.get("error") or "")).strip()

    def launch(self, command: list[str]) -> dict:
        """Start a GUI program on the desktop and return immediately."""
        cmd = " ".join(q(str(x)) for x in command)
        return self.execute(
            ["bash", "-lc",
             f"DISPLAY=:0 setsid nohup {cmd} >/dev/null 2>&1 < /dev/null & echo launched"], 60)

    # ── files ──────────────────────────────────────────────────────────────
    def file_exists(self, guest_path: str) -> bool:
        probe = self.execute(["bash", "-c", f"test -f {q(guest_path)} && echo YES || echo NO"])
        return "YES" in (probe.get("output") or "")

    def glob(self, pattern: str, timeout: int = 120) -> list[str]:
        """Guest paths of the regular files matching `pattern` (Python `glob` rules: `*`, `?`,
        `[...]`; no `**` recursion, dotfiles only when the pattern names the dot), sorted.
        Expanded by the guest's own python3, so like `pull` and `upload` it needs a POSIX guest.
        Raises RuntimeError when the guest cannot produce the list."""
        r = self.execute(["python3", "-c", GLOB_SNIPPET, pattern], timeout)
        try:
            found = json.loads((r.get("output") or "").strip())
            if not isinstance(found, list):
                raise ValueError("not a list")
        except ValueError:
            what = ((r.get("output") or "") + (r.get("error") or "")).strip()[-200:]
            raise RuntimeError(f"could not list {pattern!r} in the guest "
                               f"(rc={r.get('returncode')}): {what!r}") from None
        return [str(x) for x in found]

    def pull(self, guest_path: str, dest: pathlib.Path) -> bool:
        """Form-encoded, not JSON. See the module docstring."""
        dest = pathlib.Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            f"{self.host}/file",
            data=urllib.parse.urlencode({"file_path": guest_path}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body = r.read()
        except urllib.error.HTTPError:
            return False
        try:
            d = json.loads(body)
            data = base64.b64decode(d["file"]) if isinstance(d, dict) and "file" in d else body
        except Exception:  # noqa: BLE001
            data = body
        # A missing file often comes back as a JSON error packet with HTTP 200,
        # so confirm on the guest rather than trusting the status code.
        if not self.file_exists(guest_path):
            return False
        dest.write_bytes(data)
        return True

    def upload(self, src: pathlib.Path, guest_path: str) -> None:
        """Multipart /setup/upload first (one request, byte-checked); the
        chunked-base64 /execute path as the fallback."""
        src = pathlib.Path(src)
        parent = str(pathlib.PurePosixPath(guest_path).parent)
        size = src.stat().st_size
        try:
            blob = src.read_bytes()
            boundary = uuid.uuid4().hex
            body = ((f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="file_path"\r\n\r\n{guest_path}\r\n'
                     f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="file_data"; filename="{src.name}"\r\n'
                     f'Content-Type: application/octet-stream\r\n\r\n').encode()
                    + blob + f'\r\n--{boundary}--\r\n'.encode())
            self.execute(["bash", "-c", f"mkdir -p {q(parent)}"])
            req = urllib.request.Request(
                f"{self.host}/setup/upload", data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=600) as r:
                if r.status == 200:
                    got = (self.execute(["bash", "-c", f"stat -c%s {q(guest_path)}"])
                           .get("output") or "").strip()
                    if got == str(size):
                        return
        except Exception:  # noqa: BLE001
            pass  # fall through to the chunked path
        b64 = base64.b64encode(src.read_bytes()).decode()
        self.execute(["bash", "-c", f"mkdir -p {q(parent)}; : > {q(guest_path)}.b64"])
        for k in range(0, len(b64), UPLOAD_CHUNK):
            self.execute(["bash", "-c",
                          f"printf %s {q(b64[k:k + UPLOAD_CHUNK])} >> {q(guest_path)}.b64"], 180)
        r = self.execute(["bash", "-c",
                          f"base64 -d {q(guest_path)}.b64 > {q(guest_path)} && "
                          f"rm -f {q(guest_path)}.b64 && stat -c%s {q(guest_path)}"])
        got = (r.get("output") or "").strip()
        if got != str(size):
            raise RuntimeError(f"upload of {src.name} landed at {got} bytes, expected {size}")

    def sha256_of(self, guest_path: str) -> str | None:
        out = (self.execute(["bash", "-c", f"sha256sum {q(guest_path)} 2>/dev/null"])
               .get("output") or "").split()
        return out[0] if out else None

    # ── liveness / observation ─────────────────────────────────────────────
    def alive(self, require_display: bool = True) -> bool:
        """Is the guest answering? With `require_display` (boot and staging) the display must
        deliver a screenshot too; without it (collecting results) a plain command suffices,
        so a guest whose X server has stopped accepting clients still hands its files over."""
        if require_display:
            return self.screenshot(timeout=8) is not None
        try:
            return self.execute(["true"], timeout=8).get("returncode") == 0
        except GuestUnreachable:
            return False

    def screenshot(self, timeout: int = 60) -> bytes | None:
        """Through /execute, not the server's /screenshot endpoint (guest/screenshot.py)."""
        from .screenshot import capture
        return capture(self.host, timeout=timeout)

    def windows(self, timeout: int = 20) -> str:
        """`wmctrl -lx` — window list with WM_CLASS (titles are unreliable:
        a GNOME terminal's title is 'user@host: ~')."""
        return self.execute(["bash", "-lc", "DISPLAY=:0 wmctrl -lx 2>/dev/null"],
                            timeout).get("output") or ""


def sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
