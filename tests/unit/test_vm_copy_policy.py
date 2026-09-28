"""OSCI_VM_COPY_IMAGES decides whether an image is copied or mounted in place;
`osci vm-cache` lists and cleans the local copies."""
from pathlib import Path

from osworld_science.config import Settings
from osworld_science.vm import docker as d
from osworld_science.vm.docker import VM
from osworld_science.vm.snapshots import Snapshot, VMDefaults


def _vm(tmp_path: Path, image: Path, policy: str, log) -> VM:
    s = Settings.load()
    s.vm_fast_dir = tmp_path / "fast"
    s.vm_copy_images = policy
    snap = Snapshot(name="t", image=image.name, default_port=5998, container_prefix="osci_t")
    return VM(snap, VMDefaults(), s, port=5998, image=image, log=log)


def test_policy_never_and_auto_local_mount_in_place(tmp_path, monkeypatch):
    image = tmp_path / "img.qcow2"
    image.write_bytes(b"x" * 1024)
    monkeypatch.setattr(VM, "_refuse_backing_file", lambda self, p: None)
    monkeypatch.setattr(d, "filesystem_type", lambda p: "xfs")
    lines = []
    assert _vm(tmp_path, image, "never", lines.append).ensure_local_copy() == image.resolve()
    assert _vm(tmp_path, image, "auto", lines.append).ensure_local_copy() == image.resolve()
    assert not (tmp_path / "fast").exists() and all("in place" in ln for ln in lines)


def test_policy_auto_copies_from_network_fs_and_always_copies(tmp_path, monkeypatch):
    image = tmp_path / "img.qcow2"
    image.write_bytes(b"y" * 2048)
    monkeypatch.setattr(VM, "_refuse_backing_file", lambda self, p: None)
    monkeypatch.setattr(d, "filesystem_type", lambda p: "nfs4")
    lines = []
    got = _vm(tmp_path, image, "auto", lines.append).ensure_local_copy()
    assert got == tmp_path / "fast" / "img.qcow2" and got.read_bytes() == image.read_bytes()
    monkeypatch.setattr(d, "filesystem_type", lambda p: "ext4")
    got2 = _vm(tmp_path, image, "always", lines.append).ensure_local_copy()
    assert got2 == got and lines[-1].startswith("local working copy already present")


def test_filesystem_type_reads_proc_mounts(tmp_path, monkeypatch):
    mounts = "rootfs / rootfs rw 0 0\n/dev/x / xfs rw 0 0\nnas:/vol /mnt/nas nfs4 rw 0 0\n/dev/y /mnt/nas/local ext4 rw 0 0\n"
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: mounts if str(self) == "/proc/mounts" else "")
    assert d.filesystem_type(Path("/mnt/nas/data/img.qcow2")) == "nfs4"
    assert d.filesystem_type(Path("/mnt/nas/local/img.qcow2")) == "ext4"   # longest mount wins
    assert d.is_network_fs(Path("/mnt/nas/img.qcow2")) and not d.is_network_fs(Path("/home/x"))


def test_cache_list_and_clean(tmp_path, monkeypatch):
    s = Settings.load()
    s.vm_fast_dir = tmp_path / "fast"
    s.vm_fast_dir.mkdir()
    used = s.vm_fast_dir / "a.qcow2"
    unused = s.vm_fast_dir / "b.qcow2"
    used.write_bytes(b"a" * 10)
    unused.write_bytes(b"b" * 20)
    (s.vm_fast_dir / "b.qcow2.lock").write_text("")
    monkeypatch.setattr(d, "mounted_images", lambda settings: {str(used): ["osci_t_5040"]})
    entries = {Path(e["path"]).name: e for e in d.cache_entries(s)}
    assert entries["a.qcow2"]["used_by"] == ["osci_t_5040"] and entries["b.qcow2"]["used_by"] == []
    removed = d.clean_cache(s, log=lambda *_: None)
    assert removed == [str(unused)] and used.exists() and not unused.exists()
    assert not (s.vm_fast_dir / "b.qcow2.lock").exists()
    assert d.clean_cache(s, all_copies=True, log=lambda *_: None) == [str(used)] and not used.exists()


def test_rebuild_frees_our_previous_occupant_and_refuses_foreign(tmp_path, monkeypatch):
    """A slot held by our container of another snapshot is freed; a foreign holder is an error."""
    from osworld_science.vm.docker import VMError
    image = tmp_path / "img.qcow2"
    image.write_bytes(b"x")
    calls = []
    vm = _vm(tmp_path, image, "never", lambda *_: None)
    vm.name = "osci_stat_5042"
    monkeypatch.setattr(vm, "docker", lambda *args, **kw: calls.append(args) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(d, "containers_publishing", lambda env, port: [("osci_ling_5042", True), ("osci_stat_5042", True)])
    vm._free_port_holders()
    assert ("rm", "-f", "osci_ling_5042") in calls and ("volume", "rm", "osci_ling_5042_storage") in calls
    assert not any("osci_stat_5042" in c for c in calls)          # our own name is left to start()
    monkeypatch.setattr(d, "containers_publishing", lambda env, port: [("orion_stat", False)])
    try:
        vm._free_port_holders()
    except VMError as e:
        assert "orion_stat" in str(e) and "not create" in str(e)
    else:
        raise AssertionError("a foreign container holding the port must be refused")
