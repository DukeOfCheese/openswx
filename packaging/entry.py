"""PyInstaller entry point for openswx.exe / openswx-cli.exe."""
import multiprocessing
import sys

from openswx.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
