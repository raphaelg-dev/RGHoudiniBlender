"""The main-thread timer: it sends the cooks, applies the answers, sends the other
commands, does the upkeep and stops hython when idle. Also the synchronous cook
(cook_now), and starting, stopping and releasing.

A request is only prepared after ensure_server() returned True, and
ensure_server() resets cook/state.py for a session it sees for the first time,
so nothing prepared for an older session reaches a new one (link.server.post also binds
each request to the connection it was prepared for).
"""

import time
import traceback

import bpy

from ....shared import contract
from .. import events, feedback, hython, link, parameters, preferences, streams, targets, trust
from . import queue, request, result, state

# Where a result shows: the viewports, and the modifier panel of the Properties editor.
RESULT_AREAS = ("VIEW_3D", "PROPERTIES")
TICK_BUSY = 0.02  # seconds between two ticks while something waits, or during playback
TICK_IDLE = 0.1  # seconds between two ticks otherwise
UPKEEP_INTERVAL = 1.0  # seconds between two maintain()


class Scheduler:
    """What the timer keeps from one tick to the next, and the commands. Main thread only.

    - next_upkeep: time.monotonic() of the next maintain();
    - shown_status: the server state and whether a cook is in flight, as last drawn;
    - rendering: a render runs (no live update meanwhile), set by the render handlers;
    - commands: (key, header, callback) waiting for the server;
    - callbacks: key -> callback(answer, error) of the commands sent;
    - frame_callbacks: key -> callback(answer, output, error) of the cooks made for a cache.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.next_upkeep = 0.0
        self.shown_status = None
        self.rendering = False
        self.commands = []
        self.callbacks = {}
        self.frame_callbacks = {}


timer = Scheduler()


# --- the server, as the cook engine sees it -------------------------------------------


def begin_session():
    """When a new Houdini session answers, forget what was sent (it knows nothing yet),
    remove the .vdb files an ended Blender left, and emit events.server_started, before
    anything is prepared for that session."""
    if link.server.take_new_session():
        state.sent.reset()
        streams.remove_orphan_volume_files()
        events.server_started.emit()


def ensure_server(blocking=False):
    """True when the server answers (a new session is begun first). Otherwise start it, in
    the background by default (see ServerLink.ensure_running)."""
    if not link.server.ensure_running(blocking):
        return False
    begin_session()
    return True


def stop_server():
    """Stop hython: nothing sent is known any more, and no answer will come."""
    link.server.stop()
    state.sent.reset()
    queue.cooks.clear_in_flight()


def _recook_all():
    for target in targets.all_targets():
        if target.settings.live:
            queue.cooks.request(target.tree, force=True)


def start_server():
    """The Start button: allow the open file (core/trust.py), clear a failed start, start
    hython, and recook every modifier that is not locked."""
    trust.allow()
    link.server.startup_error = ""
    ensure_server()
    _recook_all()


def allow_file():
    """Allow Execution, or an action of the user on a Houdini modifier: the open file may cook
    (core/trust.py). When it was waiting, every modifier that is not locked cooks now."""
    if trust.allow():
        _recook_all()


def start_with_blender():
    """Preference "Start With Blender": the first cook does not wait for hython. Not for a
    file that waits for Allow Execution."""
    if preferences.get().start_with_blender and not bpy.app.background and not trust.is_blocked():
        ensure_server()
    return None


# --- releasing and splitting modifiers ------------------------------------------------


def _remove_holder(settings):
    holder = settings.holder
    settings.holder = None
    if holder is not None:
        mesh = holder.data
        bpy.data.objects.remove(holder)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def release_modifier(tree):
    """A Houdini modifier was applied or removed: turn it off, emit events.modifier_released,
    then drop its streams, its holder and its server instance."""
    settings = tree.houdini_bridge
    settings.enabled = False
    events.modifier_released.emit(settings)
    streams.release(settings)
    _remove_holder(settings)
    state.sent.drop_count(tree.session_uid)
    state.sent.release(tree.session_uid)  # an undo may bring it back: see handlers.on_undo
    delete_instance(tree)


def _give_own_holder(tree):
    """Give this node group, which shared its holder, a copy of the holder and its mesh."""
    holder = tree.houdini_bridge.holder
    if holder is None:
        return
    new_holder = holder.copy()
    new_holder.data = holder.data.copy()
    for modifier in new_holder.modifiers:  # a cache player of its own: caching the original again leaves it
        if modifier.type == "NODES" and modifier.node_group is not None:
            modifier.node_group = modifier.node_group.copy()
    tree.houdini_bridge.holder = new_holder
    streams.detach(tree.houdini_bridge)
    for node in tree.nodes:
        if node.bl_idname == "GeometryNodeObjectInfo":
            node.inputs["Object"].default_value = new_holder


def make_unique(tree, modifier):
    """Give a duplicated object its own copy of a Houdini modifier, with a copy of the result
    (a locked one keeps it)."""
    copy = tree.copy()
    _give_own_holder(copy)
    modifier.node_group = copy
    parameters.apply_houdini_interface(copy)
    if copy.houdini_bridge.live:
        queue.cooks.request(copy, force=True)
    return copy


def maintain():
    """Once a second: clean up removed modifiers, split modifiers shared by duplicates."""
    holders = set()
    trees = targets.houdini_trees()
    users_by_tree = targets.users_by_tree(trees)  # splitting one tree changes the users of no other
    for tree in trees:
        users = users_by_tree[tree]
        if not users:
            release_modifier(tree)
            continue
        for _, modifier in users[1:]:
            make_unique(tree, modifier)
        # Blender may duplicate the node group with the object: the copy then
        # still points at the original's holder.
        holder = tree.houdini_bridge.holder
        if holder is not None and holder.session_uid in holders:
            _give_own_holder(tree)
            if tree.houdini_bridge.live:
                queue.cooks.request(tree, force=True)
        elif holder is not None:
            holders.add(holder.session_uid)


def delete_instance(tree):
    """Delete the server instance of a target, so that its next cook starts from scratch."""
    uid = tree.session_uid
    state.sent.forget(uid)
    queue.cooks.cancel(tree)
    if link.server.is_running():
        link.server.post(None, contract.request(contract.DELETE, instance=str(uid)))


# --- what is not a live cook ----------------------------------------------------------


def post_cook(key, header, arrays, callback):
    """Send a prepared cook; callback(answer, output, error) gets the geometry on the main
    thread, and nothing is applied to the target."""
    timer.frame_callbacks[key] = callback
    link.server.post(key, header, arrays)


def send_command(key, header, callback):
    """Send a command other than a cook (the catalog...), starting the server when needed;
    callback(answer, error) is called on the main thread."""
    timer.commands.append((key, header, callback))


def activity():
    """([(target, seconds cooking, changes waiting)] for every cook in flight, whether
    cooks wait for hython to start), for the viewport feedback."""
    now = time.perf_counter()
    cooking = []
    found = targets.by_keys(queue.cooks.in_flight | queue.cooks.caching.keys())
    for uid in queue.cooks.in_flight:
        target = found.get(uid)
        if target is not None and target.valid:
            cooking.append((target, now - queue.cooks.started.get(uid, now), uid in queue.cooks.pending))
    for uid, (_, _, started) in queue.cooks.caching.items():
        if uid in found and uid not in queue.cooks.in_flight:
            cooking.append((found[uid], now - started, False))
    waiting_for_server = bool(queue.cooks.pending) and link.server.starting
    return cooking, waiting_for_server


# --- the synchronous cook -------------------------------------------------------------


def cook_now(tree, force=False, buttons=()):
    """Cook and apply the result before returning (tests, background mode). Returns the
    server answer, {} when nothing was sent (nothing changed, or the modifier is bypassed)."""
    target = targets.Target(tree)
    if target.bypassed:
        queue.cooks.cancel(tree)
        return {}
    ensure_server(blocking=True)
    answer = {}
    for _ in range(2):  # a second time when the server asks for the inputs in full
        prepared = request.prepare_request(target, force=force, buttons=buttons)
        queue.cooks.cancel(tree)
        if prepared is None:
            return {}
        header, arrays = prepared
        start = time.perf_counter()
        try:
            answer, output = link.server.request_sync(header, arrays)
            error = ""
        except hython.ServerError as exception:
            answer, output, error = {}, {}, str(exception)
        if contract.MISSING_INPUTS in answer:
            result.forget_lost(target, answer)  # the chains are read from Blender this time
            force = True
            continue
        result.apply_result(target, answer, output, error, time.perf_counter() - start)
        queue.cooks.cancel(tree)
        if target.key in queue.cooks.in_flight:
            queue.cooks.overtaken.add(target.key)  # its answer, when it comes, is older than this one
        return answer
    return answer


# --- the timer ------------------------------------------------------------------------


def poll_modifier_inputs():
    """Recook a modifier whose inputs changed: edited in the modifier panel, keyframed
    or driven. Changing a modifier input fires no depsgraph update a handler can see,
    so the values are compared with the last ones sent."""
    for target in targets.all_targets():
        if target.modifier is not None:
            parameters.hold_within_limits(target.modifier, target.settings)
        if queue.cooks.track_bypass(target) and target.settings.live:
            # Back from a bypass: what changed meanwhile is read now (nothing is sent if nothing did).
            queue.cooks.request(target.tree)
        if not queue.cooks_by_itself(target):
            continue
        key = target.key
        stack = state.sent.stack(key)
        if key not in queue.cooks.in_flight and stack is not None and stack != request.stack_above(target):
            # Moving a modifier or hiding one changes what the ones below receive.
            queue.mark_self_dirty(target)
            queue.cooks.request(target.tree)
        if key in queue.cooks.pending or key in queue.cooks.in_flight or state.sent.parms(key) is None:
            continue
        if request.parm_values(target) != state.sent.parms(key):
            queue.cooks.request(target.tree)


def _waits_for_stack(target):
    """Modifiers cook from the top of the stack down. After a reorder, one that is
    now above this one may still be chained on it on the server, and chaining
    this one to it first would close a loop ("Infinite recursion in evaluation")."""
    if not target.valid:
        return False
    for modifier in target.modifiers_above():
        tree = targets.houdini_tree(modifier)
        if tree is not None and (tree.session_uid in queue.cooks.pending or tree.session_uid in queue.cooks.in_flight):
            return True
    return False


def _upkeep():
    if time.monotonic() >= timer.next_upkeep:
        timer.next_upkeep = time.monotonic() + UPKEEP_INTERVAL
        maintain()


def _deliver(key, answer, output, error, duration):
    """Hand one answer of the worker to the callback waiting for it, or apply it to its target."""
    if key is None:
        return
    if key in timer.callbacks:
        timer.callbacks.pop(key)(answer, error)
        feedback.redraw(RESULT_AREAS)
        return
    if key in timer.frame_callbacks:
        timer.frame_callbacks.pop(key)(answer, output, error)
        feedback.redraw(RESULT_AREAS)
        return
    if not queue.cooks.finish(key):
        feedback.redraw(RESULT_AREAS)
        return
    tree = targets.by_uid(key)
    if tree is not None:
        target = targets.Target(tree)
        if target.valid:
            result.apply_result(target, answer, output, error, duration)
    feedback.redraw(RESULT_AREAS)


def _apply_answers():
    for answer in link.server.take_results():
        _deliver(*answer)


def _send_commands():
    commands = timer.commands
    if not commands:
        return
    if ensure_server():
        while commands:
            key, header, callback = commands.pop(0)
            timer.callbacks[key] = callback
            link.server.post(key, header)
    elif link.server.startup_error:
        while commands:
            commands.pop(0)[2]({}, link.server.startup_error)
        feedback.redraw(RESULT_AREAS)


def _send_pending():
    if queue.cooks.pending and trust.is_blocked():
        queue.cooks.pending.clear()  # the file waits for Allow Execution: hython is not started
        feedback.redraw(RESULT_AREAS)
        return
    if queue.cooks.pending and ensure_server():
        waiting = list(queue.cooks.pending)
        found = targets.by_keys(waiting)
        for uid in waiting:
            if uid in queue.cooks.in_flight:
                continue
            target = found.get(uid)
            if target is not None and _waits_for_stack(target):
                continue
            wanted = queue.cooks.pending.pop(uid)
            if target is None:
                continue
            if not queue.can_cook(target):
                queue.cooks.track_bypass(target)  # a bypassed one cooks again once its bypass is off
                continue
            try:
                prepared = request.prepare_request(target, wanted["force"], wanted["buttons"])
            except Exception:
                feedback.log.exception("the cook of %s could not be prepared", target.tree.name)
                target.settings.errors = result.error_text(traceback.format_exc())
                target.settings.status = "failed"
                continue
            if prepared is None:
                continue
            target.settings.status = "cooking…"
            queue.cooks.start(uid)
            link.server.post(uid, *prepared)
        feedback.redraw(RESULT_AREAS)
    elif queue.cooks.pending and link.server.startup_error:
        for uid in list(queue.cooks.pending):
            tree = targets.by_uid(uid)
            if tree is not None:
                tree.houdini_bridge.status = "hython server unavailable"
        queue.cooks.pending.clear()
        feedback.redraw(RESULT_AREAS)


def _is_busy():
    """Anything waiting keeps the timer at its fast pace."""
    return bool(queue.cooks.is_busy() or timer.commands or timer.callbacks or timer.frame_callbacks
                or queue.cooks.caching or link.server.starting)


def tick():
    if timer.rendering:
        # The render reads the scene: no result written, no maintenance, until it ends.
        return TICK_IDLE
    try:
        _upkeep()
        poll_modifier_inputs()
        begin_session()
        _apply_answers()
        _send_commands()
        _send_pending()
    except Exception:
        # The timer goes on: a failed step is logged, and the next tick tries again.
        feedback.log.exception("a step of the engine timer failed")
    busy = _is_busy()
    stop_when_idle(busy)
    in_flight = bool(queue.cooks.in_flight)
    status = (link.server.status()[1], in_flight)
    if in_flight or link.server.starting or status != timer.shown_status:
        # Keeps the cooking feedback animated, and the header indicator up to date.
        timer.shown_status = status
        feedback.redraw()
    # During playback every frame may ask for a cook: the slow idle pace would skip most of them.
    return TICK_BUSY if busy or _playing() else TICK_IDLE


def stop_when_idle(busy):
    """Preference "Stop When Idle": Houdini holds a license as long as it runs. The next
    cook starts it again, and sends every input again."""
    minutes = preferences.get().idle_minutes
    if not minutes or busy or not link.server.is_running() or _playing():
        return
    if link.server.idle_seconds() > minutes * 60:
        stop_server()
        link.server.idle_stop = minutes


def _playing():
    window_manager = bpy.context.window_manager
    return window_manager is not None and any(window.screen.is_animation_playing for window in window_manager.windows)


# --- unregister -----------------------------------------------------------------------


def shutdown():
    """At unregister, once the timers and handlers are gone and before link stops hython:
    deliver the answers already arrived, give every request still waiting the error
    "server stopped" (as the worker does), so nothing waits for an answer that will not
    come, then reset everything."""
    error = "server stopped"
    try:
        _apply_answers()
    except Exception:
        feedback.log.exception("an answer could not be applied at unregister")
    waiting = ([(key, {}, {}, error, 0.0) for key in list(timer.callbacks)]
               + [(key, {}, {}, error, 0.0) for key in list(timer.frame_callbacks)]
               + [(uid, {}, {}, error, 0.0) for uid in queue.cooks.in_flight])
    for answer in waiting:
        try:
            _deliver(*answer)
        except Exception:
            feedback.log.exception("a waiting request could not be told the server stopped")
    for _, _, callback in timer.commands:
        try:
            callback({}, error)
        except Exception:
            feedback.log.exception("a waiting command could not be told the server stopped")
    timer.reset()
    queue.cooks.reset()
    state.sent.clear()
    request.own_updates.reset()
