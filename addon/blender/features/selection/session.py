"""A viewport group selection: entering it, reading what is selected, confirming it.

begin() shows the input a group field refers to as the modifier receives it, in Edit Mode,
with what the field holds already selected. finish() writes the selection into the field
as a Houdini group string on confirm; either way, restore.py restores everything begin()
changed, and the paused modifiers cook again.

What is shown:
- The modifier and every modifier below it are hidden from the viewport, and the Houdini
  modifiers among them are paused (cook.pause), so that nothing cooks meanwhile.
- An input that is the object itself with no modifier above it, or another object showing
  no modifier, is edited in place: the numbers match, and editing its geometry edits the
  input, as in Houdini.
- Otherwise Houdini numbers the elements of the evaluated mesh: a temporary object holding
  a copy of it (bpy.data.meshes.new_from_object, as a cook reads it) is shown and edited
  instead, and the source is hidden. Geometry edits on the copy are not kept.

Main thread only; one session at a time.
"""

from dataclasses import dataclass

import bmesh
import bpy
import numpy as np

from ....shared import contract
from ...core import cook, feedback, parameters, stack_cuts, targets
from . import group_string, restore

TEMPORARY_NAME = "Houdini Selection"  # the temporary object and mesh of a copy
SUPPORTED_TYPES = tuple(group_string.MODE_OF_TYPE)  # points, primitives, edges
MODES = ("VERT", "EDGE", "FACE")  # the order of mesh_select_mode
MAX_UNREAD_SHOWN = 24  # characters of the field's unread text shown in the badge


class SelectionError(Exception):
    """The selection cannot start; the message tells the user why."""


@dataclass
class Session:
    """The running selection: the field it writes, and the record of what it changed."""

    record: restore.Record
    tree: str  # the name of the Houdini modifier's node group
    parm: str  # the group field
    label: str  # "Base Group · Group Create", for the badge
    types: tuple  # the group types the field takes (SUPPORTED_TYPES only)
    entry_type: str  # the type it started in
    type_parm: str  # the menu parameter giving the type, or ""
    unread: str = ""  # the field's text when it could not be preselected
    status: str = ""  # the status text set last


_session = None  # the running session, or None


def active():
    return _session


def edited_object():
    return bpy.data.objects.get(_session.record.edit) if _session is not None and _session.record.edit else None


# --- what the field takes -------------------------------------------------------------


def _source(target, selection):
    """(input item, object) whose elements the field refers to: input `input` of the
    selection, or input 0 when the tag says -n and input n is empty."""
    settings = target.settings
    index = abs(selection["input"])
    item = settings.inputs[index] if index < len(settings.inputs) else None
    source = (target.owner if item.is_self else item.object) if item is not None else None
    if source is None and selection["input"] < 0 and len(settings.inputs):
        item = settings.inputs[0]
        source = target.owner if item.is_self else item.object
    if source is None:
        raise SelectionError("Input %d of %s is empty: nothing to select" % (index + 1, settings.label))
    if source.type != "MESH":
        raise SelectionError("%s is not a mesh: only mesh elements can be selected" % source.name)
    return item, source


def _current_type(context):
    """The group type of the select mode, or None when several modes are on."""
    modes = [mode for mode, on in zip(MODES, context.scene.tool_settings.mesh_select_mode, strict=True) if on]
    return group_string.TYPE_OF_MODE[modes[0]] if len(modes) == 1 else None


def _types(context, target, selection, text):
    """(types the field takes, the type it starts in, the name of the menu giving the type or "")."""
    settings, modifier = target.settings, target.modifier
    type_parm = settings.parms.get(selection["type_parm"]) if selection["type_parm"] else None
    current = _current_type(context)
    if type_parm is not None:
        offered = [group_string.type_of_token(token) for token in parameters.menu_tokens(type_parm)]
        types = tuple(dict.fromkeys(t for t in offered if t in SUPPORTED_TYPES))
        entry = group_string.type_of_token(parameters.read_value(modifier, type_parm) or "")
        return types or SUPPORTED_TYPES, entry or current, type_parm.name  # "guess": the current mode
    declared = tuple(selection["types"])
    entry = group_string.initial_type(declared, text, current)
    types = tuple(t for t in declared if t in SUPPORTED_TYPES) or (SUPPORTED_TYPES if not declared else ())
    return types, entry, ""


