"""Settings: `.env` + environment + repo-relative defaults.

Precedence: process environment > `.env` file > defaults. Secrets are read
into `Settings.env` and never logged; `Settings.describe()` redacts them.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_SECRET_HINT = re.compile(r"(KEY|TOKEN|SECRET|PASS)", re.I)


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the repository root: $OSCI_ROOT, else walk up from `start`
    (default cwd) looking for `pyproject.toml` + `configs/` + `src/osworld_science/`,
    else fall back to
    the source checkout this file lives in."""
    if os.environ.get("OSCI_ROOT"):
        return Path(os.environ["OSCI_ROOT"]).expanduser().resolve()
    here = (start or Path.cwd()).resolve()
    for p in (here, *here.parents):
        if (p / "pyproject.toml").is_file() and (p / "configs").is_dir() and (p / "src" / "osworld_science").is_dir():
            return p
    return Path(__file__).resolve().parents[2]


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Values keep everything after '=' (quotes stripped
    when the whole value is quoted). Never prints anything."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def expand_template(template: str, env: dict[str, str]) -> str:
    """Expand `${VAR}` and `${VAR:-default}` in URL templates."""

    def sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        val = env.get(name, "")
        if val:
            return val
        if default is not None:
            return default
        raise KeyError(name)

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", sub, template)


@dataclass
class Settings:
    repo_root: Path
    env: dict[str, str] = field(default_factory=dict, repr=False)

    # paths
    data_dir: Path = field(default=None)  # type: ignore[assignment]
    vm_dir: Path = field(default=None)  # type: ignore[assignment]
    runs_dir: Path = field(default=None)  # type: ignore[assignment]
    vm_fast_dir: Path = field(default=None)  # type: ignore[assignment]
    vm_copy_images: str = "auto"          # auto | always | never (see vm/docker.py)
    # tools
    docker_host: str = ""
    rscript: str = "Rscript"
    # agent / request knobs
    image_detail: str = "high"
    max_tokens: int = 16000
    reasoning_effort: str = ""
    max_actions_per_step: int = 10       # 0 = unlimited; see runner/episode.py
    # sweeps
    port_base: int = 5040
    max_workers: int = 4
    # hugging face
    hf_data_repo: str = ""

    @classmethod
    def load(cls, repo_root: Path | None = None, env_file: Path | None = None) -> Settings:
        root = (repo_root or find_repo_root()).resolve()
        env = read_env_file(env_file or (root / ".env"))
        env.update({k: v for k, v in os.environ.items()})  # process env wins

        def path(key: str, default: str) -> Path:
            p = Path(env.get(key) or default).expanduser()
            return p if p.is_absolute() else (root / p).resolve()

        def integer(key: str, default: int) -> int:
            try:
                return int(env.get(key) or default)
            except ValueError:
                return default

        s = cls(repo_root=root, env=env)
        s.data_dir = path("OSCI_DATA_DIR", "data")
        s.vm_dir = path("OSCI_VM_DIR", "vm")
        s.runs_dir = path("OSCI_RUNS_DIR", "runs")
        s.vm_fast_dir = Path(env.get("OSCI_VM_FAST_DIR") or "/tmp/osci_vm").expanduser()
        s.vm_copy_images = (env.get("OSCI_VM_COPY_IMAGES") or "auto").strip().lower()
        if s.vm_copy_images not in ("auto", "always", "never"):
            raise ValueError(f"OSCI_VM_COPY_IMAGES must be auto|always|never, not {s.vm_copy_images!r}")
        s.docker_host = env.get("OSCI_DOCKER_HOST") or env.get("DOCKER_HOST") or ""
        s.rscript = env.get("OSCI_RSCRIPT") or "Rscript"
        s.image_detail = env.get("OSCI_IMAGE_DETAIL") or "high"
        s.max_tokens = integer("OSCI_MAX_TOKENS", 16000)
        s.reasoning_effort = env.get("OSCI_REASONING_EFFORT") or ""
        s.max_actions_per_step = integer("OSCI_MAX_ACTIONS_PER_STEP", 10)
        s.port_base = integer("OSCI_PORT_BASE", 5040)
        s.max_workers = integer("OSCI_MAX_WORKERS", 4)
        s.hf_data_repo = env.get("OSCI_HF_DATA_REPO") or ""
        return s

    # convenience
    @property
    def configs_dir(self) -> Path:
        return self.repo_root / "configs"

    def describe(self) -> dict:
        """Everything except secrets, for meta.json and logs."""
        return {
            "repo_root": str(self.repo_root),
            "data_dir": str(self.data_dir),
            "vm_dir": str(self.vm_dir),
            "runs_dir": str(self.runs_dir),
            "vm_fast_dir": str(self.vm_fast_dir),
            "vm_copy_images": self.vm_copy_images,
            "rscript": self.rscript,
            "image_detail": self.image_detail,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort or None,
            "max_actions_per_step": self.max_actions_per_step,
            "env_keys_present": sorted(k for k in self.env if k.startswith(("OSCI_", "OPENAI", "ANTHROPIC", "OPENROUTER", "GEMINI", "QWEN", "HF_"))
                                        and not _SECRET_HINT.search(k)),
        }
