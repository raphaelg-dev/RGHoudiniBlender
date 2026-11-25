"""Building the request of a cook: reading the inputs and the parameters, and
comparing them with what was sent last (cook/state.py).

prepare_request() records what it prepares as sent; peek_request() builds the
same request and records nothing.
"""

import os

import bpy
from mathutils import Matrix

from ....shared import contract
from .. import mesh_io, parameters, preferences, streams, targets
from . import state


class OwnUpdates:
    """The depsgraph updates the engine causes itself, which must not recook (main thread only).

    - objects: uids of the objects whose next geometry update we caused
      (update_from_editmode, showing modifiers again after reading a stack);
    - reading_stack: the uid of the object whose modifier stack is partially evaluated
      right now, or None. Only that object's updates are ours: another object's, in the
      same evaluation, may be a new result another target reads.
    """

    def __init__(self):
        self.objects = set()
        self.reading_stack = None

    def take(self, geometry):
        """The uids among `geometry` whose update we caused, forgotten from now on."""
        ours = self.objects & geometry
        self.objects.difference_update(geometry)
        return ours

    def reset(self):
        self.objects.clear()
        self.reading_stack = None


own_updates = OwnUpdates()


def parm_values(target):
    """The parameter values in the server's format, as a cook sends them and as CookState
    compares them; {} once the modifier is gone."""
    return parameters.read_values(target.modifier, target.settings) if target.modifier else {}


def _values_sent(target, parms):
    """The values a cook sends: all but those Houdini drives with an expression (parm.expression)
    that did not change since the last cook, which Houdini keeps evaluating; the first cook
    of a session sends none of those. Changed, a value is sent and replaces the expression."""
    known = state.sent.parms(target.key)
    driven = {parm.name for parm in target.settings.parms if parm.expression}
    return {name: value for name, value in parms.items()
            if name not in driven or (known is not None and name in known and known[name] != value)}


def _read_up_to_modifier(target, prefix):
    """The owner's mesh with the modifiers above this one applied (Subdivision,
    Mirror, other Houdini modifiers…).

    Blender cannot evaluate a stack partially, so this modifier and the ones
    below are hidden from the viewport for one evaluation, then shown again. The
    depsgraph handler ignores the owner's updates of that evaluation, and the update that showing them
    again triggers is recorded as our own."""
    owner = target.owner
    hidden = []
    seen_target = False
    for modifier in owner.modifiers:
        seen_target = seen_target or modifier == target.modifier
        if seen_target and modifier.show_viewport:
            hidden.append(modifier)
    own_updates.reading_stack = owner.session_uid
    try:
        for modifier in hidden:
            modifier.show_viewport = False
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        return mesh_io.read_object(owner, depsgraph, Matrix.Identity(4), prefix)
    finally:
        for modifier in hidden:
            modifier.show_viewport = True
        own_updates.reading_stack = None
        own_updates.objects.add(owner.session_uid)


def _read_source(target, item, depsgraph, prefix):
    """(source object, arrays, topology digest, content digest) of one input, or None."""
    if item.is_self:
        # What this modifier receives, in the owner's local space (no transform).
        source = target.owner
        if target.modifiers_above():
            return (source,) + _read_up_to_modifier(target, prefix)
        if source.mode == "EDIT":
            # Edit Mode changes reach the mesh only through update_from_editmode,
            # which triggers one more depsgraph update that must not recook.
            source.update_from_editmode()
            own_updates.objects.add(source.session_uid)
        vertex_groups = [group.name for group in source.vertex_groups]
        return (source,) + mesh_io.read_mesh(source.data, Matrix.Identity(4), prefix, vertex_groups)
    source = item.object
    if source is None:
        return None
    return (source,) + mesh_io.read_object(source, depsgraph, target.space() @ source.matrix_world, prefix)


def scene_time(scene=None):
    """(frame, frames per second) of the scene; the frame includes the subframe."""
    scene = scene or bpy.context.scene
    render = scene.render
    return scene.frame_current + scene.frame_subframe, render.fps / render.fps_base


def stack_above(target):
    """The names of the modifiers shown above a target: when they change (a modifier moved,
    hidden or shown), input 1 is another mesh."""
    return [modifier.name for modifier in target.modifiers_above()]


def chained_above(target):
    """The Houdini modifier right above this one, whose Houdini result feeds
    input 1 directly on the server (an Object Merge): volumes, packed
    primitives, curves and points pass as they are, which reading the
    evaluated mesh from Blender would lose. None when the modifier above is a
    Blender one, plays a cache, or nothing is above."""
    above = target.modifiers_above()
    tree = targets.houdini_tree(above[-1]) if above else None
    if tree is None or tree.houdini_bridge.use_cache or state.sent.cook_count(tree.session_uid) is None:
        return None
    return tree


# --- the inputs -----------------------------------------------------------------------
# Each kind of input gives (state, key, fields, arrays): its state in the request
# (contract.INPUT_STATES), what is recorded as sent for it (None: nothing), the request
# fields it adds under its index, and the arrays it sends.


def _chained_input(chain, known, force):
    """Input 1 fed by the Houdini modifier above: follows its result count, not the mesh
    Blender shows."""
    key = (contract.CHAIN, chain.session_uid, state.sent.cook_count(chain.session_uid))
    input_state = contract.UNCHANGED if known == key and not force else contract.CHAIN
    return input_state, key, {"chains": str(chain.session_uid)}, {}


