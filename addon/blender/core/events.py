"""Signals: how the core tells the optional features that something happened.

A feature connects its functions in its register() and disconnects them in its
unregister(); the core emits the signal where the event happens. A signal is
synchronous: emit() calls the connected functions at once, on the main thread,
in the order they were connected, and catches nothing. A signal returns nothing
and never moves a cook forward: what the core needs in order to go on is a
direct call.
"""


class Signal:
    """A named event: emit(*arguments) calls every connected function with those arguments."""

    def __init__(self, name, doc=""):
        self.name = name
        self.__doc__ = doc
        self._listeners = []

    def connect(self, listener):
        """Call `listener` at each emit, after the functions connected before it."""
        self._listeners.append(listener)

    def disconnect(self, listener):
        """Stop calling `listener` (nothing happens when it is not connected)."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, *arguments):
        # A copy: a listener may disconnect itself, or another one, while it is called.
        for listener in tuple(self._listeners):
            listener(*arguments)

    @property
    def listeners(self):
        """The connected functions, in the order they are called."""
        return tuple(self._listeners)

    def __repr__(self):
        return "<Signal %s, %d listener(s)>" % (self.name, len(self._listeners))


server_started = Signal("server_started", """server_started()

Emitted by cook/scheduler.begin_session each time a new Houdini session answers
(a first start, a restart, a start after an idle stop), before anything is asked
of it. What was sent before is already forgotten.""")

result_applied = Signal("result_applied", """result_applied(target, extras)

Emitted by cook/result._write_result once the cooked mesh, its attributes and its
groups are written to the holder (`extras`, as mesh_io.write_mesh returns them, also
in settings.outputs_json). Not written yet: the native streams, the cook count, the
status and the timings; the Houdini modifiers below are not asked to recook yet.""")

modifier_released = Signal("modifier_released", """modifier_released(settings)

Emitted by cook.release_modifier when a Houdini modifier is applied or removed, once
settings.enabled is False. The holder (the cache is attached to it), the native
streams and the server instance are still there, and dropped after the signal.""")

setting_changed = Signal("setting_changed", """setting_changed(settings, name)

Emitted from the `update` callback of a setting of a Houdini modifier, after the
new value is stored. `name` is the property's name ("use_cache"); a listener
checks it.""")

modifier_applied = Signal("modifier_applied", """modifier_applied(obj, outputs, summary)

Emitted by modifiers.apply_modifier once a Houdini modifier is applied: its
streams are objects of their own, its mesh is the mesh of `obj` (or, on a
Volume object, an object of its own), and it and the modifiers applied with it
are released and gone from the stack. `outputs` are the groups and attributes
of its last result. Not done yet: an emptied `obj` may still be replaced by
the single object Apply made, and the selection is not set.

`summary` is a dictionary the listeners fill in for the message shown to the
user (summary["groups"]: how many groups joined the Groups panel).""")
