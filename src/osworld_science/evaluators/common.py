"""Evaluators shared by every domain: json_fields, csv_table,
protected_inputs_hash, artifact. Ported from the legacy checks.py; the
flattened-input fallback in protected_inputs_hash comes from the radiology
suite and is a superset of the stat behaviour."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ._io import read_csv, read_json
from .core import compare_scalar, index_columns, normalise_key, summarise, walk_spec
from .registry import EvalContext, register


@register("json_fields")
def json_fields(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Grade a submitted JSON against a spec tree from ground truth."""
    doc, err = read_json(sub / params["file"])
    if err:
        return 0.0, {"error": err}
    spec = gt
    for step in params.get("gt_path", []):
        spec = spec[step]
    root = doc
    for step in params.get("doc_path", []):
        if not isinstance(root, dict) or step not in root:
            return 0.0, {"error": f"submission has no section {'.'.join(params['doc_path'])!r}"}
        root = root[step]
    return summarise(walk_spec(spec, root))


@register("csv_table")
def csv_table(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Grade a submitted CSV row by row against expected records."""
    rows, err = read_csv(sub / params["file"])
    if err:
        return 0.0, {"error": err}

    expected = gt
    for step in params.get("gt_path", []):
        expected = expected[step]
    tspec = gt
    for step in params.get("spec_path", []):
        tspec = tspec[step]

    key = tspec["key"]
    key_cols = key if isinstance(key, list) else [key]
    columns = tspec["columns"]
    tol = tspec.get("tolerance", {})

    if not rows:
        return 0.0, {"error": "submitted table has no data rows"}
    colmap = index_columns(list(rows[0].keys()))
    missing = [c for c in columns if normalise_key(c) not in colmap]
    if missing:
        return 0.0, {"error": f"table is missing column(s): {missing}",
                     "found": list(rows[0].keys())}

    def cell(row, col):
        return row.get(colmap[normalise_key(col)])

    def norm_key(v):
        try:
            return str(int(float(str(v).strip())))
        except (TypeError, ValueError):
            return str(v).strip()

    got_by_key = {}
    for r in rows:
        k = tuple(norm_key(cell(r, kc)) for kc in key_cols)
        got_by_key.setdefault(k, r)

    key_label = "|".join(key_cols)
    results = [{"field": "<row count>",
                "pass": len(rows) == len(expected),
                "detail": f"{len(rows)} rows, expected {len(expected)}"}]
    for exp_row in expected:
        k = tuple(norm_key(exp_row[kc]) for kc in key_cols)
        klabel = f"{key_label}={'|'.join(k)}"
        got = got_by_key.get(k)
        if got is None:
            results.append({"field": klabel, "pass": False,
                            "detail": "row absent from submission"})
            continue
        for col in columns:
            ev = exp_row.get(col)
            spec = ({"expect": ev, **tol[col]} if col in tol and ev is not None
                    else {"expect": ev, "tol_abs": 0} if ev is not None
                    else {"expect": None})
            ok, detail = compare_scalar(spec, cell(got, col))
            results.append({"field": f"{klabel}.{col}", "pass": ok, "detail": detail})
    return summarise(results)


@register("protected_inputs_hash")
def protected_inputs_hash(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """The shipped data must come back byte-identical."""
    expected = gt["protected_inputs"]
    base = sub / params.get("dir", "inputs")
    results = []
    for rel, want in expected.items():
        # Collected inputs are flat. Basename is the historical layout; when a
        # task ships several series whose files share names (1.dcm ...), the
        # harness pulls them as <path minus dataset dir, '/' -> '_'> instead.
        flat = "_".join(Path(rel).parts[1:])
        p = base / Path(rel).name
        if not p.exists() and (base / flat).exists():
            p = base / flat
        if not p.exists():
            results.append({"field": rel, "pass": False,
                            "detail": "input file was not collected"})
            continue
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        results.append({"field": rel, "pass": got == want,
                        "detail": "unchanged" if got == want
                        else f"MODIFIED (sha {got[:12]} != {want[:12]})"})
    return summarise(results)


_MAGIC = {"pdf": b"%PDF-", "png": b"\x89PNG\r\n\x1a\n"}


@register("artifact")
def artifact(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """A required deliverable exists, is the right kind of file, and is not a stub."""
    results = []
    for item in params["files"]:
        p = sub / item["path"]
        name = item["path"]
        if not p.exists():
            results.append({"field": name, "pass": False, "detail": "missing"})
            continue
        size = p.stat().st_size
        if size < item.get("min_bytes", 1):
            results.append({"field": name, "pass": False, "detail": f"only {size} bytes"})
            continue
        kind = item.get("kind")
        if kind in _MAGIC:
            head = p.read_bytes()[:8]
            if not head.startswith(_MAGIC[kind]):
                results.append({"field": name, "pass": False,
                                "detail": f"not a {kind.upper()} file"})
                continue
        if kind == "text":
            try:
                txt = p.read_text(errors="strict")
            except UnicodeDecodeError:
                results.append({"field": name, "pass": False,
                                "detail": "not decodable as text"})
                continue
            need = item.get("must_match", [])
            bad = [rx for rx in need if not re.search(rx, txt, re.I | re.S)]
            if bad:
                results.append({"field": name, "pass": False,
                                "detail": f"content pattern(s) not found: {bad}"})
                continue
        results.append({"field": name, "pass": True, "detail": f"{size:,} bytes"})
    return summarise(results)
