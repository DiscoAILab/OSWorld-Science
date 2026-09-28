"""configs/snapshots.yaml → typed snapshot registry."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Hook:
    script: str
    when_related_app: str | None = None
    required: bool = False

    def applies_to(self, related_apps: list[str]) -> bool:
        if not self.when_related_app:
            return True
        return self.when_related_app.lower() in {a.lower() for a in related_apps}


@dataclass(frozen=True)
class VMDefaults:
    docker_image: str = "happysixd/osworld-docker"
    ram: str = "8G"
    cpus: int = 4
    disk: str = "128G"
    boot_timeout_s: int = 1520
    base_image: str = "Ubuntu.qcow2"


@dataclass(frozen=True)
class Snapshot:
    name: str
    image: str
    default_port: int
    container_prefix: str
    domain: str = ""              # whose data/<domain>/vm/ holds the image
    description: str = ""
    tools_probe: str = ""
    shell_init: str = ""  # sourced before tools_probe / check_commands (e.g. a conda activate)
    provision: str | None = None
    pre_task_hooks: tuple[Hook, ...] = ()
    check_commands: tuple[str, ...] = ()
    window_classes: dict[str, str] = field(default_factory=dict)
    bake_cleanup: str = ""
    ram: str | None = None        # override defaults.ram (whole-slide-image desktops need more)
    cpus: int | None = None       # override defaults.cpus


class SnapshotRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        doc = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        d = doc.get("defaults") or {}
        self.defaults = VMDefaults(
            docker_image=d.get("docker_image", VMDefaults.docker_image),
            ram=str(d.get("ram", VMDefaults.ram)),
            cpus=int(d.get("cpus", VMDefaults.cpus)),
            disk=str(d.get("disk", VMDefaults.disk)),
            boot_timeout_s=int(d.get("boot_timeout_s", VMDefaults.boot_timeout_s)),
            base_image=str(d.get("base_image", VMDefaults.base_image)),
        )
        self._snapshots: dict[str, Snapshot] = {}
        for name, s in (doc.get("snapshots") or {}).items():
            hooks = tuple(Hook(script=str(h["script"]), when_related_app=h.get("when_related_app"),
                               required=bool(h.get("required", False)))
                          for h in (s.get("pre_task_hooks") or []))
            self._snapshots[name] = Snapshot(
                name=name, image=str(s["image"]), default_port=int(s["default_port"]),
                container_prefix=str(s.get("container_prefix") or f"osci_{name}"),
                domain=str(s.get("domain") or (name[len("ubuntu_"):] if name.startswith("ubuntu_") else name)),
                description=str(s.get("description", "")),
                tools_probe=str(s.get("tools_probe", "")),
                shell_init=str(s.get("shell_init", "") or ""),
                provision=s.get("provision"), pre_task_hooks=hooks,
                check_commands=tuple(s.get("check_commands") or ()),
                window_classes=dict(s.get("window_classes") or {}),
                bake_cleanup=str(s.get("bake_cleanup", "")),
                ram=(str(s["ram"]) if s.get("ram") else None),
                cpus=(int(s["cpus"]) if s.get("cpus") else None),
            )

    def __getitem__(self, name: str) -> Snapshot:
        try:
            return self._snapshots[name]
        except KeyError:
            raise KeyError(f"unknown snapshot {name!r}; known: {', '.join(self.names())}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._snapshots

    def names(self) -> list[str]:
        return list(self._snapshots)
