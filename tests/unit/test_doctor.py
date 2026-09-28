from osworld_science.config import Settings
from osworld_science.doctor import checks


def test_doctor_reports_every_requirement():
    rows = checks(Settings.load(), workers=1)
    names = {r[0] for r in rows}
    for expected in ("host", "cpu virtualisation", "/dev/kvm", "/dev/net/tun", "docker", "qemu-img",
                     "cpu capacity", "memory", "base image", "ports", "python", "Rscript", "model keys"):
        assert expected in names, expected
    assert all(r[1] in ("ok", "warn", "fail") for r in rows)
    assert any(r[0].startswith("snapshot ubuntu_") for r in rows)