def _check_numbering(target, item):
    """Refuse to start when Blender's numbers differ from Houdini's. Input 1 chained on the
    Houdini modifier above reaches the server as that modifier's whole Houdini result
    (cook.chained_above), while Blender shows the mesh it received back. When that mesh left
    out, unpacked or converted part of the result (cook.sent.is_renumbered), a selection made here
    would pick other elements."""
    above = cook.chained_above(target) if item.is_self else None
    if above is not None and cook.sent.is_renumbered(above.session_uid):
        label = above.houdini_bridge.label or above.name
        raise SelectionError("Input 1 comes from %s through Houdini, whose elements are numbered differently "
                             "from the mesh Blender shows (instances, curves, volumes or loose points): a "
                             "selection made here would pick the wrong elements" % label)


def _check_type(entry, types, label):
    if entry is not None and entry not in SUPPORTED_TYPES:
        raise SelectionError("%s takes Houdini %s, which Blender cannot select" % (label, entry))
    if not types:
        raise SelectionError("%s takes no element Blender can select" % label)
    return entry if entry in types else types[0]


# --- entering -------------------------------------------------------------------------


def _in_place(target, item, source, context):
    """Whether the source can be edited in place: its own mesh is what the cook sends."""
    if source.library is not None or source.data.library is not None:
        return False
    if source.name not in context.view_layer.objects:
        return False
    if item.is_self:
        return not target.modifiers_above()
    return not any(modifier.show_viewport for modifier in source.modifiers)


def _hide_modifiers_from(record, target):
    """Hide the modifier and every one below it (a cut of core/stack_cuts.py), and pause the
    Houdini ones among them."""
    cut = stack_cuts.below(target.owner, target.modifier, including=True)
    for modifier in cut:
        tree = targets.houdini_tree(modifier)
        if tree is not None and not cook.cooks.is_paused(tree):
            cook.cooks.pause(tree)
            record.paused.append(tree.name)
    stack_cuts.hold(target.owner, restore.CUT, cut, anchor=target.modifier)
    record.cut_object = target.owner.name


def _temporary_copy(context, source):
    """A new object holding the evaluated mesh of `source`, as a cook reads it, at its place."""
    depsgraph = context.evaluated_depsgraph_get()
    depsgraph.update()
    mesh = bpy.data.meshes.new_from_object(source.evaluated_get(depsgraph), depsgraph=depsgraph)
    mesh.name = TEMPORARY_NAME
    copy = bpy.data.objects.new(TEMPORARY_NAME, mesh)
    copy.hide_render = True  # a render made meanwhile shows the source only
    context.scene.collection.objects.link(copy)
    copy.matrix_world = source.matrix_world.copy()
    # A 3D view in local view shows only its own objects: the copy joins those that show the source.
    for screen in (window.screen for window in context.window_manager.windows):
        for area in screen.areas:
            space = area.spaces.active if area.type == "VIEW_3D" else None
            if space is not None and space.local_view is not None and source.local_view_get(space):
                copy.local_view_set(space, True)
    return copy


def _mesh_selection(mesh):
    flags = []
    for elements in (mesh.vertices, mesh.edges, mesh.polygons):
        values = np.empty(len(elements), bool)
        elements.foreach_get("select", values)
        flags.append(values)
    return tuple(flags)


def _preselect(mesh, group_type, elements):
    """Select these elements in Edit Mode, and nothing else."""
    edit = bmesh.from_edit_mesh(mesh)
    for sequence in (edit.verts, edit.edges, edit.faces):
        for element in sequence:
            element.select_set(False)
    edit.verts.ensure_lookup_table()
    edit.faces.ensure_lookup_table()
    if group_type == contract.GROUP_POINTS:
        for number in elements:
            if number < len(edit.verts):
                edit.verts[number].select_set(True)
    elif group_type == contract.GROUP_PRIMITIVES:
        for number in elements:
            if number < len(edit.faces):
                edit.faces[number].select_set(True)
    else:
        edit.verts.index_update()
        wanted = set(elements)
        for edge in edit.edges:
            a, b = (vertex.index for vertex in edge.verts)
            if (min(a, b), max(a, b)) in wanted:
                edge.select_set(True)
    edit.select_flush_mode()
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)


