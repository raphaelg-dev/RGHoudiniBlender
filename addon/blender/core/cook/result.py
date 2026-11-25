"""Applying what Houdini answers: the description (parameter and input lists)
and the result (mesh, streams, status, timings).

The order of apply_result is the order a result reaches Blender: the
description first, then the mesh, the signal for the features
(events.result_applied), the native streams, the cook count, and the Houdini
modifiers below, which cook again.
"""

import json
import time

from ....shared import contract
from .. import events, mesh_io, parameters, preferences, streams, targets
from . import queue, request, state

MAX_ERROR_TEXT = 2000  # characters kept of an error (error_text)
# A property of the holder: the cook count of the result it holds, which an undo compares
# with CookState (result_undone). Stored in .blend files.
COOK_STAMP = "houdini_bridge_cook"

# The signature of a parameter list holds whatever changes the rebuilt inputs: for every
# parameter, its name (a multiparm count change), kind, size and folder path (panels), and
# the fields of Kind.signature (menu items, the range and its limits, the type of a ramp).
# It is stored in .blend files (settings.structure), so the layout of a row is fixed: name,
# kind, size, menu, range, folder, ramp, with null for a field the kind does not have; any
# other field of a kind comes at the end of its row.
RANGE = ("min", "max", "min_strict", "max_strict")
LAYOUT = ("menu", *RANGE, "ramp")


def _signature(parms):
    return json.dumps([_row(p) for p in parms])


def _row(description):
    own = parameters.kind_of(description["kind"]).signature

    def field(name):
        return description.get(name) if name in own else None

    row = (description["name"], description["kind"], description.get("size", 1), field("menu"),
           [field(name) for name in RANGE], _folder(description), field("ramp"))
    return row + tuple(description.get(name) for name in own if name not in LAYOUT)


def _folder(description):
    """The folder path of a parameter as stored: labels separated by tabs."""
    folder = description.get("folder") or []
    return parameters.FOLDER_SEPARATOR.join(folder)


def apply_description(target, answer):
    """Update the parameter and input lists. True when a recook is needed."""
    settings = target.settings
    needs_recook = False
    with queue.cooks.ignoring_input_changes():
        settings.label = answer.get("label", settings.label)
        labels = answer.get("inputs", [])
        while len(settings.inputs) > len(labels):
            settings.inputs.remove(len(settings.inputs) - 1)
        while len(settings.inputs) < len(labels):
            settings.inputs.add()
        for item, label in zip(settings.inputs, labels, strict=False):
            item.label = label
        parms = answer.get("parms", [])
        signature = _signature(parms)
        if signature != settings.structure:
            needs_recook = _rebuild_parms(target, parms, signature)
        _refresh_parms(target, parms)
        parameters.apply_houdini_interface(target.tree)
    return needs_recook


def _rebuild_parms(target, parms, signature):
    """New or edited HDA: rebuild the parameter list and the inputs, keeping the values
    already set. True when a kept value differs from the HDA's, which must then cook it."""
    settings, modifier = target.settings, target.modifier
    needs_recook = False
    kinds = {p.name: p.kind for p in settings.parms}
    previous = {(name, kinds[name]): value for name, value in parameters.read_values(modifier, settings).items()}
    sockets = {p.name: p.socket for p in settings.parms if p.socket}
    # A ramp keeps the Houdini interpolations of the ramp of the same name.
    bases = {p.name: p.ramp_basis for p in settings.parms if p.kind == contract.RAMP}
    settings.parms.clear()
    values = {}
    for description in parms:
        parm = settings.parms.add()
        parm.name = description["name"]
        parm.label = description["label"]
        parm.folder = _folder(description)
        parm.help = description.get("help", "")
        parm.kind = description["kind"]
        parm.size = description.get("size", 1)
        parm.multiparm = bool(description.get("multiparm"))
        if parm.name in bases:
            parm.ramp_basis = bases[parm.name]
        kind = parameters.kind_of(parm.kind)
        if kind.copy is not None:
            kind.copy(parm, description)
        parameters.store_range(parm, description)
        parm.hidden = bool(description.get("hidden"))
        parm.expression = bool(description.get("expression"))
        if "value" in description:
            values[parm.name] = description["value"]
        old_value = previous.get((parm.name, parm.kind))
        if old_value is not None and old_value != values.get(parm.name):
            values[parm.name] = old_value
            needs_recook = True
    settings.structure = signature
    parameters.sync_interface(target.tree, settings, sockets, folders_closed=not preferences.get().folders_open)
    parameters.apply_values(modifier, settings, values)
    return needs_recook


def _refresh_parms(target, parms):
    """What every answer refreshes without a rebuild. Values never come back from the server
    after creation (a dragged slider would jump back): only the hidden, disabled and driven
    (expression) states,
    what a string takes from every answer (the suggestions of a group field) and the folder
    types, which only the panel draws (they are not part of the signature)."""
    folders_changed = False
    for parm, description in zip(target.settings.parms, parms, strict=False):
        parm.hidden = bool(description.get("hidden"))
        parm.disabled = bool(description.get("disabled"))
        parm.expression = bool(description.get("expression"))
        folder_types = json.dumps(description.get("folder_types") or [])
        if parm.folder_types != folder_types:
            parm.folder_types = folder_types
            folders_changed = True
        parameters.refresh_from_answer(parm, description)
    if folders_changed:
        parameters.forget_layout(target.tree)


