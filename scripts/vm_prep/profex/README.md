# Profex (materials domain)

`ubuntu_profex` extends the OSWorld Ubuntu 22.04 base with the official Profex
5.7.2 Linux bundle, BGMN 4.2.23d and its bundled structure database. The COD
database is not installed. No task inputs or answers are baked into the image.

## Build and verify

```bash
scripts/vm_prep/base/download_base.sh
bash scripts/vm_prep/profex/build_image.sh 5180
uv run osci vm reset --snapshot ubuntu_profex
uv run osci vm check --snapshot ubuntu_profex
```

Set `PROFEX_TARBALL=/path/to/Profex-5.7.2-linux-x86_64-bin.tar.gz` to reuse a
downloaded archive. The installer pins 5.7.2 (an empty local package repository
stops it from upgrading itself). The vendor binaries need newer glibc/GCC
runtimes than the base guest: `prepare_runtime.sh` installs them under
`/opt/Profex5/compat` and points only the Profex binaries at them.

## Tasks and grading

```text
data/materials/tasks/<id>.json
data/materials/public/assets/<id>.tgz
data/materials/private/reference_private/<id>/gt.json
data/materials/private/grader_tests/run_tests.py
```

`xrd_phase_set` scores `{"phases": ["formula", ...]}` 1 if it equals the
`gt.phases` set and 0 otherwise, comparing reduced elemental formulas (order,
spacing, Unicode subscripts and parentheses are free; element case matters).
Precision and recall are reported for analysis. Pair it with the
`protected_inputs_hash` gate.

```bash
uv run osci tasks validate
uv run pytest tests/unit/test_xrd_evaluator.py -q
uv run pytest tests/grader -m data -k materials -q
```
