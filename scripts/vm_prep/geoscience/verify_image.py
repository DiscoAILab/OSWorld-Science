#!/usr/bin/env python3
"""Verify the QGIS image: is QGIS there, does it work (GUI and headless), is the image clean.

Adapted from the QuPath image verifier; the numbered sections (1)-(17) are the
acceptance items. The key design is unchanged: what is verified is the
*product qcow2*, booted in a *fresh container* with a *fresh volume*, because
"installed" and "stored in the image" are two different things (the overlay
lives in the container layer and is gone after docker rm).

QGIS-specific points (calibrated on this host against the baked 3.44.13 image, 2026-09-03):
  * The version must be pinned to 3.44.13: the frozen upstream .so of the rareplanes
    task links libqgis_core.so.3.44.13, and even 3.44.14 breaks it.
  * The probes are not task assets (no licence coupling): probe/probe.tif (64×64,
    EPSG:32633, GT=(500000,10,0,4649776,0,-10), band checksum 48341) and probe.gpkg
    (layer probe_units, three square features) come from the recipe in
    probe/make_probe.py (any machine with osgeo can regenerate them). The truth is
    hard-coded in the TRUTH dicts below; the truth table and the sync obligation
    are in probe/README.md.
  * Cleanliness is judged by *package ownership*, not by a path whitelist (changed
    2026-09-06, see the long comment at (15)): every data-like file found is looked
    up in dpkg -S and in the pip dist-info/RECORD files; owned files pass, orphans
    are reported. Ground truth written with sudo into /usr/share/qgis or /usr/lib
    during a bake is therefore still caught, while the test data shipped with
    numpy/gdal/matplotlib no longer raises false alarms. The per-file exclusion of
    world_map.gpkg is no longer needed: the qgis-data package owns it.
  * The guest has no xdotool: windows via wmctrl, the mouse via pyautogui and
    /cursor_position; GUI applications must be started through /setup/launch
    (nohup through /execute does not come up).
  * /execute defaults to /bin/sh (dash); use an explicit bash -c for bash semantics;
    120 s hard timeout.

Usage:
    python3 verify_image.py [--image PATH] [--port 5106] [--keep]
"""
from __future__ import annotations

import argparse
import io
import os
import pathlib
import statistics
import subprocess
import sys
import tempfile
import time

import requests

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]                                   # repository root
DEFAULT_IMG = REPO / "data/geoscience/vm/ubuntu_qgis.qcow2"   # or --image / GEO_IMAGE
BOOT_TIMEOUT_S = 1520          # same as defaults.boot_timeout_s in snapshots.yaml
VERSION_PIN = "3.44.13"                                  # see the module docstring: must stay pinned
QGIS_BIN = "/usr/bin/qgis"
QGIS_PROCESS = "/usr/bin/qgis_process"

# probe/ truth (recipe: probe/make_probe.py; after regenerating, update these as probe/README.md requires)
PROBE_TIF = HERE / "probe/probe.tif"
PROBE_GPKG = HERE / "probe/probe.gpkg"
TIF_TRUTH = {"epsg": "32633", "size": "64x64", "origin_x": "500000", "checksum": 48341}
GPKG_TRUTH = {"layer": "probe_units", "features": 3, "epsg": "32633"}

# DOCKER_HOST auto-detection (unset -> use the rootless socket if there is one, else the default)
_denv = dict(os.environ)
if "DOCKER_HOST" not in _denv:
    _sock = f"/run/user/{os.getuid()}/docker.sock"
    if os.path.exists(_sock):
        _denv["DOCKER_HOST"] = f"unix://{_sock}"

P = F = 0


def ok(m):
    global P
    P += 1
    print(f"  ✅ {m}", flush=True)


def bad(m):
    global F
    F += 1
    print(f"  ❌ {m}", flush=True)


def info(m):
    print(f"     {m}", flush=True)


def docker(*args, **kw):
    return subprocess.run(["docker", *args], env=_denv,
                          capture_output=True, text=True, **kw)


