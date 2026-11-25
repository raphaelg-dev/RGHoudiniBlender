"""The extension's logger, and the redraw of the areas showing its state.

`log` is the logger named after the package (paths.PACKAGE), its messages prefixed with
the extension's name, and nothing configures it: with no handler set, warnings and errors
reach the system console. redraw() is needed because nothing redraws a setting stored on
a node group, or a change made from a timer.
"""

import logging

import bpy

from . import paths


class _Prefixed(logging.LoggerAdapter):
    """The console shows the message alone: the prefix tells whose it is."""

    def process(self, msg, kwargs):
        return "RG Houdini Bridge: " + msg, kwargs


log = _Prefixed(logging.getLogger(paths.PACKAGE), {})

VIEWPORTS = ("VIEW_3D",)


def redraw(area_types=VIEWPORTS):
    """Redraw every area of these types, in every window."""
    window_manager = bpy.context.window_manager
    if window_manager is None:  # background mode
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type in area_types:
                area.tag_redraw()
