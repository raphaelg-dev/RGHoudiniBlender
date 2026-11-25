"""The Blender handlers of the cook engine (depsgraph, frame, render, file, undo),
its timers, and register / unregister.

The handlers only mark inputs and ask for cooks; the timer (cook/scheduler.py)
reads the inputs and sends the cooks.
"""

import bpy

from ....shared import contract
from .. import link, mesh_io, parameters, preferences, streams, targets, trust
from . import queue, request, result, scheduler, state

FIRST_TICK_DELAY = 0.5  # seconds after registration before the timer's first tick
START_WITH_BLENDER_DELAY = 2.0  # seconds after registration before "Start With Blender" starts hython


@bpy.app.handlers.persistent
def on_depsgraph_update(scene, depsgraph):
    """An input moved or changed shape: mark it, and recook the targets that use it. A locked
    target is marked too, so that lifting its lock reads what changed meanwhile. A change of
    the owner's materials is a geometry update too: the result follows them."""
    reading = request.own_updates.reading_stack  # our own partial evaluation of that object's stack
    geometry = set()
    moved = set()
    for update in depsgraph.updates:
        datablock = update.id
        if not isinstance(datablock, bpy.types.Object):
            continue
        uid = datablock.original.session_uid
        if uid == reading:
            continue
        if update.is_updated_geometry:
            geometry.add(uid)
        if update.is_updated_transform:
            moved.add(uid)
    if not geometry and not moved:
        return
    ours = request.own_updates.take(geometry)
    changed = geometry | moved

    on_exit_only = preferences.get().edit_mode_cook == "ON_EXIT"
    for target in targets.all_targets():
        if not target.valid:
            continue
        if target.owner.session_uid in geometry and not scheduler.timer.rendering:
            result.follow_owner_materials(target)  # a material set on the owner, a locked one too
        # The other inputs are expressed in the owner's space: they follow its moves.
        owner_moved = target.owner.session_uid in moved
        for index, item in enumerate(target.settings.inputs):
            if item.is_self:
                uid = target.owner.session_uid
                touched = uid in geometry and uid not in ours
            else:
                touched = item.object is not None and (item.object.session_uid in changed or owner_moved)
            if not touched:
                continue
            state.sent.mark_dirty(target.key, index)
            if not target.settings.live:
                continue  # locked: lifting the lock reads the inputs marked
            if item.is_self and on_exit_only and target.owner.mode == "EDIT":
                # Heavy HDAs: wait for the user to leave Edit Mode, which updates
                # the mesh once more and lands here again.
                target.settings.status = "waiting for Edit Mode to end"
                continue
            queue.cooks.request(target.tree)


@bpy.app.handlers.persistent
def on_frame_change(scene, depsgraph=None):
    """Recook the targets whose HDA reads the time, and those with an animated input. The
    inputs are only marked here; they are read when the cook is prepared. They are marked on
    a target that does not cook by itself too (locked, bypassed, paused): lifting that reads
    them."""
    if scheduler.timer.rendering:
        return
    for target in targets.all_targets():
        if not target.valid:
            continue
        animated = queue.mark_animated_inputs(target)
        if queue.cooks_by_itself(target) and (animated or target.settings.time_dependent):
            queue.cooks.request(target.tree)


@bpy.app.handlers.persistent
def on_render_start(*_):
    scheduler.timer.rendering = True


@bpy.app.handlers.persistent
def on_render_end(*_):
    scheduler.timer.rendering = False


@bpy.app.handlers.persistent
def on_file_loaded(*_):
    """New .blend file: clear the server instances, apply the Houdini interface to every
    Houdini modifier and recook every one that is not locked (a locked one keeps the result
    saved in its holder), unless the file waits for Allow Execution (core/trust.py): then
    each shows the result saved with it. The meshes of the old file are gone, and another
    mesh may take the address of one of them."""
    trust.file_loaded()
    mesh_io.forget_written()
    parameters.forget_layout(tabs=True)  # the plans of the panel, and the tabs opened in the old file
    state.sent.reset()
    state.sent.clear_marks()
    queue.cooks.clear()
    scheduler.timer.rendering = False
    if link.server.is_running():
        link.server.post(None, contract.request(contract.CLEAR))
    for target in targets.all_targets():
        parameters.apply_houdini_interface(target.tree)
        if target.settings.live and not trust.is_blocked():
            queue.cooks.request(target.tree, force=True)


@bpy.app.handlers.persistent
def on_undo(*_):
    """Undo and redo bring data back, not the server's state: a modifier whose instance was
    deleted (applied, removed), whose .vdb file is gone (each cook replaces the previous
    one) or whose holder came back with an older result than the last one written (its
    values may not be that result's) is cooked again from scratch, unless it is locked. The
    meshes written before may be gone too: the next write of each mesh is a full one."""
    mesh_io.forget_written()
    parameters.forget_layout()  # the plans of the panel: a node group may now hold other folders
    for target in targets.all_targets():
        if (state.sent.take_released(target.key) or streams.volume_file_gone(target.settings)
                or result.result_undone(target)):
            state.sent.forget(target.key)
            state.sent.drop_count(target.key)
            if target.settings.live:
                queue.cooks.request(target.tree, force=True)


# (name of a bpy.app.handlers list, function), in the order they are added.
HANDLERS = (
    ("depsgraph_update_post", on_depsgraph_update),
    ("frame_change_post", on_frame_change),
    ("render_init", on_render_start),
    ("render_complete", on_render_end),
    ("render_cancel", on_render_end),
    ("load_post", on_file_loaded),
    ("undo_post", on_undo),
    ("redo_post", on_undo),
)


def register():
    trust.forget()  # a file may be open already: whether it may cook is worked out again
    for name, function in HANDLERS:
        getattr(bpy.app.handlers, name).append(function)
    bpy.app.timers.register(scheduler.tick, first_interval=FIRST_TICK_DELAY, persistent=True)
    bpy.app.timers.register(scheduler.start_with_blender, first_interval=START_WITH_BLENDER_DELAY)


def unregister():
    """Remove the timers and the handlers, answer every request still waiting with an error
    (scheduler.shutdown), then stop hython and the worker thread."""
    for timer in (scheduler.tick, scheduler.start_with_blender):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for name, function in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    scheduler.shutdown()
    link.server.shutdown()
