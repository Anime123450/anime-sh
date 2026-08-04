"""Entry point for the standalone (PyInstaller) build.

`anime_sh/__main__.py` uses a relative import, which PyInstaller cannot run as a
top-level script — it fails with "attempted relative import with no known parent
package". This launcher imports the same entry point absolutely.
"""

from anime_sh.cli.main import main

if __name__ == "__main__":
    main()
