"""CookState: what was sent to Houdini for each Houdini modifier.

A cook sends only what changed since the last one: this is what it compares with.
Keys are target.key. Main thread only. A new Houdini session knows nothing, so the
state is reset() before anything is prepared for it (cook/scheduler.begin_session).
"""

import itertools
import secrets


class Sent:
    """What preparing one cook records for its target (CookState.record): its inputs and
    the modifiers above it always; its parameter values and frame only when a request
    is made (frame is None otherwise)."""

    __slots__ = ("inputs", "stack", "parms", "frame")

    def __init__(self, inputs, stack, parms=None, frame=None):
        self.inputs = inputs
        self.stack = stack
        self.parms = parms
        self.frame = frame


class CookState:
    """Per target uid:

    - inputs: {input index: (source uid, topology digest, content digest)}, or
      ("chain", uid of the modifier above, its cook count), or (source uid, "volume", digest);
    - parms: the parameter values of the last cook;
    - frames: (frame, frames per second) of the last cook;
    - stacks: names of the modifiers above it at the last cook;
    - counts: how many results were written (what a chained modifier below follows), and
      whether the last one was renumbered (see count_result);
    - stamps: what the last result written carries in its holder (result.COOK_STAMP), never
      given twice, after a reset or in another Blender session either: an undo that brings
      back an older result is told by it, which a count starting again at 1 could not;
    - dirty: the input indices marked as changed since the last cook;
    - released: whose server instance was deleted (applied or removed modifiers).
    """

    def __init__(self):
        self._inputs = {}
        self._parms = {}
        self._frames = {}
        self._stacks = {}
        self._counts = {}
        self._renumbered = set()  # uids whose last result was renumbered (count_result)
        self._stamps = {}
        self._stamp_numbers = itertools.count(1)  # never reset
        self._stamp_prefix = secrets.token_hex(4)  # this CookState's, not another session's
        self._dirty = {}
        self._released = set()

    # --- what was sent ----------------------------------------------------------------

    def inputs(self, uid):
        """{input index: key} of the last cook (a copy)."""
        return dict(self._inputs.get(uid, {}))

    def parms(self, uid):
        """Parameter values of the last cook, None before the first one."""
        return self._parms.get(uid)

    def frame(self, uid):
        return self._frames.get(uid)

    def stack(self, uid):
        """Names of the modifiers above at the last cook, None before the first one."""
        return self._stacks.get(uid)

    def cook_count(self, uid):
        """How many results were written for this target in this session, None before the first."""
        return self._counts.get(uid)

    def stamp(self, uid):
        """The stamp of the last result written for this target, None before the first."""
        return self._stamps.get(uid)

    def record(self, uid, sent):
        """Record what a prepared cook sent, and clear its input marks."""
        self._inputs[uid] = sent.inputs
        self._stacks[uid] = sent.stack
        self._dirty.pop(uid, None)
        if sent.frame is not None:
            self._parms[uid] = sent.parms
            self._frames[uid] = sent.frame

    def record_parms(self, uid, parms):
        """Record the values the server uses (a parameter list just built)."""
        self._parms[uid] = parms

    def count_result(self, uid, renumbered=False):
        """Count a written result. `renumbered` (the answer's "renumbered"): its mesh does not
        number the elements as the HDA's own output does, which a chained modifier below reads."""
        self._counts[uid] = self._counts.get(uid, 0) + 1
        self._stamps[uid] = "%s-%d" % (self._stamp_prefix, next(self._stamp_numbers))
        if renumbered:
            self._renumbered.add(uid)
        else:
            self._renumbered.discard(uid)

    def is_renumbered(self, uid):
        return uid in self._renumbered

    def drop_count(self, uid):
        """The server lost this target's result (a chain below reads Blender's mesh meanwhile)."""
        self._counts.pop(uid, None)
        self._stamps.pop(uid, None)
        self._renumbered.discard(uid)

    # --- inputs marked as changed -----------------------------------------------------

    def dirty(self, uid):
        """The input indices marked as changed since the last cook (a copy)."""
        return set(self._dirty.get(uid, set()))

    def mark_dirty(self, uid, index):
        self._dirty.setdefault(uid, set()).add(index)

    # --- released modifiers -----------------------------------------------------------

    def release(self, uid):
        """Note that this target's server instance was deleted: an undo that brings the modifier
        back recooks it."""
        self._released.add(uid)

    def take_released(self, uid):
        """Whether the target was released, forgetting that it was."""
        if uid in self._released:
            self._released.discard(uid)
            return True
        return False

    # --- forgetting -------------------------------------------------------------------

    def forget_inputs(self, uid):
        """Forget the inputs sent for a target (its cook failed), so its next cook sends them all.
        Its parameter values stay, which the poll compares: changing one cooks it again."""
        self._inputs.pop(uid, None)

    def forget(self, uid):
        """Forget what was sent for a target, so its next cook sends everything. Its cook count
        and its input marks stay (a chain below keeps following the count)."""
        self._inputs.pop(uid, None)
        self._parms.pop(uid, None)
        self._frames.pop(uid, None)
        self._stacks.pop(uid, None)

    def reset(self):
        """A new Houdini session, or a stopped one: nothing was sent or cooked. The input marks
        and the released modifiers stay."""
        self._inputs.clear()
        self._parms.clear()
        self._frames.clear()
        self._stacks.clear()
        self._counts.clear()
        self._stamps.clear()
        self._renumbered.clear()

    def clear_marks(self):
        self._dirty.clear()

    def clear(self):
        """Everything, as new (unregister)."""
        self.reset()
        self._dirty.clear()
        self._released.clear()


sent = CookState()
