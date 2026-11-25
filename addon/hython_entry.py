"""Entry point of the extension in hython: the add-on runs `hython hython_entry.py`.

The package's __init__.py imports bpy, so hython never runs it: this script mounts the
extension's folder as a package without it, then starts houdini/server.py, whose imports
are relative. Nothing of the extension is importable by a bare name in hython, where the
Python of the HDAs runs too.

This file stays readable by an older Python than the server needs, so that an older
Houdini gets one line saying why, not a traceback.
"""

import importlib
import os
import sys
import types

PACKAGE = "rg_houdini_bridge"
FOLDER = os.path.dirname(os.path.abspath(__file__))
MINIMUM_PYTHON = (3, 10)  # what the server's code needs: Houdini 20.0 and later


def python_refusal(version=sys.version_info):
    """The line to exit with when hython's Python is older than MINIMUM_PYTHON, else None."""
    if tuple(version[:2]) >= MINIMUM_PYTHON:
        return None
    return "RG Houdini Bridge needs Houdini 20.0 or later: this hython runs Python %d.%d, older than %d.%d" % (
        tuple(version[:2]) + MINIMUM_PYTHON)


def mount(folder=FOLDER):
    """Take `folder` off sys.path (hython puts the script's folder there) and declare it
    as the package PACKAGE, without running its __init__.py."""
    target = os.path.normcase(os.path.abspath(folder))
    sys.path[:] = [entry for entry in sys.path if os.path.normcase(os.path.abspath(entry or os.curdir)) != target]
    package = types.ModuleType(PACKAGE)
    package.__path__ = [folder]
    sys.modules[PACKAGE] = package
    return package


if __name__ == "__main__":
    refusal = python_refusal()
    if refusal is not None:
        print(refusal)
        sys.exit(1)
    mount()
    importlib.import_module(PACKAGE + ".houdini.server").main()
