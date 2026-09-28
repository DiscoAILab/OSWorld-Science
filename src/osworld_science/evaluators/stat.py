"""Statistics-domain evaluators (ported from stat_osworld/evaluators/checks.py):
r_syntax, sas_or_r_program, r_function_probe, sql_query_replay, shiny_app_check.
`Rscript` comes from the evaluation context (OSCI_RSCRIPT)."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from .core import compare_scalar, index_columns, normalise_key, summarise
from .registry import EvalContext, register


@register("r_syntax")
def r_syntax(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Require a submitted R program to parse successfully."""
    p = sub / params["file"]
    if not p.exists():
        return 0.0, {"error": f"missing file: {params['file']}"}
    try:
        r = subprocess.run([ctx.rscript, "-e", "parse(commandArgs(TRUE)[1])", str(p.resolve())],
                           capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 0.0, {"error": f"could not parse R program: {e}"}
    return (1.0, {"parsed": True}) if r.returncode == 0 else (
        0.0, {"error": "R syntax error", "stderr": r.stderr[-800:]})


@register("sas_or_r_program")
def sas_or_r_program(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Require substantive analysis source written in SAS or R, not Python."""
    p = sub / params["file"]
    if not p.exists():
        return 0.0, {"error": f"missing file: {params['file']}"}
    try:
        txt = p.read_text(errors="strict")
    except UnicodeDecodeError:
        return 0.0, {"error": "program is not decodable as text"}
    if len(txt.encode()) < params.get("min_bytes", 1):
        return 0.0, {"error": "program is too short"}

    r_markers = [r"\bglm\s*\(", r"family\s*=\s*binomial", r"\bread\.csv\s*\("]
    sas_markers = [r"\bproc\s+logistic\b", r"\bdata\s+[A-Za-z_]", r"\brun\s*;"]
    is_r = all(re.search(rx, txt, re.I) for rx in r_markers)
    is_sas = all(re.search(rx, txt, re.I) for rx in sas_markers)
    forbidden = [rx for rx in (r"\bstatsmodels\b", r"\bsklearn\b", r"\bpandas\b")
                 if re.search(rx, txt, re.I)]
    required = params.get("must_match", [])
    missing = [rx for rx in required if not re.search(rx, txt, re.I | re.S)]
    passed = (is_r or is_sas) and not forbidden and not missing
    language = "R" if is_r else "SAS" if is_sas else "unknown"
    return passed, {"language": language, "forbidden": forbidden,
                    "missing_patterns": missing}


_PROBE_R = r"""
# Load the submitted file into its own environment, take the function out of
# it, and call it with argument sets the submission has never seen.
argv <- commandArgs(trailingOnly = TRUE)
env <- new.env()
sys.source(argv[1], envir = env)
if (!exists("%FN%", envir = env, inherits = FALSE))
  stop("the submitted file does not define %FN%")
fn <- get("%FN%", envir = env)
if (!is.function(fn)) stop("%FN% is not a function")

source(argv[2], local = TRUE)          # defines `spec`

pick <- function(v, name) {
  v <- as.list(v)
  if (!name %in% names(v)) stop(paste("result has no member", name))
  out <- as.numeric(v[[name]])
  if (length(out) != 1L || !is.finite(out))
    stop(paste("result member is not one finite number:", name))
  out
}
parts <- vapply(names(spec), function(nm) {
  v <- do.call(fn, spec[[nm]])
  values <- vapply(fields, function(field) {
    sprintf('"%s":%.17g', field, pick(v, field))
  }, "")
  sprintf('"%s":{%s}', nm, paste(values, collapse = ","))
}, "")
cat("<<RESULT>>", paste0("{", paste(parts, collapse = ","), "}"), sep = "")
"""


@register("r_function_probe")
def r_function_probe(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Call the SUBMITTED function with parameter sets it has never seen."""
    src = sub / params["file"]
    if not src.exists():
        return 0.0, {"error": f"missing file: {params['file']}"}
    calls = gt[params.get("gt_block", "held_out_calls")]
    fields = params.get("fields", ["average_bias", "empirical_type_I_error", "SSE"])
    if not (isinstance(fields, list) and fields
            and all(isinstance(f, str) and f for f in fields)):
        return 0.0, {"error": "probe fields must be a non-empty string list"}

    def _arg(v):
        # a repo-relative data path in the held-out call (e.g. a hidden study under
        # reference_private/) becomes an absolute host path for the R side
        if isinstance(v, str) and v.startswith(("reference_private/", "assets/")):
            resolved = ctx.resolve(v)
            if resolved.exists():
                return str(resolved)
        return v

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "spec.R").write_text(
            "fields <- c(" + ", ".join(json.dumps(f) for f in fields)
            + ")\n\nspec <- list(\n" + ",\n".join(
                f"  {nm} = list(" + ", ".join(
                    f"{k} = {_arg(v)!r}" if isinstance(v, str) else f"{k} = {v}"
                    for k, v in rec["_args"].items()) + ")"
                for nm, rec in calls.items()) + "\n)\n")
        runner = td / "probe.R"
        runner.write_text(_PROBE_R.replace("%FN%", params.get("function", "slope")))
        try:
            r = subprocess.run(
                [ctx.rscript, str(runner), str(src.resolve()), str(td / "spec.R")],
                cwd=td, capture_output=True, text=True,
                timeout=params.get("timeout", 1800))
        except subprocess.TimeoutExpired:
            return 0.0, {"error": "submitted function did not finish in time"}
        except FileNotFoundError:
            return 0.0, {"error": f"Rscript not available at {ctx.rscript}"}
        if r.returncode != 0:
            return 0.0, {"error": "submitted R file failed to run",
                         "stderr": r.stderr[-1500:]}
        if "<<RESULT>>" not in r.stdout:
            return 0.0, {"error": "probe produced no result", "stdout": r.stdout[-500:]}
        try:
            got = json.loads(r.stdout.split("<<RESULT>>", 1)[1].strip())
        except json.JSONDecodeError as e:
            return 0.0, {"error": f"probe output unparsable: {e}"}

    results = []
    for nm, rec in calls.items():
        g = got.get(nm, {})
        for field in fields:
            ok, detail = compare_scalar(rec[field], g.get(field))
            results.append({"field": f"{nm}.{field}", "pass": ok, "detail": detail})
    return summarise(results)


def _load_sqlite(con: sqlite3.Connection, tables: dict[str, Path]) -> None:
    for name, path in tables.items():
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rdr = csv.reader(fh)
            header = next(rdr)
            # NUMERIC affinity so numeric-looking text is stored as numbers
            # and bare literals compare the way they do in PROC SQL.
            cols = ", ".join(f'"{h.strip()}" NUMERIC' for h in header)
            con.execute(f'CREATE TABLE "{name}" ({cols})')
            con.executemany(
                f'INSERT INTO "{name}" VALUES ({",".join("?" * len(header))})',
                ([None if c.strip() == "" else c for c in row] for row in rdr))
    con.commit()


@register("sql_query_replay")
def sql_query_replay(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Execute the submitted SELECT against BOTH shipped datasets."""
    qf = sub / params["file"]
    if not qf.exists():
        return 0.0, {"error": f"missing file: {params['file']}"}
    sql = qf.read_text()

    stripped = re.sub(r"--[^\n]*", " ", sql)
    stripped = re.sub(r"/\*.*?\*/", " ", stripped, flags=re.S)
    if not re.search(r"\bselect\b", stripped, re.I):
        return 0.0, {"error": "file contains no SELECT statement"}
    if re.search(r"\b(proc\s+sql|quit\s*;|run\s*;|libname|%macro)\b", stripped, re.I):
        return 0.0, {"error": "file must hold a portable SELECT only — "
                              "no vendor procedure wrapper"}
    if len([s for s in stripped.split(";") if s.strip()]) != 1:
        return 0.0, {"error": "file must hold exactly one statement"}

    data = ctx.resolve(params["data_dir"])
    tspec = gt["table_spec"]
    key, columns, tol = tspec["key"], tspec["columns"], tspec.get("tolerance", {})

    results = []
    for tag in params["datasets"]:
        tables = {name: data / tmpl.format(tag=tag) for name, tmpl in params["tables"].items()}
        con = sqlite3.connect(":memory:")
        try:
            _load_sqlite(con, tables)
            cur = con.execute(stripped.rstrip().rstrip(";"))
            names = [c[0] for c in cur.description]
            rows = [dict(zip(names, r, strict=False)) for r in cur.fetchall()]
        except sqlite3.Error as e:
            return 0.0, {"error": f"query failed on dataset {tag}: {e}"}
        finally:
            con.close()

        expected = gt["datasets"][tag]["table"]
        colmap = index_columns(names)
        missing = [c for c in columns if normalise_key(c) not in colmap]
        if missing:
            results.append({"field": f"{tag}:columns", "pass": False,
                            "detail": f"result is missing {missing}; got {names}"})
            continue
        by_key = {}
        for r in rows:
            k = r.get(colmap[normalise_key(key)])
            try:
                k = str(int(float(str(k).strip())))
            except (TypeError, ValueError):
                k = str(k).strip()
            by_key.setdefault(k, r)
        results.append({"field": f"{tag}:row count",
                        "pass": len(rows) == len(expected),
                        "detail": f"{len(rows)} rows, expected {len(expected)}"})
        for exp_row in expected:
            k = str(int(exp_row[key]))
            got = by_key.get(k)
            if got is None:
                results.append({"field": f"{tag}:{key}={k}", "pass": False,
                                "detail": "row absent from query result"})
                continue
            for col in columns:
                ev = exp_row.get(col)
                spec = ({"expect": ev, **tol[col]} if col in tol and ev is not None
                        else {"expect": ev, "tol_abs": 0} if ev is not None
                        else {"expect": None})
                ok, detail = compare_scalar(spec, got.get(colmap[normalise_key(col)]))
                results.append({"field": f"{tag}:{key}={k}.{col}", "pass": ok, "detail": detail})
    return summarise(results)


@register("shiny_app_check")
def shiny_app_check(sub: Path, params: dict, gt: dict, ctx: EvalContext):
    """Structural check on the submitted app: source patterns plus an R parse."""
    p = sub / params["file"]
    if not p.exists():
        return 0.0, {"error": f"missing file: {params['file']}"}
    src = p.read_text(errors="replace")

    results = []
    for label, rx in params["must_contain"].items():
        results.append({"field": f"contains:{label}",
                        "pass": bool(re.search(rx, src, re.I | re.S)),
                        "detail": rx})

    with tempfile.TemporaryDirectory() as td:
        chk = Path(td) / "parse.R"
        chk.write_text("invisible(parse(commandArgs(trailingOnly=TRUE)[1]))\n")
        try:
            r = subprocess.run([ctx.rscript, str(chk), str(p.resolve())],
                               capture_output=True, text=True, timeout=120)
            results.append({"field": "parses as R", "pass": r.returncode == 0,
                            "detail": r.stderr[-400:] if r.returncode else "ok"})
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            results.append({"field": "parses as R", "pass": False,
                            "detail": f"could not run R: {e}"})
    return summarise(results)
