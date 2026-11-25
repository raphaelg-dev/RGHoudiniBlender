"""The Display flag of a Houdini modifier, like Houdini's: the viewport shows the mesh at the
output of this HDA, and every modifier below it is hidden from the viewport while the flag is
set, then restored exactly as it was. One flag per object. Renders are not affected.

The flag is a cut of core/stack_cuts.py (named CUT) anchored on the flagged modifier, so the
flag and what it hides survive undo, redo, duplication and saving.

The stack is settled again whenever it may have changed under the flag: after a depsgraph
update of a flagged object (from a timer, outside the evaluation), an undo, a redo and a file
load. A modifier added or moved below the flagged one is cut, one moved above it is restored,
and the flag is lifted when its modifier is gone or no longer a Houdini modifier. A cut
modifier the user turns back on (or bypasses) also lifts the flag, since Houdini cannot run a
node below its display flag; that modifier stays as the user set it.

Cut Houdini modifiers are off in the viewport only, so they still cook for renders.
"""

import bpy

from ..core import feedback, stack_cuts, targets
from ..core.modifiers import ModifierOperator

CUT = "display"  # the name of the flag's cut of the stack (core/stack_cuts.py)

_pending = set()  # names of the objects to settle at the next timer


def cut_by(obj, modifier):
    """The modifiers the Display flag of `modifier` hides: every modifier below it, whatever
    its type."""
    return stack_cuts.below(obj, modifier)


def _is_houdini(modifier):
    return targets.houdini_tree(modifier) is not None


def flagged(obj):
    """The modifier of `obj` with the Display flag, or None."""
    return stack_cuts.anchor(obj, CUT) if obj is not None else None


def is_flagged(modifier):
    return modifier is not None and flagged(modifier.id_data) == modifier


def is_cut(modifier):
    """Whether a Display flag above this modifier hides it."""
    return CUT in stack_cuts.held_by(modifier)


def set_flag(obj, modifier):
    """Move the Display flag of `obj` to `modifier`, or lift it with None."""
    current = flagged(obj)
    if current is not None and current == modifier:
        return
    if stack_cuts.has(obj, CUT):
        stack_cuts.release(obj, CUT)
    if modifier is not None:
        stack_cuts.hold(obj, CUT, cut_by(obj, modifier), anchor=modifier)
    feedback.redraw()


def settle(obj):
    """Fit the flag's cut to the stack as it is now, or lift the flag (see the module docstring)."""
    if not stack_cuts.has(obj, CUT):
        return
    modifier = flagged(obj)
    if not _is_houdini(modifier) or stack_cuts.changed_by_user(obj, CUT):
        stack_cuts.release(obj, CUT)
        feedback.redraw()
        return
    stack_cuts.settle(obj, CUT, cut_by(obj, modifier))


def settle_all():
    for obj in stack_cuts.objects_with(bpy.data.objects, CUT):
        settle(obj)


def lift_all():
    """Lift every Display flag of the file."""
    for obj in stack_cuts.objects_with(bpy.data.objects, CUT):
        stack_cuts.release(obj, CUT)


class HOUDINI_BRIDGE_OT_display(ModifierOperator, bpy.types.Operator):
    """Show the mesh at the output of this HDA in the viewport, as Houdini's display flag: the
    modifiers below it are cut from the viewport until the flag is lifted (renders are not
    touched). One flag per object"""

    bl_idname = "houdini_bridge.display"
    bl_label = "Display Flag"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = self.node_group()
        users = targets.modifier_users(tree) if tree else []
        if not users:
            return {"CANCELLED"}
        obj, modifier = users[0]
        set_flag(obj, None if is_flagged(modifier) else modifier)
        return {"FINISHED"}


# --- settling whenever the stack may have changed -------------------------------------


def _settle_pending():
    """Timer: settle the objects a depsgraph update named, outside the evaluation."""
    names = list(_pending)
    _pending.clear()
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            settle(obj)
    return None


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph):
    for update in depsgraph.updates:
        obj = update.id.original if isinstance(update.id, bpy.types.Object) else None
        if obj is not None and getattr(obj, stack_cuts.PROPERTY, "") and stack_cuts.has(obj, CUT):
            _pending.add(obj.name)
    if _pending and not bpy.app.timers.is_registered(_settle_pending):
        bpy.app.timers.register(_settle_pending, first_interval=0.0)


@bpy.app.handlers.persistent
def _on_data_replaced(*_):
    """An undo, a redo or a file load: the flags and their cuts come back with the data."""
    _pending.clear()
    settle_all()


HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph_update),
    ("undo_post", _on_data_replaced),
    ("redo_post", _on_data_replaced),
    ("load_post", _on_data_replaced),
)


def register():
    bpy.utils.register_class(HOUDINI_BRIDGE_OT_display)
    for name, function in HANDLERS:
        getattr(bpy.app.handlers, name).append(function)


def unregister():
    """Lift every flag, so that no stack stays cut without the extension."""
    for name, function in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    if bpy.app.timers.is_registered(_settle_pending):
        bpy.app.timers.unregister(_settle_pending)
    _pending.clear()
    if isinstance(bpy.data, bpy.types.BlendData):  # not restricted when an extension is disabled
        lift_all()
    bpy.utils.unregister_class(HOUDINI_BRIDGE_OT_display)
