"""Whether the open file may cook by itself: Blender's "Auto Run Python Scripts", applied to
the Houdini modifiers.

Opening a file whose Houdini modifiers are not locked starts hython and installs the HDA
files the file names, and an HDA can run Python. So a file Blender would not let run its own
scripts cooks nothing until the user allows it (is_blocked()): with Allow Execution, or with an
action of their own on a Houdini modifier of that file (adding one, Recook, Start, Cache, a
button of the HDA), which calls cook.allow_file(). The permission lasts until another file
is opened. A new file, and the startup file, are the user's own: never blocked.

Blender 5.2 does not tell an add-on whether it let the loaded file run its scripts. auto_run()
reads what it exposes:
- bpy.app.autoexec_fail: Blender blocked a script or a driver of this file;
- the command line: --enable-autoexec (-y) and --disable-autoexec (-Y), the last one given,
  override the preference;
- the preference Auto Run Python Scripts (filepaths.use_scripts_auto_execute) and its
  Excluded Paths (preferences.autoexec_paths), compared with the file's folder as Blender
  compares them (a glob, or the start of the path; without case on Windows).
A file opened as a Trusted Source (the option of File > Open) while the preference is off is
not told apart from another one: it asks too.
"""

import fnmatch
import os
import sys

import bpy

ENABLE_FLAGS = ("-y", "--enable-autoexec")
DISABLE_FLAGS = ("-Y", "--disable-autoexec")


class Trust:
    """blocked: the open file waits for the user's permission to cook, or None while that is
    not worked out yet (main thread only)."""

    def __init__(self):
        self.blocked = None


_trust = Trust()


def forget():
    """The extension is being registered: installed, updated, enabled or reloaded, maybe with
    a file already open, and no load_post will come for it. register() cannot read bpy.data
    (Blender hides it while an add-on is enabled), so the first question works it out."""
    _trust.blocked = None


def is_blocked():
    if _trust.blocked is None:
        file_loaded()
    return _trust.blocked


def _command_line():
    """True or False when the command line turns auto run on or off, else None."""
    found = None
    for argument in sys.argv[1:]:
        if argument == "--":  # what follows is for a script
            break
        if argument in ENABLE_FLAGS:
            found = True
        elif argument in DISABLE_FLAGS:
            found = False
    return found


def _excluded(path_compare, folder):
    pattern = path_compare.path
    if not pattern:
        return False
    if path_compare.use_glob:
        return fnmatch.fnmatch(folder, pattern)  # without case and with either slash on Windows
    return os.path.normcase(folder).startswith(os.path.normcase(pattern))


def auto_run(filepath):
    """Whether Blender runs the scripts of the file at `filepath` by itself, as far as it says."""
    if bpy.app.autoexec_fail:
        return False
    given = _command_line()
    if given is not None:
        return given
    preferences = bpy.context.preferences
    if not preferences.filepaths.use_scripts_auto_execute:
        return False
    folder = os.path.join(os.path.dirname(os.path.abspath(filepath)), "")
    return not any(_excluded(path_compare, folder) for path_compare in preferences.autoexec_paths)


def file_loaded():
    """A file was opened (load_post), or the extension was registered with one open (forget):
    block it when Blender would not run its scripts."""
    path = bpy.data.filepath
    _trust.blocked = bool(path) and not auto_run(path)


def allow():
    """The open file may cook from now on. True when it was blocked."""
    was_blocked = is_blocked()
    _trust.blocked = False
    return was_blocked
