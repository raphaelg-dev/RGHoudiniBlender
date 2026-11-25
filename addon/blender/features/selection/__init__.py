"""Selecting a group in the viewport, like Houdini's arrow next to a group field: the
elements of the input are selected in Edit Mode, and Enter writes them into the field as a
Houdini group string.

- group_string.py: reading and writing group strings (numbers, ranges, edge paths);
- session.py: entering a selection, reading what is selected, confirming it;
- restore.py: recording what a selection changes (also on the scene), and restoring it;
- mode.py: the modal operator, the border and badge over the 3D view, the handlers.
"""

from . import mode

draw_arrow = mode.draw_arrow


# Only mode.py registers something: group_string.py, session.py and restore.py hold no class or handler.
def register():
    mode.register()


def unregister():
    mode.unregister()
