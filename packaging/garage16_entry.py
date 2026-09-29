"""PyInstaller entry point for the friend-facing Garage16 client (V0.8.6).

A tiny shim rather than pointing PyInstaller at client/main.py directly:
`client/main.py` uses package-relative imports (`from client.config import
...`) and is normally started as `python -m client.main`; a frozen
executable is started as a plain script, so this file gives it a
top-level `__main__` that imports the package the normal way.
"""

from client.main import main

if __name__ == "__main__":
    main()