def _set_select_mode(context, group_type):
    mode = group_string.MODE_OF_TYPE[group_type]
    context.scene.tool_settings.mesh_select_mode = tuple(name == mode for name in MODES)


def _record(context):
    """The context as it is now, for restoring it at the end."""
    view_layer = context.view_layer
    obj = view_layer.objects.active
    return restore.Record(
        scene=context.scene.name,
        workspace=context.workspace.name if context.workspace is not None else "",
        active=obj.name if obj is not None else "",
        selected=[o.name for o in view_layer.objects if o.select_get()],
        mode=obj.mode if obj is not None else "OBJECT",
        select_mode=list(context.scene.tool_settings.mesh_select_mode),
    )


def _make_active(context, obj):
    view_layer = context.view_layer
    for other in view_layer.objects:
        if other.select_get():
            other.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj


def begin(context, tree, parm_name):
    """Enter the viewport selection of a group field. Raises SelectionError, leaving the
    state as it was, when it cannot start (another selection runs, the field cannot be
    selected, its input is empty or not a mesh)."""
    global _session
    if _session is not None:
        raise SelectionError("A group selection is already running: confirm or cancel it first")
    target = targets.Target(tree)
    settings = tree.houdini_bridge
    parm = settings.parms.get(parm_name)
    selection = parameters.group_selection(parm) if parm is not None else None
    if selection is None or target.owner is None or target.modifier is None:
        raise SelectionError("This field cannot be selected in the viewport")
    if cook.cooks.caching:
        raise SelectionError("A cache is being made: wait for it to end, or cancel it, before selecting a group")
    item, source = _source(target, selection)
    _check_numbering(target, item)
    text = parameters.read_value(target.modifier, parm) or ""
    types, entry, type_parm = _types(context, target, selection, text)
    entry = _check_type(entry, types, parm.label)

    session = Session(record=_record(context), tree=tree.name, parm=parm.name,
                      label="%s · %s" % (parm.label, settings.label or settings.node_type),
                      types=types, entry_type=entry, type_parm=type_parm)
    _session = session
    try:
        _enter(context, session, target, item, source, text)
    except Exception:
        finish(context, confirm=False)
        raise
    return session


def _enter(context, session, target, item, source, text):
    record = session.record
    active = context.view_layer.objects.active
    if active is not None and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    in_place = _in_place(target, item, source, context)
    _hide_modifiers_from(record, target)
    if in_place and source.hide_get():
        source.hide_set(False)
        record.shown.append(source.name)
    if in_place and not source.visible_get():
        in_place = False  # hidden by its collection or its viewport toggle: a copy is shown instead
    if in_place:
        edited = source
        record.mesh_selection = _mesh_selection(source.data)
    else:
        edited = _temporary_copy(context, source)
        record.temporary = True
        if source.visible_get():
            source.hide_set(True)
            record.hidden.append(source.name)
    record.edit = edited.name
    restore.store(record)  # from here on, data Blender keeps for undo holds the record
    _make_active(context, edited)
    bpy.ops.object.mode_set(mode="EDIT")
    _set_select_mode(context, session.entry_type)
    elements = group_string.read_selection(text, session.entry_type)
    session.unread = text.strip() if elements is None else ""
    _preselect(edited.data, session.entry_type, elements or [])
    set_status(context)
    feedback.redraw()


# --- what is selected -----------------------------------------------------------------


def current_type(context):
    """The group type a confirm would write: the select mode when the field takes it, else the
    type the selection started in."""
    found = _current_type(context)
    return found if found in _session.types else _session.entry_type


def message(context):
    """The text of the badge and of the status bar."""
    word = group_string.ELEMENT_WORDS[group_string.MODE_OF_TYPE[current_type(context)]]
    parts = ["Selecting %s" % _session.label, word]
    if _session.record.temporary:
        parts.append("geometry edits are not kept")
    if _session.unread:
        shown = _session.unread
        if len(shown) > MAX_UNREAD_SHOWN:
            shown = shown[:MAX_UNREAD_SHOWN - 1] + "…"
        parts.append("“%s” is kept until Enter" % shown)
    parts += ["Enter to confirm", "Esc to cancel"]
    return " · ".join(parts)


