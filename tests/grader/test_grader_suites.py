"""Runs the answer-bearing grader test suites that travel with the data
(data/<domain>/private/grader_tests/run_tests*.py) against the scorer, by
shimming the `score.py` command they invoke.

Each suite builds a correct submission from ground truth, mutates copies of
it (attacks that must FAIL, in-tolerance variants that must PASS) and calls
`<suite root>/score.py <task> --submission <dir> --json`. The shim below is
installed as that score.py and dispatches to osworld_science.evaluators.
`run_tests.py` is the legacy per-domain suite; `run_tests_<task_id>.py` are
per-task suites written by the importers.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHIM = '''#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, {src!r})
from osworld_science.config import Settings
from osworld_science.data.layout import DataLayout
from osworld_science.evaluators.registry import EvalContext
from osworld_science.evaluators.score import grade
from osworld_science.tasks.registry import TaskSet
import argparse
ap = argparse.ArgumentParser(); ap.add_argument("task_id"); ap.add_argument("--submission", required=True, type=Path)
ap.add_argument("--json", action="store_true"); ap.add_argument("--out", type=Path)
a = ap.parse_args()
s = Settings.load(Path({repo!r}))
t = TaskSet(s.data_dir).get(a.task_id)
layout = DataLayout(s.data_dir)
res = grade(t, layout.load_gt(t.domain, t.id), a.submission, EvalContext(domain=t.domain, layout=layout, rscript=s.rscript, task=t))
out = a.out or (a.submission / "score.json")
out.write_text(json.dumps(res, indent=2))
print(json.dumps(res, indent=2))
sys.exit(0 if res["verdict"] == "PASS" else 1)
'''


def _suite_root(tmp_path_factory, domain: str) -> Path | None:
    """A throwaway suite root laid out the way the legacy scripts expect:
    tasks/, reference_private/, assets/, tests/{run_tests.py,make_fixtures.py,fixtures}, score.py."""
    from osworld_science.config import Settings
    s = Settings.load(REPO)
    gt_dir = s.data_dir / domain / "private" / "grader_tests"
    if not list(gt_dir.glob("run_tests*.py")):
        return None
    root = tmp_path_factory.mktemp(f"suite_{domain}")
    (root / "tasks").symlink_to(s.data_dir / domain / "tasks")
    (root / "reference_private").symlink_to(s.data_dir / domain / "private" / "reference_private")
    (root / "assets").symlink_to(s.data_dir / domain / "public" / "assets")
    # the linguistics suites import `evaluators.<name>_verifier` from the suite root
    ev = root / "evaluators"
    ev.mkdir()
    (ev / "__init__.py").write_text("")
    for verifier in (REPO / "src" / "osworld_science" / "evaluators" / "linguistics").glob("*_verifier.py"):
        (ev / verifier.name).symlink_to(verifier)
    tests = root / "tests"
    tests.mkdir()
    for src in list(gt_dir.glob("run_tests*.py")) + [gt_dir / "make_fixtures.py"]:
        if src.exists():
            shutil.copy(src, tests / src.name)
    for name in ("fixtures", "independent"):
        if (gt_dir / name).is_dir():
            (tests / name).symlink_to(gt_dir / name)
    (root / "score.py").write_text(SHIM.format(src=str(REPO / "src"), repo=str(REPO)))
    (root / "score.py").chmod(0o755)
    return root


@pytest.mark.data
@pytest.mark.parametrize("domain", ["linguistics", "biomed", "stat", "geoscience", "astro"])
def test_grader_suite(domain, tmp_path_factory):
    root = _suite_root(tmp_path_factory, domain)
    if root is None:
        pytest.skip(f"no grader_tests for {domain} under data/{domain}/private")
    from osworld_science.config import Settings
    rscript = Settings.load(REPO).rscript
    env = {**os.environ, "OSCI_RSCRIPT": rscript, "STAT_RSCRIPT": rscript}
    if (root / "tests" / "make_fixtures.py").exists():
        r = subprocess.run([sys.executable, "tests/make_fixtures.py"], cwd=root, env=env,
                           capture_output=True, text=True, timeout=3600)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    suites = sorted((root / "tests").glob("run_tests*.py"))
    assert suites, "no run_tests*.py in the suite root"
    for suite in suites:
        args = [sys.executable, str(suite)]
        if domain == "stat" and suite.name == "run_tests.py":
            # the independent cross-checks (run when no ids are given) take ~20 min and are
            # about the oracle, not the grader; pass every task id explicitly to skip them
            args += sorted(p.stem for p in (Settings.load(REPO).data_dir / "stat" / "tasks").glob("*.json"))
        r = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=7200)
        tail = f"[{suite.name}]\n" + r.stdout[-3000:] + r.stderr[-1500:]
        assert r.returncode == 0, tail
        # stat prints "N/N checks behaved as intended"; biomed/linguistics/geoscience print
        # "ALL BEHAVED AS INTENDED"; the astro authors' suites print "ALL TESTS PASSED" /
        # "ALL SUITES PASSED"
        low = r.stdout.lower()
        assert any(p in low for p in ("behaved as intended", "all tests passed", "all suites passed")), tail