def _volume_input(target, item, source, known, force):
    """A Volume object: its .vdb file, loaded by the server, and the matrix that brings its
    grids into Houdini space (its placement in the owner's space, then the axes), as 16
    row-vector floats. No input when the volume has no file on disk (packed, or made by
    Geometry Nodes only: modifiers of the volume object are not read)."""
    path = bpy.path.abspath(source.data.filepath) if source.data.filepath else ""
    if not path or not os.path.exists(path):
        return contract.NONE, None, {}, {}
    placement = Matrix.Identity(4) if item.is_self else target.space() @ source.matrix_world
    full = mesh_io.TO_HOUDINI @ placement
    rows = [value for row in full.transposed() for value in row]
    digest = "%s|%s|%s" % (path, os.path.getmtime(path), ",".join("%.6g" % value for value in rows))
    key = (source.session_uid, "volume", digest)
    input_state = contract.UNCHANGED if known == key and not force else contract.FULL
    return input_state, key, {"input_volumes": {"path": path, "matrix": rows}}, {}


def _mesh_input(target, item, index, known, force, dirty, depsgraph):
    """A mesh, read again only when the depsgraph marked it (`dirty`) or a cook is forced. Its
    digests decide what travels: nothing when its content is unchanged, the values only when
    its topology, groups and attribute layout are, else everything."""
    source = target.owner if item.is_self else item.object
    same_source = known is not None and known[0] == source.session_uid
    if same_source and not force and not dirty:
        return contract.UNCHANGED, known, {}, {}
    _, arrays, meta, topology, content = _read_source(target, item, depsgraph, contract.INPUT_PREFIX % index)
    key = (source.session_uid, topology, content)
    fields = {"input_attributes": meta["attributes"]} if meta["attributes"] else {}
    if same_source and not force and known[2] == content:
        return contract.UNCHANGED, key, {}, {}
    if same_source and not force and known[1] == topology:
        return contract.POSITIONS, key, fields, {name: arrays[name] for name in meta["values"]}
    if meta["groups"]:
        fields["input_groups"] = meta["groups"]
    return contract.FULL, key, fields, arrays


def _read_inputs(target, force):
    """(states, fields, arrays, sent) of the inputs of a cook: their states in order, the
    request fields they add ({field: {input index: value}}), the arrays they send, and what
    is recorded as sent for each."""
    uid = target.key
    sent = state.sent.inputs(uid)
    dirty = state.sent.dirty(uid)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    states, arrays = [], {}
    fields = {"input_groups": {}, "input_attributes": {}, "chains": {}, "input_volumes": {}}
    for index, item in enumerate(target.settings.inputs):
        known = sent.get(index)
        chain = chained_above(target) if item.is_self else None
        source = target.owner if item.is_self else item.object
        if chain is not None:
            found = _chained_input(chain, known, force)
        elif source is None:
            found = contract.NONE, None, {}, {}
        elif source.type == "VOLUME":
            found = _volume_input(target, item, source, known, force)
        else:
            found = _mesh_input(target, item, index, known, force, index in dirty, depsgraph)
        input_state, key, input_fields, input_arrays = found
        states.append(input_state)
        if key is None:
            sent.pop(index, None)
        else:
            sent[index] = key
        for field, value in input_fields.items():
            fields[field][str(index)] = value
        arrays.update(input_arrays)
    return states, fields, arrays, sent


# --- the request ----------------------------------------------------------------------


def _read(target, force, buttons, always, output):
    """(request, what it sends as a state.Sent). The request is (header, arrays), or None
    when nothing changed since the last cook (unless `always`: a cache needs every frame).
    `output` is the output mode asked for, None for the modifier's own."""
    settings = target.settings
    uid = target.key
    stack = stack_above(target)
    states, fields, arrays, sent = _read_inputs(target, force)
    parms = parm_values(target)
    frame, fps = scene_time()
    # A static HDA ignores the frame: changing frames only recooks one that reads the time.
    new_frame = settings.time_dependent and state.sent.frame(uid) != (frame, fps)
    if (not always and not force and not buttons and not new_frame
            and all(s in (contract.UNCHANGED, contract.NONE) for s in states) and state.sent.parms(uid) == parms):
        return None, state.Sent(sent, stack)
    prefs = preferences.get()
    header = contract.request(
        contract.COOK,
        instance=str(uid),
        path=bpy.path.abspath(settings.path),
        type=settings.node_type,
        inputs=states,
        parms=_values_sent(target, parms),
        buttons=list(buttons),
        **fields,
        extras=prefs.bring_back_extras,
        frame=frame,
        fps=fps,
        # Native: instances, curves, points and volumes in their own streams (core/streams/).
        output=output or settings.output_mode.lower(),
        volume_dir=streams.volume_dir(),
        sphere_frequency=prefs.sphere_frequency,
        resend=bool(force),  # the prototypes again, even those Blender should have
    )
    return (header, arrays), state.Sent(sent, stack, parms, (frame, fps))


def prepare_request(target, force=False, buttons=(), always=False, output=None):
    """(header, arrays) for a cook, or None when nothing changed since the last one
    (unless `always`: a cache needs every frame). `output` replaces the modifier's output
    mode (a cached frame is one mesh: contract.MESH). Records what it prepares as sent."""
    request, sent = _read(target, force, buttons, always, output)
    state.sent.record(target.key, sent)
    return request


def peek_request(target, force=False, buttons=(), always=False, output=None):
    """What prepare_request would give now, recording nothing as sent."""
    return _read(target, force, buttons, always, output)[0]
