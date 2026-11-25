"""The cooks asked for and the cooks in flight.

cooks.request() puts a Houdini modifier in the queue; the timer
(cook/scheduler.py) sends it once nothing of that modifier is in flight, so
quick changes merge into one cook. A modifier being cached (features/cache.py
makes those cooks itself) is not cooked live.
"""

import contextlib
import time

from .. import animation
from . import state


class CookQueue:
    """Main thread only. The methods take datablocks; the fields are keyed by target uid.

    - pending: target uid -> {"force": bool, "buttons": [...]}, the cooks asked for, oldest first;
    - in_flight: target uids whose request is sent and not answered yet;
    - overtaken: those of in_flight a synchronous cook (cook_now) cooked again meanwhile,
      whose answer, older than the result shown, is dropped;
    - started: target uid -> perf_counter() when its request was sent;
    - caching: target uid -> (frames done, frame count, perf_counter() at start), written by
      features/cache.py while it makes a cache;
    - quiet: while true, input_changed() does nothing (a parameter list being rebuilt);
    - paused: target uids not cooked for now (pause()), their inputs still marked when they change;
    - bypassed: target uids the poll saw bypassed (track_bypass()), which cook again once
      the bypass is turned off.
    """

    def __init__(self):
        self.pending = {}
        self.in_flight = set()
        self.overtaken = set()
        self.started = {}
        self.caching = {}
        self.quiet = False
        self.paused = set()
        self.bypassed = set()

    # --- asking for cooks -------------------------------------------------------------

    def request(self, tree, force=False, buttons=()):
        if tree.houdini_bridge.use_cache:
            return  # the cached frames are shown; turning the cache off recooks
        request = self.pending.setdefault(tree.session_uid, {"force": False, "buttons": []})
        request["force"] = request["force"] or force
        request["buttons"].extend(buttons)

    def cancel(self, tree):
        self.pending.pop(tree.session_uid, None)

    def is_requested(self, tree):
        return tree.session_uid in self.pending

    def input_changed(self, tree, item):
        """An input of a Houdini modifier was set (its object, in the modifier panel): read it again."""
        if self.quiet or not tree.houdini_bridge.enabled:
            return
        index = list(tree.houdini_bridge.inputs).index(item)
        state.sent.mark_dirty(tree.session_uid, index)
        if tree.houdini_bridge.live:
            self.request(tree)

    @contextlib.contextmanager
    def ignoring_input_changes(self):
        """Silence input_changed() while the inputs are rebuilt from a description."""
        self.quiet = True
        try:
            yield
        finally:
            self.quiet = False

    # --- cooks in flight --------------------------------------------------------------

    def start(self, uid):
        self.in_flight.add(uid)
        self.started[uid] = time.perf_counter()

    def finish(self, uid):
        """The answer of uid arrived: False when it is to be dropped (overtaken)."""
        self.in_flight.discard(uid)
        self.started.pop(uid, None)
        if uid in self.overtaken:
            self.overtaken.discard(uid)
            return False
        return True

    def clear_in_flight(self):
        """The server stopped: no answer will come."""
        self.in_flight.clear()
        self.overtaken.clear()
        self.started.clear()

    def is_busy(self):
        return bool(self.pending or self.in_flight)

    # --- bypassed and paused targets --------------------------------------------------

    def track_bypass(self, target):
        """Note whether a target is bypassed, and return True once when its bypass has just been
        turned off. The cooks asked for while it was bypassed were dropped, but the inputs that
        changed meanwhile are still marked, so the caller asks for a cook then."""
        if target.bypassed:
            self.bypassed.add(target.key)
            return False
        if target.key in self.bypassed:
            self.bypassed.discard(target.key)
            return True
        return False

    def pause(self, tree):
        """Stop cooking this Houdini modifier until resume(). Cooks asked for meanwhile are
        dropped, but the inputs that change are still marked, so the cook resume() asks for
        reads them. Not stored in the file."""
        self.paused.add(tree.session_uid)
        self.pending.pop(tree.session_uid, None)

    def resume(self, tree):
        """Cook this Houdini modifier again, and ask for a cook unless it is locked (that cook
        sends nothing when nothing changed while it was paused)."""
        self.paused.discard(tree.session_uid)
        if tree.houdini_bridge.live:
            self.request(tree)

    def is_paused(self, tree):
        return tree.session_uid in self.paused

    # --- a new file, unregister -------------------------------------------------------

    def clear(self):
        """A new file: no cook asked for, in flight or being cached."""
        self.pending.clear()
        self.in_flight.clear()
        self.overtaken.clear()
        self.caching.clear()
        self.paused.clear()
        self.bypassed.clear()

    def reset(self):
        """Everything, as new (unregister)."""
        self.clear()
        self.started.clear()
        self.quiet = False


cooks = CookQueue()


# --- which targets may cook -----------------------------------------------------------


def can_cook(target):
    """Whether the engine may cook a target and write its result now: not bypassed, not
    playing a cache, not being cached, not paused."""
    settings = target.settings
    return (target.valid and not target.bypassed and not settings.use_cache
            and target.key not in cooks.caching and target.key not in cooks.paused)


def cooks_by_itself(target):
    """Whether a change (depsgraph, poll, frame) may cook this target without the user
    asking: it can cook, and it is not locked (settings.live off: only Recook cooks it)."""
    return can_cook(target) and target.settings.live


# --- inputs marked as changed ---------------------------------------------------------


def mark_self_dirty(target):
    for index, item in enumerate(target.settings.inputs):
        if item.is_self:
            state.sent.mark_dirty(target.key, index)


def mark_animated_inputs(target):
    """Mark the inputs that may have changed with the frame; True if any."""
    # The other inputs are expressed in the owner's space: they follow its moves.
    owner_moves = animation.moves(target.owner, set())
    marked = False
    for index, item in enumerate(target.settings.inputs):
        if item.is_self:
            # What this modifier receives: the mesh and the modifiers above it.
            animated = animation.changes_shape(target.owner, before=target.modifier)
        else:
            animated = item.object is not None and (owner_moves or animation.is_animated(item.object))
        if animated:
            state.sent.mark_dirty(target.key, index)
            marked = True
    return marked
