"""The arrow of a group field: the modal operator of the viewport selection, its drawing
over the 3D view, and the handlers that end it whatever happens.

The operator drives session.py: modal() passes every event to Blender (selecting,
navigating and editing work as usual) except Enter, which confirms, and Esc, which cancels.
While a session runs, every 3D view gets a Houdini-orange border and a badge naming the
field, the node and the selected elements, and the status bar says the same.

A session ends, and everything is restored, on Enter or Esc, when its object leaves Edit
Mode or another object becomes active (checked at each event and after each undo), before
the file is saved, and when the extension is disabled. A file being loaded replaces the
data the session changed, so the session is dropped. Data of a selection that an undo, a
redo or a file brings back later is restored too (restore.repair).
"""

import bpy
import gpu
from bpy.props import StringProperty
from gpu_extras.batch import batch_for_shader

from ...core import feedback
from ...core.modifiers import ModifierOperator
from ..viewport import overlay
from . import restore, session

BORDER_WIDTH = 3  # pixels, at a UI scale of 1
GLOW_WIDTH = 10
GLOW_ALPHA = 0.18
BADGE_MARGIN = 76  # from the top of the view, below its header and tool header, at a UI scale of 1
CONFIRM_KEYS = {"RET", "NUMPAD_ENTER"}
CANCEL_KEYS = {"ESC"}

_handles = []
_ended_reason = ""  # why a selection ended outside its operator, for the operator to say


class HOUDINI_BRIDGE_OT_select_group(ModifierOperator, bpy.types.Operator):
    """Select the elements of this group field in the viewport, as Houdini's arrow does:
    Enter writes them into the field, Esc cancels"""

    bl_idname = "houdini_bridge.select_group"
    bl_label = "Select Group in Viewport"
    # No UNDO option: session.finish pushes the step itself, once the field is written.

    parm: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        tree = self.node_group()
        if tree is None:
            return {"CANCELLED"}
        try:
            session.begin(context, tree, self.parm)
        except session.SelectionError as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _ended_reason
        if session.active() is None:
            # Ended elsewhere (saved, disabled, a file loaded): said once, at the next event.
            if _ended_reason:
                self.report({"WARNING"}, "Group selection cancelled: %s" % _ended_reason)
                _ended_reason = ""
            return {"CANCELLED"}
        if event.value == "PRESS" and event.type in CONFIRM_KEYS:
            reason = session.is_broken(context)
            text = session.finish(context, confirm=True)
            if text is None:  # the session could not go on: nothing was written
                self.report({"WARNING"}, "Group selection cancelled: %s" % (reason or "nothing was written"))
                return {"CANCELLED"}
            self.report({"INFO"}, "Group set to “%s”" % text if text else "Group emptied")
            return {"FINISHED"}
        if event.value == "PRESS" and event.type in CANCEL_KEYS:
            session.finish(context, confirm=False)
            return {"CANCELLED"}
        reason = session.is_broken(context)
        if reason:
            session.finish(context, confirm=False)
            self.report({"WARNING"}, "Group selection cancelled: %s" % reason)
            return {"CANCELLED"}
        session.set_status(context)  # the elements of the mode selected now
        return {"PASS_THROUGH"}

    def cancel(self, context):
        # Blender ends the operator itself (a window closed, a file loaded).
        session.finish(context, confirm=False)


def draw_arrow(layout, tree, parm):
    """The arrow next to a group field, drawn by the modifier panel when Houdini selects the
    field in its viewport."""
    if parm.group_selection:  # tested as a string, not parsed at each draw
        arrow = layout.operator(HOUDINI_BRIDGE_OT_select_group.bl_idname, text="", icon="RESTRICT_SELECT_OFF")
        arrow.tree = tree.name
        arrow.parm = parm.name


# --- drawing --------------------------------------------------------------------------


def _rectangles(rectangles, color):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    positions, indices = [], []
    for x, y, width, height in rectangles:
        start = len(positions)
        positions += [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
        indices += [(start, start + 1, start + 2), (start, start + 2, start + 3)]
    batch = batch_for_shader(shader, "TRIS", {"pos": positions}, indices=indices)
    shader.uniform_float("color", color)
    batch.draw(shader)


def _border(width, height, thickness):
    """The four bars of a frame along the edges of a region, as (x, y, width, height)."""
    return [(0, 0, width, thickness), (0, height - thickness, width, thickness),
            (0, 0, thickness, height), (width - thickness, 0, thickness, height)]


def draw():
    """POST_PIXEL: the border and the badge of a running selection, in every 3D view."""
    if session.active() is None:
        return
    context = bpy.context
    region = context.region
    if region is None:
        return
    scale = context.preferences.system.ui_scale
    gpu.state.blend_set("ALPHA")
    try:
        _rectangles(_border(region.width, region.height, GLOW_WIDTH * scale), (*overlay.HOUDINI_ORANGE, GLOW_ALPHA))
        _rectangles(_border(region.width, region.height, BORDER_WIDTH * scale), (*overlay.HOUDINI_ORANGE, 0.95))
        overlay.badge(region.width / 2, region.height - BADGE_MARGIN * scale, session.message(context), scale)
    except Exception as error:  # drawing must never break the viewport
        feedback.log.warning("group selection drawing: %s", error)
    gpu.state.blend_set("NONE")


# --- ending it whatever happens -------------------------------------------------------


@bpy.app.handlers.persistent
def _on_undo(*_):
    """An undo or a redo during a selection may go back to before it began: the selection
    ends. After a selection, one may bring back data from while it ran (Blender keeps the
    data before the first Edit Mode step): what it changed is restored again."""
    context = bpy.context
    if session.active() is None:
        restore.repair(context, after_undo=True)
    elif session.is_broken(context):
        session.finish(context, confirm=False)


@bpy.app.handlers.persistent
def _on_save(*_):
    """A selection is never saved: it is cancelled before the file is written (the operator
    reports it at the next event)."""
    global _ended_reason
    if session.active() is not None:
        session.finish(bpy.context, confirm=False)
        _ended_reason = "the file was saved, and nothing of a selection is saved"


@bpy.app.handlers.persistent
def _on_load_pre(*_):
    session.forget()


@bpy.app.handlers.persistent
def _on_load_post(*_):
    """Restore a file saved while a selection ran (an autosave, a recovered session)."""
    restore.repair(bpy.context)


HANDLERS = (
    ("undo_post", _on_undo),
    ("redo_post", _on_undo),
    ("save_pre", _on_save),
    ("load_pre", _on_load_pre),
    ("load_post", _on_load_post),
)
CLASSES = (HOUDINI_BRIDGE_OT_select_group,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for name, function in HANDLERS:
        getattr(bpy.app.handlers, name).append(function)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_PIXEL"))


def unregister():
    """Cancel a running selection first, restoring everything it changed."""
    global _ended_reason
    session.finish(bpy.context, confirm=False)
    _ended_reason = ""
    while _handles:
        bpy.types.SpaceView3D.draw_handler_remove(_handles.pop(), "WINDOW")
    for name, function in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
