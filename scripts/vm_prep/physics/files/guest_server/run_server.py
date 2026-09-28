"""Start the vendored OSWorld guest server without Flask's debug reloader (which double-starts)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402  (module-level: platform detection, Flask app, routes)
if __name__ == "__main__":
    main.app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