class Guest:
    def __init__(self, port: int):
        self.b = f"http://localhost:{port}"

    def sh(self, cmd: str, timeout: int = 90) -> str:
        try:
            r = requests.post(f"{self.b}/execute",
                              json={"command": cmd, "shell": True}, timeout=timeout)
            d = r.json()
            return (d.get("output") or "") + (d.get("error") or "")
        except Exception as e:
            return f"__ERR__ {type(e).__name__}: {e}"

    def py(self, code: str, timeout: int = 110) -> str:
        """Run python in argv form, avoiding shell quoting altogether."""
        try:
            r = requests.post(f"{self.b}/execute",
                              json={"command": ["python3", "-c", code]}, timeout=timeout)
            d = r.json()
            return (d.get("output") or "") + (d.get("error") or "")
        except Exception as e:
            return f"__ERR__ {type(e).__name__}: {e}"

    def launch(self, argv: list[str]) -> bool:
        try:
            r = requests.post(f"{self.b}/setup/launch",
                              json={"command": argv, "shell": False}, timeout=60)
            return r.status_code == 200
        except Exception:
            return False

    def alive(self, timeout: int = 6) -> bool:
        try:
            return requests.get(f"{self.b}/screenshot", timeout=timeout).status_code == 200
        except Exception:
            return False

    def shot(self) -> bytes:
        return requests.get(f"{self.b}/screenshot", timeout=30).content

    def cursor(self):
        return requests.get(f"{self.b}/cursor_position", timeout=15).json()

    def put(self, host: pathlib.Path, guest_path: str) -> bool:
        """Upload, then re-check the byte count: HTTP 200 does not prove the file was
        written in full (a truncated upload would later look like a calibration mismatch)."""
        with open(host, "rb") as fh:
            r = requests.post(f"{self.b}/setup/upload",
                              data={"file_path": guest_path},
                              files={"file_data": fh}, timeout=1800)
        if r.status_code != 200:
            return False
        want = host.stat().st_size
        got = self.sh(f"stat -c %s {guest_path} 2>/dev/null").strip()
        if got != str(want):
            print(f"     size mismatch after upload: {guest_path} expected {want}, got {got or 'unreadable'}")
            return False
        return True

    def pull_ok(self, guest_path: str) -> bool:
        """The POST /file evidence channel (what collect depends on), checked on its own."""
        try:
            r = requests.post(f"{self.b}/file", data={"file_path": guest_path}, timeout=60)
            return r.status_code == 200 and len(r.content) > 0
        except Exception:
            return False


