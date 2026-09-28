"""Parallel workers must share one local image copy: exactly one copy is made,
the others wait for it, and every worker gets the same intact file."""
import shutil
import threading
from pathlib import Path

from osworld_science.config import Settings
from osworld_science.vm.docker import VM
from osworld_science.vm.snapshots import Snapshot, VMDefaults


def _vm(tmp_path: Path, image: Path, log) -> VM:
    s = Settings.load(tmp_path.parents[0] if (tmp_path / "pyproject.toml").exists() else None)
    s.vm_fast_dir = tmp_path / "fast"
    s.vm_copy_images = "always"          # the copy path is what this test exercises
    snap = Snapshot(name="t", image=image.name, default_port=5999, container_prefix="osci_t")
    return VM(snap, VMDefaults(), s, port=5999, image=image, log=log)


def test_concurrent_workers_copy_once(tmp_path, monkeypatch):
    image = tmp_path / "images" / "ubuntu_t.qcow2"
    image.parent.mkdir()
    image.write_bytes(b"QFI\xfb" + bytes(range(256)) * 64)  # not a real qcow2; qemu-img sees "raw", no backing file
    monkeypatch.setattr(VM, "_refuse_backing_file", lambda self, p: None)
    copies, lines = [], []
    real_copy = shutil.copyfile

    def slow_copy(src, dst, *a, **kw):
        copies.append(dst)
        threading.Event().wait(0.2)          # make the race window wide
        return real_copy(src, dst, *a, **kw)
    monkeypatch.setattr(shutil, "copyfile", slow_copy)

    results, errors = [], []

    def worker():
        try:
            results.append(_vm(tmp_path, image, lines.append).ensure_local_copy())
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], errors
    assert len(copies) == 1, "the image must be copied exactly once"
    assert len(set(results)) == 1 and results[0] == tmp_path / "fast" / "ubuntu_t.qcow2"
    assert results[0].read_bytes() == image.read_bytes()
    assert not list((tmp_path / "fast").glob("*.part*"))
    assert sum("waiting for it" in ln for ln in lines) == 2 and sum(ln == "copied" for ln in lines) == 1
    # a second call finds the copy and does nothing
    assert _vm(tmp_path, image, lines.append).ensure_local_copy() == results[0] and len(copies) == 1