def forget_lost(target, answer):
    """The server lost inputs (a restart, a reloaded HDA): forget what was sent for this
    target, and the result count of every chained modifier above it the server lost.
    Returns the uids of those."""
    state.sent.forget(target.key)
    lost = [int(uid) for uid in answer.get(contract.MISSING_CHAINS, [])]
    for uid in lost:
        state.sent.drop_count(uid)
    return lost


def _recook_below(target):
    """A new result changes what every Houdini modifier further down receives."""
    if target.owner.mode == "EDIT" and preferences.get().edit_mode_cook == "ON_EXIT":
        return
    modifiers = list(target.owner.modifiers)
    for modifier in modifiers[modifiers.index(target.modifier) + 1:]:
        tree = targets.houdini_tree(modifier)
        if tree is not None and modifier.show_viewport:
            below = targets.Target(tree)
            queue.mark_self_dirty(below)
            if below.settings.live:
                queue.cooks.request(below.tree)


def error_text(error):
    """What settings.errors keeps of an error: its end, at most MAX_ERROR_TEXT characters. The
    panel, the sidebar and the viewport badge show its first lines, so a traceback starts with
    its last line, the cause, and goes on with the end of the rest."""
    lines = error.strip().splitlines()
    if len(lines) < 2 or not lines[0].startswith("Traceback"):
        return error[-MAX_ERROR_TEXT:]
    cause = lines[-1][:MAX_ERROR_TEXT]
    room = MAX_ERROR_TEXT - len(cause) - 1
    return cause + "\n" + "\n".join(lines[:-1])[-room:] if room > 0 else cause


def apply_result(target, answer, output, error, duration):
    settings = target.settings
    if error:
        settings.errors = error_text(error)
        settings.status = "failed"
        # The server may not hold the inputs; the values stay as sent, so changing one cooks again.
        state.sent.forget_inputs(target.key)
        return
    if contract.MISSING_INPUTS in answer:
        for uid in forget_lost(target, answer):
            # The server lost the modifier above (restart): it cooks again first.
            above = targets.by_uid(uid)
            if above is not None:
                queue.cooks.request(above, force=True)
        queue.cooks.request(target.tree, force=True)
        return
    structure = settings.structure
    if apply_description(target, answer):
        queue.cooks.request(target.tree)
    elif settings.structure != structure:
        # The parameter list was just built: its values are the ones the server already uses,
        # so they must not count as a change. Otherwise what prepare_request recorded stays:
        # a value typed while this cook was in flight is not sent yet, and the poll sends it.
        state.sent.record_parms(target.key, request.parm_values(target))
    errors = answer.get("errors", [])
    settings.errors = "\n".join(errors)
    if settings.time_dependent != bool(answer.get("time_dependent")):
        settings.time_dependent = bool(answer.get("time_dependent"))
    if not errors and "P" in output:
        if target.holder.mode == "EDIT":
            settings.status = "output in Edit Mode: not updated"
        else:
            start = time.perf_counter()
            _write_result(target, answer, output)
            _report(settings, output, answer.get("timings", {}), duration, time.perf_counter() - start)
    elif errors:
        settings.status = "cook error (last result kept)"


def _write_result(target, answer, output):
    """Write the mesh and the streams of a result, and tell the features and the Houdini
    modifiers below."""
    settings = target.settings
    extras = mesh_io.write_mesh(target.holder.data, answer, output, target.own_materials())
    settings.outputs_json = json.dumps(extras)
    events.result_applied.emit(target, extras)
    if not streams.write(settings, answer, output):
        queue.cooks.request(target.tree, force=True)  # a prototype went missing
    state.sent.count_result(target.key, bool(answer.get("renumbered")))
    target.holder[COOK_STAMP] = state.sent.stamp(target.key)
    _recook_below(target)


def result_undone(target):
    """Whether an undo or a redo brought back in the holder a result other than the last one
    written: the undo step was pushed before that result came (a value typed in the panel
    pushes its step, then cooks)."""
    holder, stamp = target.settings.holder, state.sent.stamp(target.key)
    return holder is not None and stamp is not None and holder.get(COOK_STAMP) != stamp


def follow_owner_materials(target):
    """A result Houdini gives no material shows its owner's (mesh_io.write_mesh): the holder,
    and the cached frames, take a material set on the owner without a cook."""
    materials = target.own_materials()
    if materials is None:
        return
    meshes = [target.holder.data]
    if target.settings.cache_collection is not None:
        meshes += [obj.data for obj in target.settings.cache_collection.objects if obj.type == "MESH"]
    for mesh in meshes:
        mesh_io.follow_materials(mesh, materials)


def _report(settings, output, timings, duration, blender_time):
    """The status line of a result (what it holds) and its timings."""
    settings.status = "%d points, %d faces" % (len(output["P"]) // 3, len(output["counts"]))
    described = streams.describe(settings)
    if described:
        settings.status += " · " + described
    settings.timings = "total %.0f ms · cook %.0f · import %.0f · export %.0f · Blender %.0f" % (
        duration * 1000,
        timings.get("cook", 0) * 1000,
        timings.get("import", 0) * 1000,
        timings.get("export", 0) * 1000,
        blender_time * 1000,
    )
    stream_times = ["%s %.0f" % (name, timings[name] * 1000) for name in streams.STREAM_NAMES if name in timings]
    if stream_times:
        settings.timings += " · " + " · ".join(stream_times)