def main() -> int:
    ap = argparse.ArgumentParser()
    # The image comes from the same override variables the run scripts honour (GEO_IMAGE first,
    # QGIS_IMAGE as the older name); otherwise, in an environment that sets them, what is
    # verified is not what is run.
    ap.add_argument("--image", default=None,
                    help="default: GEO_IMAGE, then QGIS_IMAGE, then data/geoscience/vm/ubuntu_qgis.qcow2")
    # The default port is outside the range osci assigns to benchmark guests, so a verify run
    # (especially with --keep) never holds a slot a benchmark run wants.
    ap.add_argument("--port", type=int, default=5106)
    ap.add_argument("--keep", action="store_true", help="keep the container after verifying, for manual inspection")
    a = ap.parse_args()

    if a.image:
        img, img_src = pathlib.Path(a.image).resolve(), "--image"
    elif os.environ.get("GEO_IMAGE"):
        img, img_src = pathlib.Path(os.environ["GEO_IMAGE"]).resolve(), "GEO_IMAGE"
    elif os.environ.get("QGIS_IMAGE"):
        img, img_src = pathlib.Path(os.environ["QGIS_IMAGE"]).resolve(), "QGIS_IMAGE"
    else:
        img, img_src = DEFAULT_IMG.resolve(), "default (data/geoscience/vm/)"
    name, vol = f"qgis_verify_{os.getpid()}", f"qgis_verify_vol_{os.getpid()}"
    g = Guest(a.port)

    print("════ image under test ════")
    print(f"  {img}  [source: {img_src}]")
    if not img.is_file():
        bad("image file does not exist")
        return 1
    info(f"{img.stat().st_size / 2**30:.2f} GB")

    # ── (1) must be a self-contained image ──────────────────────────────
    r = docker("run", "--rm", "--entrypoint", "qemu-img",
               "-v", f"{img.parent}:/d:ro", "happysixd/osworld-docker",
               "info", f"/d/{img.name}")
    if r.returncode != 0:
        bad(f"qemu-img info did not run (rc {r.returncode}); cannot tell whether there is a backing file: "
            f"{(r.stdout + r.stderr).strip()[:200]}")
    elif "backing file:" in r.stdout:
        bad(f"still has a backing file: {[x for x in r.stdout.splitlines() if 'backing' in x]}")
    else:
        ok("self-contained image (no backing file; usable as is)")

    try:
        # ── (2)(3)(4) fresh container + bootable + writable FS ───────────
        print("\n════ fresh container + fresh volume ════")
        p = a.port
        r = docker("run", "-d", "--name", name,
                   "--device", "/dev/kvm", "--device", "/dev/net/tun",
                   "--cap-add", "CAP_NET_ADMIN",
                   "-p", f"{p}:5000", "-p", f"{p+3006}:8006",
                   "-e", "BOOT=http://example.com/image.iso", "-e", "DISK_SIZE=128G",
                   "-e", "NETWORK=user", "-e", "USER_PORTS=5000,9222,8080",
                   # Overridable like the run scripts: the original host used 12G/6 cores, and
                   # small hosts (WSL2 and the like) must be able to go lower, otherwise the
                   # acceptance always fails there. The defaults match configs/snapshots.yaml,
                   # i.e. the size osci actually boots.
                   "-e", f"RAM_SIZE={os.environ.get('RAM_SIZE', '8G')}",
                   "-e", f"CPU_CORES={os.environ.get('CPU_CORES', '4')}",
                   "-v", f"{img}:/System.qcow2:ro", "-v", f"{vol}:/storage",
                   "happysixd/osworld-docker")
        if r.returncode:
            bad(f"container did not start: {r.stderr[:200]}")
            return 1
        ok(f"container up ({name}, :{p})")

        t0 = time.time()
        while time.time() - t0 < BOOT_TIMEOUT_S and not g.alive():
            time.sleep(5)
        if not g.alive(10):
            bad(f"guest did not boot ({BOOT_TIMEOUT_S}s)")
            return 1
        ok(f"guest boots ({time.time()-t0:.0f}s)")
        ok("filesystem writable") if "RW" in g.sh(
            "touch /home/user/.wt && rm -f /home/user/.wt && echo RW", 40) \
            else bad("filesystem read-only")

        # ── (5)(6) QGIS present, version pinned ──────────────────────────
        print("\n════ QGIS ════")
        ok(f"executable present: {QGIS_BIN}") if "YES" in g.sh(f"test -x {QGIS_BIN} && echo YES", 30) \
            else bad(f"{QGIS_BIN} missing")
        ver = g.sh(f"{QGIS_BIN} --version 2>/dev/null | head -1", 110).strip()
        ok(f"version pinned {VERSION_PIN}: {ver}") if VERSION_PIN in ver \
            else bad(f"version is not {VERSION_PIN} (the rareplanes .so link would break): {ver!r}")

        # ── (7)(8) headless channels: qgis_process + PyQGIS/GDAL known values ──
        print("\n════ headless channels ════")
        out = g.sh(f"{QGIS_PROCESS} --version 2>/dev/null | head -1", 110).strip()
        ok(f"qgis_process works: {out}") if VERSION_PIN in out \
            else bad(f"qgis_process broken: {out!r}")
        out = g.py("from qgis.core import Qgis; print('PYQGIS='+Qgis.QGIS_VERSION)")
        ok(f"PyQGIS imports ({out.strip()})") if f"PYQGIS={VERSION_PIN}" in out \
            else bad(f"PyQGIS broken: {out[:150]!r}")
        out = g.py("from osgeo import ogr\n"
                   "g=ogr.CreateGeometryFromWkt('POLYGON((0 0,80 0,80 80,0 80,0 0))')\n"
                   "print('AREA='+str(g.GetArea()))")
        ok("GDAL/OGR geometry engine works (80×80 → 6400)") if "AREA=6400" in out \
            else bad(f"OGR area wrong: {out[:150]!r}")

        # ── (9)(10) end to end: upload the probes, read the calibration back ──
        print("\n════ end to end: geo probes read back ════")
        for probe in (PROBE_TIF, PROBE_GPKG):
            if not probe.is_file():
                bad(f"probe missing: {probe} (regenerate with probe/make_probe.py; truth table in probe/README.md)")
                return 1
        g.sh("mkdir -p /home/user/vtest", 30)
        if not (g.put(PROBE_TIF, "/home/user/vtest/probe.tif")
                and g.put(PROBE_GPKG, "/home/user/vtest/probe.gpkg")):
            bad("probe upload failed (/setup/upload)")
        else:
            ok("both probes uploaded (/setup/upload works)")
            out = g.py(
                "from osgeo import gdal, osr\n"
                "ds=gdal.Open('/home/user/vtest/probe.tif')\n"
                "srs=osr.SpatialReference(wkt=ds.GetProjection())\n"
                "print('EPSG='+srs.GetAuthorityCode(None))\n"
                "print('SIZE=%dx%d'%(ds.RasterXSize,ds.RasterYSize))\n"
                "print('OX=%d'%ds.GetGeoTransform()[0])\n"
                "print('CS=%d'%ds.GetRasterBand(1).Checksum())")
            got = dict(x.split("=", 1) for x in out.strip().splitlines() if "=" in x)
            if (got.get("EPSG") == TIF_TRUTH["epsg"] and got.get("SIZE") == TIF_TRUTH["size"]
                    and got.get("OX") == TIF_TRUTH["origin_x"]
                    and got.get("CS") == str(TIF_TRUTH["checksum"])):
                ok(f"raster probe calibrates: EPSG:{got['EPSG']} {got['SIZE']} checksum={got['CS']}")
            else:
                # expected values are built from the TRUTH dict, so a regenerated probe needs one edit
                bad(f"raster calibration wrong: {got} (want EPSG:{TIF_TRUTH['epsg']} {TIF_TRUTH['size']} "
                    f"OX={TIF_TRUTH['origin_x']} CS={TIF_TRUTH['checksum']})")
            out = g.py(
                "from osgeo import ogr\n"
                "ds=ogr.Open('/home/user/vtest/probe.gpkg')\n"
                "lyr=ds.GetLayer(0)\n"
                "print('LAYER='+lyr.GetName())\n"
                "print('N=%d'%lyr.GetFeatureCount())\n"
                "print('EPSG='+lyr.GetSpatialRef().GetAuthorityCode(None))")
            got = dict(x.split("=", 1) for x in out.strip().splitlines() if "=" in x)
            if (got.get("LAYER") == GPKG_TRUTH["layer"]
                    and got.get("N") == str(GPKG_TRUTH["features"])
                    and got.get("EPSG") == GPKG_TRUTH["epsg"]):
                ok(f"vector probe calibrates: {got['LAYER']} ×{got['N']} EPSG:{got['EPSG']}")
            else:
                bad(f"vector calibration wrong: {got} (want {GPKG_TRUTH['layer']} ×{GPKG_TRUTH['features']} "
                    f"EPSG:{GPKG_TRUTH['epsg']})")
        # the evidence channel grading depends on: POST /file
        ok("evidence channel POST /file works") if g.pull_ok("/home/user/vtest/probe.tif") \
            else bad("POST /file does not work: collect cannot pull any deliverable")

        # ── (11)(12) GUI: /setup/launch starts QGIS, a window appears ────
        print("\n════ GUI ════")
        ok("launch request sent (/setup/launch)") if g.launch([QGIS_BIN]) \
            else bad("/setup/launch failed")
        t0, win = time.time(), ""
        while time.time() - t0 < 240:
            w = g.sh("DISPLAY=:0 wmctrl -l 2>/dev/null", 30)
            if "QGIS" in w:
                win = w
                break
            time.sleep(5)
        if win:
            line = [x for x in win.splitlines() if "QGIS" in x]
            ok(f"GUI window appeared ({time.time()-t0:.0f}s)")
            info(f"wmctrl: {line[0].strip() if line else ''}")
        else:
            bad("no QGIS window within 240s")
            info(g.sh("ps aux | grep -i qgis | grep -v grep | head -2", 30)[:200])

        # ── (13) the screenshot has content ──────────────────────────────
        try:
            raw = g.shot()
            from PIL import Image
            im = Image.open(io.BytesIO(raw)).convert("L")
            px = im.tobytes()[::997]
            mean, sd = statistics.mean(px), statistics.pstdev(px)
            dest = pathlib.Path(tempfile.gettempdir()) / "osci_qgis_verify_shot.png"
            dest.write_bytes(raw)
            if sd > 5:
                ok(f"screenshot has content: {im.size[0]}x{im.size[1]} mean {mean:.0f} sd {sd:.0f}")
                info(f"saved to {dest}")
            else:
                bad(f"screenshot looks like a flat colour (GUI not drawn): mean {mean:.0f} sd {sd:.0f}")
        except Exception as e:
            bad(f"screenshot analysis failed: {type(e).__name__}: {e}")

        # ── (14) GUI action channel ──────────────────────────────────────
        print("\n════ GUI action channel (pyautogui, the OSWorld action space) ════")
        try:
            before = g.cursor()
            g.sh("DISPLAY=:0 python3 -c 'import pyautogui; pyautogui.moveTo(640,400)'", 40)
            time.sleep(1)
            after = g.cursor()
            ok(f"mouse can be driven: {before} → {after}") if after == [640, 400] \
                else bad(f"mouse did not reach the target: {before} → {after}")
        except Exception as e:
            bad(f"action channel error: {type(e).__name__}: {e}")

        # ── (15) cleanliness ─────────────────────────────────────────────
        print("\n════ cleanliness ════")
        # Criterion = package ownership, not a path whitelist (changed 2026-09-06; before that
        # a per-file exclusion table).
        #   * Why: once the whole-tree exclusions were tightened to per-file ones, this item had
        #     not been run on a live guest again. The first live rerun (2026-09-06) produced 72
        #     hits, all system content: numpy/matplotlib test data, /usr/share/gdal, QGIS's
        #     metadata-ISO, grass78, distro-info... The exclusion table would either grow to
        #     dozens of lines (and drift with every image) or fall back to whole trees.
        #   * The new criterion is stronger, not weaker: instead of "is this path whitelisted",
        #     it asks "does an installed package own this file". Ground truth written with sudo
        #     into /usr during a bake has no owner and is still caught, which is exactly the
        #     case a path whitelist could not cover.
        #   * Two ownership sources: dpkg -S (deb-installed) ∪ the dist-info/RECORD files in
        #     site-packages (pip-installed; this image has numpy 1.26.2 in ~/.local, invisible
        #     to dpkg). Measured: 2 s, 2956 index entries, 0 orphans.
        #   * Fail-closed by construction: if the index cannot be built it is empty and comm -23
        #     reports every hit as an orphan, so a broken criterion gets noisier, never silently
        #     permissive. The index size is printed for a human to see.
        #   * Patterns include *.csv / *.points (the old *_results.csv is subsumed by *.csv;
        #     ndvi_by_unit.csv and registration.points were not covered by the old table), and
        #     the six deliverable basenames are named explicitly, so a narrowed wildcard in
        #     the future still leaves the answer file names covered.
        #   * The command ends with the __SCAN_DONE__ sentinel: the guest's /execute has a 120 s
        #     hard timeout, a killed find returns 500 with no output, and empty output would
        #     read as "clean". The scan only counts as finished when the sentinel is seen.
        raw = g.sh(
            r"find / -xdev \( -iname '*.qgz' -o -iname '*.qgs' -o -iname 'gt_*.json' "
            r"-o -iname '*.gpkg' -o -iname '*.tif' -o -iname '*.tiff' "
            r"-o -iname '*.csv' -o -iname '*.points' "
            r"-o -iname 'ndvi_by_unit.csv' -o -iname 'aircraft_points.gpkg' "
            r"-o -iname 'registration.points' -o -iname 'building_footprints.gpkg' "
            r"-o -iname 'road_centerlines.gpkg' -o -iname 'submission.gpkg' \) "
            r"-not -path '/proc/*' -not -path '/sys/*' "
            r"-not -path '/home/user/vtest/*' 2>/dev/null | sort -u > /tmp/_hits; "
            # deb ownership
            r"xargs -a /tmp/_hits -d'\n' dpkg -S 2>/dev/null "
            r"| sed 's/^[^:]*: //' | sort -u > /tmp/_owned; "
            # pip ownership (RECORD paths are relative to site-packages; add the prefix)
            r"for sp in /home/user/.local/lib/python3*/site-packages "
            r"/usr/lib/python3/dist-packages /usr/local/lib/python3*/dist-packages; do "
            r"[ -d $sp ] || continue; "
            r"cat $sp/*/RECORD 2>/dev/null | cut -d, -f1 | sed -e s@^@$sp/@; "
            r"done | sort -u >> /tmp/_owned; sort -u /tmp/_owned -o /tmp/_owned; "
            r"echo __IDX__ $(wc -l < /tmp/_hits) $(wc -l < /tmp/_owned); "
            r"{ comm -23 /tmp/_hits /tmp/_owned | head -40; } 2>/dev/null; echo __SCAN_DONE__",
            180)
        if "__SCAN_DONE__" not in raw:
            bad("cleanliness scan did not finish (no __SCAN_DONE__ sentinel; probably killed by the guest's 120 s timeout)"
                f": {raw[:120]!r}")
        else:
            lines = [x for x in raw.replace("__SCAN_DONE__", "").strip().splitlines() if x.strip()]
            idx = next((x for x in lines if x.startswith("__IDX__")), "")
            # only absolute paths count: g.sh appends stderr to the return value, and diagnostic
            # lines are not leftovers (same trap as .bash_history below)
            left = [x for x in lines if not x.startswith("__IDX__") and x.startswith("/")]
            n_hits, n_owned = (idx.split()[1:3] + ["?", "?"])[:2] if idx else ("?", "?")
            if not left:
                ok(f"no ground truth / answers / datasets / projects left behind ({n_hits} data-like files found, "
                   f"all owned by a package; ownership index {n_owned} entries)")
            else:
                bad(f"data files with NO package owner ({len(left)} of {n_hits} hits, "
                    f"ownership index {n_owned} entries):\n       " + "\n       ".join(left))
        # The measured cleanliness criterion is "neither /home/user/share nor BenchData exists";
        # share/ is the root of the six deliverables, and baked into the image it is an answer surface.
        ok("/home/user/share does not exist") if "NO_SHARE" in g.sh(
            "test -e /home/user/share && echo HAS_SHARE || echo NO_SHARE", 30) \
            else bad("/home/user/share exists: the deliverables root must not be baked into the image")
        ok("BenchData empty or absent") if g.sh(
            "ls -A /home/user/BenchData 2>/dev/null | wc -l", 30).strip() in ("0", "") \
            else bad("BenchData is not empty")
        # Do not use `wc -l < f 2>/dev/null || echo 0`: dash reports the failed `<` before
        # 2>/dev/null takes effect, g.sh appends stderr to the return value, and "0" + an error
        # message != "0" reads as non-empty. Seen on 2026-09-03: a missing file was reported as
        # ".bash_history not empty".
        ok(".bash_history empty or absent") if "EMPTY_OK" in g.sh(
            "test -s /home/user/.bash_history && echo NONEMPTY || echo EMPTY_OK", 30) \
            else bad(".bash_history is not empty")

        # ── (16) desktop/account hard requirements (the prompts and the coordinate frame rely on them) ──
        print("\n════ desktop/account ════")
        out = g.sh("whoami; pgrep -u user -x gnome-shell >/dev/null && echo GNOME; "
                   "xdpyinfo 2>/dev/null | grep dimensions", 40)
        ok("account user + GNOME + 1920x1080") if ("user" in out and "GNOME" in out
                                                    and "1920x1080" in out) \
            else bad(f"desktop/account mismatch: {out[:150]!r}")
        out = g.sh("echo password | sudo -S -k true 2>/dev/null && echo PW_OK", 40)
        ok("sudo password = 'password' (as the agent prompt states)") if "PW_OK" in out \
            else bad("password is not 'password': the prompt would mislead the model; fix the image or the prompt")

        # ── (17) toolchain: not graded, but it defines the bypass surface ──
        # The command-line tools named in check_commands of snapshots.yaml, one by one:
        # `osci vm check` treats any MISSING among them as blocking, so check them here first
        print("\n════ commands named in check_commands ════")
        for cmd in ("qgis", "python3", "gdalinfo", "firefox", "gedit"):
            out = g.sh(f"command -v {cmd} || echo MISSING", 30).strip()
            ok(f"{cmd}: {out}") if "MISSING" not in out else bad(f"{cmd} is not on PATH (named in check_commands of snapshots.yaml)")

        print("\n════ toolchain (recorded; defines the bypass surface) ════")
        for m in ("numpy", "PIL", "osgeo", "shapely", "pyautogui"):
            v = g.py(f"import {m};print(getattr({m},'__version__','?'))").strip()
            v = v if "__ERR__" not in v and "Error" not in v else ""
            print(f"  {'yes' if v else 'no '}  {m:<10} {v.splitlines()[-1] if v else ''}")
        net = g.sh("timeout 10 curl -s -o /dev/null -w '%{http_code}' "
                   "https://pypi.org/simple/ 2>&1", 40).strip()
        info(f"internet: {'reachable (pypi ' + net + '): the agent can pip install anything' if net == '200' else 'unreachable (' + net + ')'}")

    finally:
        if a.keep:
            print(f"\n  (--keep: container {name} left on :{a.port}; when done, "
                  f"docker rm -f {name} && docker volume rm {vol})")
        else:
            docker("rm", "-f", name)
            docker("volume", "rm", vol)

    print("\n" + "═" * 44)
    print(f"  passed {P} / failed {F}")
    print("  ✅ image usable" if F == 0 else "  ❌ image has problems")
    return 0 if F == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
