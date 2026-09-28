# Local changes to the vendored OSWorld guest server

- `run_server.py` (new): starts `main.app` with `debug=False, threaded=True` on 0.0.0.0:5000.
  Upstream's `main.py` runs `app.run(debug=True)`, whose reloader forks a second process.
- `main.py` — `_get_libreoffice_version()` (image r2, 2026-09-11): wrapped in try/except and returns `()`
  when `libreoffice` is absent or its output is unparsable. Upstream did `stdout.split()[1]` unguarded, so on
  this guest (no LibreOffice) `GET /accessibility` raised IndexError -> HTTP 500. `()` compares lower than
  any real version tuple, so the `libreoffice_version_tuple < (7, 4)` check in `_create_atspi_node` still
  works. No other change; the `/accessibility` XML is otherwise upstream behaviour.
- `pyxcursor.py`, `requirements.txt`: unchanged from upstream (2026-09-11, branch main).
  Windows/macOS branches are dead code inside the container; `pywinauto`/`pygame` are not installed.
- `main.py` — `/execute` and `/execute_with_verification` (image r3, 2026-09-12): added
  `stdin=subprocess.DEVNULL` to both `subprocess.run()` calls. Upstream lets the child inherit the Flask
  server's stdin, which is a supervisord pipe that never signals EOF, so every command that reads stdin
  (`gnuplot -e 'set term'` -> REPL + "Press return for more:" pager, bare `python3`, `cat`, `sort`, `bc`,
  `less`, ...) hung for the full 120 s timeout and the endpoint returned HTTP 500 -- indistinguishable from
  "the tool is broken" for a screenshot/JSON-driven agent. The HTTP API has no way to supply stdin, so
  nothing can regress; commands that need input still use here-docs inside the command string.
