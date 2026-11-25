"""Recording what a viewport group selection changes, and restoring it.

When a selection starts, a Record names everything it is about to change: the mode, the
active and selected objects, the selection mode, the modifiers it hides (a cut of
core/stack_cuts.py on their object) and pauses, the objects it hides or shows, the
temporary object. revert() restores all of it, a failed step never stopping the next.

The record holds names only, never Blender structs (an undo or a file load frees them),
and is also stored on the scene while the selection runs (MARKER, JSON). Blender keeps a
copy of the data before the first Edit Mode step it records, so an undo, a redo, an
autosave or a file recovered after a crash can bring back the data of a selection that
has ended: the marker comes back with it, and repair() restores what it names.

Removing the temporary copy needs an undo step right after it (push_undo): Blender decodes
an Edit Mode step of the copy on the data it last loaded, and only a newer undo step makes
it load the data holding the copy first. Without it, undoing into those steps crashes
Blender.
"""

import json
from dataclasses import asdict, dataclass, field

import bpy

from ...core import cook, feedback, stack_cuts

MARKER = "houdini_bridge_selection"  # custom property of the scene while a selection runs
CUT = "selection"  # the name of its cut of the stack (core/stack_cuts.py)


@dataclass
class Record:
    """What a selection changed, for revert()."""

    scene: str
    workspace: str = ""  # where the status text is set
    active: str = ""  # the active object, "" for none
    selected: list = field(default_factory=list)  # the selected objects
    mode: str = "OBJECT"  # the mode of the active object
    select_mode: list = field(default_factory=lambda: [True, False, False])  # Edit Mode's vertex, edge, face
    edit: str = ""  # the object in Edit Mode
    temporary: bool = False  # it is a temporary copy, removed at the end
    # The object whose modifiers are cut from the viewport (core/stack_cuts.py, cut CUT), "" for none.
    cut_object: str = ""
    paused: list = field(default_factory=list)  # node groups of the Houdini modifiers paused
    hidden: list = field(default_factory=list)  # objects hidden (hide_set)
    shown: list = field(default_factory=list)  # objects shown (hide_set)
    # Select flags of a mesh edited in place (vertices, edges, faces), not stored on the scene.
    mesh_selection: object = None


def store(record):
    """Write the record on its scene (all but the mesh selection)."""
    scene = bpy.data.scenes.get(record.scene)
    if scene is not None:
        data = asdict(record)
        data.pop("mesh_selection")
        scene[MARKER] = json.dumps(data)


def stored(scene):
    text = scene.get(MARKER)
    if not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
        return Record(**{key: value for key, value in data.items() if key in Record.__dataclass_fields__})
    except (TypeError, ValueError):
        return None


def _try(step, *arguments):
    """Run one restoring step: a failure is logged and the next steps still run."""
    try:
        step(*arguments)
    except Exception:
        feedback.log.exception("group selection, %s failed", step.__name__)


def _leave_edit_mode(context, record):
    edited = bpy.data.objects.get(record.edit) if record.edit else None
    if edited is not None and edited.mode == "EDIT":
        context.view_layer.objects.active = edited
        bpy.ops.object.mode_set(mode="OBJECT")


def _remove_temporary(record):
    edited = bpy.data.objects.get(record.edit) if record.edit else None
    if not record.temporary or edited is None:
        return
    mesh = edited.data
    bpy.data.objects.remove(edited)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _restore_mesh_selection(record):
    """Restore the select flags of a mesh edited in place, when its element counts did not change."""
    edited = bpy.data.objects.get(record.edit) if record.edit else None
    if record.temporary or edited is None or record.mesh_selection is None:
        return
    mesh = edited.data
    elements = (mesh.vertices, mesh.edges, mesh.polygons)
    if tuple(len(e) for e in elements) == tuple(len(flags) for flags in record.mesh_selection):
        for sequence, flags in zip(elements, record.mesh_selection, strict=True):
            sequence.foreach_set("select", flags)
        mesh.update()


def _restore_visibility(record):
    for name, hide in [(name, False) for name in record.hidden] + [(name, True) for name in record.shown]:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.hide_get() != hide:
            obj.hide_set(hide)
    obj = bpy.data.objects.get(record.cut_object) if record.cut_object else None
    if obj is not None:
        stack_cuts.release(obj, CUT)


def _restore_context(context, record):
    scene = bpy.data.scenes.get(record.scene)
    if scene is not None:
        scene.tool_settings.mesh_select_mode = record.select_mode
    view_layer = context.view_layer
    for obj in view_layer.objects:
        selected = obj.name in record.selected
        if obj.select_get() != selected:
            obj.select_set(selected)
    obj = view_layer.objects.get(record.active) if record.active else None
    if obj is not None:
        view_layer.objects.active = obj
        if record.mode != "OBJECT" and obj.mode != record.mode:
            bpy.ops.object.mode_set(mode=record.mode)


def _clear_status(record):
    workspace = bpy.data.workspaces.get(record.workspace) if record.workspace else None
    if workspace is not None:
        workspace.status_text_set(None)


def _resume(record):
    for name in record.paused:
        tree = bpy.data.node_groups.get(name)
        if tree is not None and cook.cooks.is_paused(tree):
            cook.cooks.resume(tree)


def _remove_marker(record):
    scene = bpy.data.scenes.get(record.scene)
    if scene is not None and MARKER in scene:
        del scene[MARKER]


def revert(context, record):
    """Restore everything the record names, in order: leave Edit Mode, remove the temporary
    copy or restore the mesh's own selection, restore visibility, selection, active object,
    mode and status bar, resume the paused modifiers (which cook), and remove the marker."""
    _try(_leave_edit_mode, context, record)
    _try(_remove_temporary, record)
    _try(_restore_mesh_selection, record)
    _try(_restore_visibility, record)
    _try(_restore_context, context, record)
    _try(_clear_status, record)
    _try(_resume, record)
    _try(_remove_marker, record)


def push_undo(message):
    """Push an undo step (none in background mode, which keeps no undo history)."""
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError as error:
        feedback.log.info("no undo step for %s (%s)", message, error)


def repair(context, after_undo=False):
    """Restore what the records left on the scenes name: data of a finished selection brought
    back by an undo, a redo (`after_undo`) or a file. A temporary copy removed after an undo
    or a redo gets its undo step (push_undo). Returns how many records were found."""
    found = 0
    for scene in bpy.data.scenes:
        record = stored(scene)
        if record is not None:
            revert(context, record)
            if after_undo and record.temporary:
                push_undo("Revert Group Selection")
            found += 1
    if found:
        feedback.redraw()
    return found