def set_status(context):
    """Show the badge's text in the status bar (written only when it changes)."""
    workspace = bpy.data.workspaces.get(_session.record.workspace) if _session is not None else None
    text = message(context) if workspace is not None else ""
    if workspace is not None and text != _session.status:
        workspace.status_text_set(text)
        _session.status = text


def selected_elements(context, group_type):
    """The elements selected in Edit Mode: point or primitive numbers, or (low, high) point
    pairs for edges."""
    edit = bmesh.from_edit_mesh(edited_object().data)
    if group_type == contract.GROUP_POINTS:
        return [number for number, vertex in enumerate(edit.verts) if vertex.select]
    if group_type == contract.GROUP_PRIMITIVES:
        return [number for number, face in enumerate(edit.faces) if face.select]
    edit.verts.index_update()
    pairs = []
    for edge in edit.edges:
        if edge.select:
            a, b = (vertex.index for vertex in edge.verts)
            pairs.append((min(a, b), max(a, b)))
    return pairs


def is_broken(context):
    """Why the session cannot go on (its object left Edit Mode or is gone, another object
    became active, the modifier is gone, an undo went back to before it), or ""."""
    obj = edited_object()
    if obj is None:
        return "the edited object is gone"
    if obj.mode != "EDIT":
        return "Edit Mode was left"
    if context.view_layer.objects.active != obj:
        return "another object became active"
    scene = bpy.data.scenes.get(_session.record.scene)
    if scene is None or restore.stored(scene) is None:
        return "the data went back to before the selection"
    # Checked at each event: only the object whose stack was cut is searched.
    tree = bpy.data.node_groups.get(_session.tree)
    owner = bpy.data.objects.get(_session.record.cut_object) if _session.record.cut_object else None
    if tree is None or owner is None or not any(modifier.type == "NODES" and modifier.node_group == tree
                                                for modifier in owner.modifiers):
        return "the Houdini modifier is gone"
    return ""


# --- leaving --------------------------------------------------------------------------


def _write(session, text, group_type):
    """Write the selection into the field, and its type into the menu giving the type."""
    tree = bpy.data.node_groups.get(session.tree)
    target = targets.Target(tree) if tree is not None else None
    if target is None or target.modifier is None:
        return
    settings = tree.houdini_bridge
    parm = settings.parms.get(session.parm)
    if parm is not None:
        parameters.write_value(target.modifier, parm, text)
    type_parm = settings.parms.get(session.type_parm) if session.type_parm else None
    if type_parm is not None:
        current = group_string.type_of_token(parameters.read_value(target.modifier, type_parm) or "")
        token = group_string.token_of_type(group_type, parameters.menu_tokens(type_parm))
        if token is not None and current != group_type:
            parameters.write_value(target.modifier, type_parm, token)


def finish(context, confirm):
    """Leave the selection. On confirm, write the group string of what is selected into the
    field, in one undo step. Either way, restore everything begin() changed; a cancel that
    removed a temporary copy pushes an undo step too (see restore.py). Returns the text
    written, or None."""
    global _session
    session = _session
    if session is None:
        return None
    text = group_type = None
    if confirm and not is_broken(context):
        group_type = current_type(context)
        text = group_string.write_selection(selected_elements(context, group_type), group_type)
    _session = None
    restore.revert(context, session.record)
    if text is not None:
        try:
            _write(session, text, group_type)
        except Exception:
            feedback.log.exception("group selection, the field could not be written")
        restore.push_undo("Select Group")
    elif session.record.temporary:
        restore.push_undo("Cancel Group Selection")  # the copy is gone: see restore.py
    feedback.redraw()
    return text


def forget():
    """Drop the session without touching any data: the file being loaded replaces what the
    session changed (its marker, if it has one, is repaired once it is loaded)."""
    global _session
    session, _session = _session, None
    if session is not None:
        for name in session.record.paused:
            tree = bpy.data.node_groups.get(name)
            if tree is not None:
                cook.cooks.resume(tree)
