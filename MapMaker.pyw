"""Entry point for the packaged program (PyInstaller) and for double-clicking with pythonw."""
import sys

from mapmaker.gui.app import run

if __name__ == "__main__":
    sys.exit(run(sys.argv))
