"""data/<domain>/{public,private,vm} layout and the VM image resolution order."""
from pathlib import Path

from osworld_science.config import Settings
from osworld_science.data.layout import DataLayout
from osworld_science.vm.docker import VM
from osworld_science.vm.snapshots import Snapshot, SnapshotRegistry, VMDefaults


def test_layout_paths(tmp_path):
    lay = DataLayout(tmp_path)
    assert lay.public_root("stat") == tmp_path / "stat" / "public"
    assert lay.private_root("stat") == tmp_path / "stat" / "private"
    assert lay.vm_root("stat") == tmp_path / "stat" / "vm"
    assert lay.tasks_root("stat") == tmp_path / "stat" / "tasks"
    assert lay.resolve("stat", "assets/x.tgz") == tmp_path / "stat" / "public" / "assets" / "x.tgz"
    assert lay.resolve("stat", "reference_private/t/gt.json") == tmp_path / "stat" / "private" / "reference_private" / "t" / "gt.json"
    assert lay.gt_path("ling", "t") == tmp_path / "ling" / "private" / "reference_private" / "t" / "gt.json"
    (tmp_path / "rad" / "vm").mkdir(parents=True)
    (tmp_path / "stat" / "public").mkdir(parents=True)
    (tmp_path / "stat" / "private").mkdir(parents=True)
    (tmp_path / "junk").mkdir()
    assert lay.available_domains() == ["rad", "stat"] and lay.has_data("stat") and not lay.has_data("rad")


def test_snapshots_carry_a_domain():
    s = Settings.load()
    reg = SnapshotRegistry(s.configs_dir / "snapshots.yaml")
    assert {reg[n].domain for n in reg.names()} == {"stat", "radiology", "linguistics", "biomed", "chem", "geoscience", "astro", "physics"}


def test_image_resolution_order(tmp_path):
    s = Settings.load()
    s.data_dir = tmp_path / "data"
    s.vm_dir = tmp_path / "vm"
    snap = Snapshot(name="ubuntu_t", image="ubuntu_t.qcow2", default_port=5997, container_prefix="osci_t", domain="t")
    vm = VM(snap, VMDefaults(), s, port=5997, log=lambda *_: None)
    base = s.vm_dir / "base" / "Ubuntu.qcow2"
    assert vm.image == base                                     # nothing prepared: the base
    local = s.vm_dir / "images" / "ubuntu_t.qcow2"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"x")
    assert vm.image == local                                    # local fallback
    domain_img = s.data_dir / "t" / "vm" / "ubuntu_t.qcow2"
    domain_img.parent.mkdir(parents=True)
    domain_img.write_bytes(b"y")
    assert vm.image == domain_img and vm.default_bake_target == domain_img   # the dataset copy wins
    assert VM(snap, VMDefaults(), s, port=5997, image=Path("/x/y.qcow2"), log=lambda *_: None).image == Path("/x/y.qcow2")
