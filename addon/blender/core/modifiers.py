"""Houdini modifiers: create one, apply it, remove it, move it to another version of its HDA."""

import bpy
from bpy.props import StringProperty

from ...shared import contract
from . import cook, events, link, parameters, preferences, properties, stack_cuts, streams, targets


class ModifierOperator:
    """Mixin of the operators acting on one Houdini modifier, named by its node group."""

    tree: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def node_group(self):
        tree = bpy.data.node_groups.get(self.tree)
        return tree if targets.is_houdini_tree(tree) else None


def _selected_inputs(context, exclude):
    """Selected input objects, the active one first."""
    found = [o for o in context.selected_objects
             if o.type in targets.INPUT_TYPES and o not in exclude and not o.houdini_bridge_result]
    active = context.active_object
    if active in found:
        found.remove(active)
        found.insert(0, active)
    return found


def supported_definitions(path):
    """The definitions of the SOP HDAs of a file, read by the server (started and waited
    for). Raises hython.ServerError when it cannot read them."""
    link.server.ensure_running(blocking=True)
    answer, _ = link.server.request_sync(contract.request(contract.DEFINITIONS, path=path))
    return [d for d in answer["definitions"] if d["supported"]]


def create_hda_object(context, path, definition):
    """Create a new object driven by an HDA: an empty mesh carrying a Houdini modifier.

    The selected input objects become its inputs. A generator (no input) is placed at the
    3D cursor; an operator stays at the origin, so its world-space inputs line up."""
    label = definition["label"] or definition["type"]
    sources = _selected_inputs(context, exclude=set())
    obj = bpy.data.objects.new(label, bpy.data.meshes.new(label))
    context.collection.objects.link(obj)
    prefs = preferences.get()
    if definition.get("max_inputs", 0) == 0 and prefs.generator_at_cursor:
        obj.location = context.scene.cursor.location
    create_hda_modifier(context, obj, path, definition, use_self=False, sources=sources)
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def create_hda_modifier(context, obj, path, definition, use_self=True, sources=None):
    """Add an HDA as a non-destructive modifier on `obj`.

    With `use_self`, the object's geometry (with the modifiers above) feeds input 1;
    the other inputs take `sources` (by default the other selected input objects). The
    cooked mesh is written into a hidden holder object shown by the modifier.
    """
    label = definition["label"] or definition["type"]
    # The holder is linked to no collection: the node group is its only user.
    holder_mesh = obj.data.copy() if use_self and obj.type == "MESH" else bpy.data.meshes.new(label)
    holder = bpy.data.objects.new("%s · %s" % (obj.name, label), holder_mesh)
    holder.houdini_bridge_result = True
    tree = parameters.build_node_group("Houdini · %s" % label, holder)

    settings = tree.houdini_bridge
    settings.path = path
    settings.node_type = definition["type"]
    settings.label = label
    settings.holder = holder
    prefs = preferences.get()
    settings.live = prefs.live_default
    if settings.output_mode != prefs.default_output:
        settings.output_mode = prefs.default_output
    if sources is None:
        sources = _selected_inputs(context, exclude={obj})
    sources = list(sources)
    for index in range(definition.get("max_inputs", 0)):
        item = settings.inputs.add()
        item.label = "Input %d" % (index + 1)
        if index == 0 and use_self:
            item.is_self = True
        elif sources:
            item.object = sources.pop(0)
    settings.enabled = True

    modifier = obj.modifiers.new(name=tree.name, type="NODES")
    modifier.node_group = tree
    modifier.show_in_editmode = True
    modifier.show_on_cage = False
    modifier.show_expanded = True
    parameters.apply_houdini_interface_to_modifier(modifier)  # its inputs come with the first cook
    obj.modifiers.active = modifier
    cook.allow_file()  # the user's own action: the open file may cook (core/trust.py)
    cook.cooks.request(tree, force=True)  # asked for, even with Live Updates off (then locked)
    return modifier


def place_hda(context, path, definition, target=None):
    """Apply an HDA as set in "Apply to": a modifier on the active mesh when possible,
    else a new object. Generators (no input) always become new objects."""
    if target is None:
        target = preferences.get().default_target
    active = context.active_object
    can_modify = (
        target == "MODIFIER"
        and definition.get("max_inputs", 0) > 0
        and active is not None
        and active.type in targets.INPUT_TYPES
        and not active.houdini_bridge_result
    )
    if can_modify:
        return create_hda_modifier(context, active, path, definition)
    return create_hda_object(context, path, definition)


def houdini_modifiers(obj):
    """(modifier, node group) of the Houdini modifiers of an object, in stack order."""
    found = []
    for modifier in obj.modifiers:
        tree = targets.houdini_tree(modifier)
        if tree is not None:
            found.append((modifier, tree))
    return found


def _move_modifiers(source, destination):
    """Move the Geometry Nodes modifiers of `source` onto `destination`, with
    their input values. Returns the Houdini node groups among them."""
    moved = []
    for modifier in list(source.modifiers):
        copy = destination.modifiers.new(modifier.name, "NODES")
        copy.node_group = modifier.node_group
        copy.show_viewport, copy.show_render = modifier.show_viewport, modifier.show_render
        tree = modifier.node_group
        if tree is None:
            continue
        for item in tree.interface.items_tree:
            if item.item_type != "SOCKET" or item.in_out != "INPUT" or item.socket_type == "NodeSocketGeometry":
                continue
            parameters.copy_input(modifier, copy, item.identifier)
        if targets.is_houdini_tree(tree):
            parameters.apply_houdini_interface_to_modifier(copy)
            moved.append(tree)
    source.modifiers.clear()
    return moved


def apply_modifier(context, tree, with_above=False):
    """Apply a Houdini modifier, and with `with_above` every modifier above it (they are
    already in its result). The mesh becomes the object's mesh (a Volume object gets a mesh
    object next to it); instances, curves, points and volume become objects of their own.
    Returns (object, made, groups adopted).

    Raises ValueError, before anything changes, for a bypassed modifier (its holder keeps a
    result made before the bypass, which Blender's own Apply would write into the mesh), for
    one the Display flag of a modifier above cuts from the viewport (it cooked without the
    modifiers the flag cut, so its result is not the stack's), for a volume whose live .vdb
    file is gone (a file opened again that has not cooked), and for a mesh Blender's Apply
    refuses (shared, with shape keys, linked). With `with_above`, the bypassed modifiers above
    stay on the object."""
    settings = tree.houdini_bridge
    obj, modifier = targets.modifier_users(tree)[0]
    _check_can_apply(obj, modifier)
    above = targets.shown_above(obj, modifier) if with_above else []
    outputs = properties.outputs(settings)
    # A mesh object keeps only a mesh: the other streams become objects first.
    made = streams.apply_to_objects(settings, obj)
    mesh_object = _apply_mesh(context, obj, modifier, settings)
    if mesh_object is not None:
        made.insert(0, mesh_object)
    _remove_applied(tree, obj, above)
    summary = {}
    events.modifier_applied.emit(obj, outputs, summary)
    obj = _replace_emptied(obj, made)
    _select_result(context, obj, made)
    return obj, made, summary.get("groups", 0)


def _check_can_apply(obj, modifier):
    if targets.is_bypassed(modifier):
        raise ValueError("%s is bypassed: turn Bypass off to apply it" % modifier.name)
    if stack_cuts.held_by(modifier):
        raise ValueError("%s is cut from the viewport by the Display flag of a modifier above: lift the flag "
                         "first" % modifier.name)
    if streams.volume_file_gone(modifier.node_group.houdini_bridge):
        raise ValueError("The volume of %s is not on disk any more (a live .vdb file lasts while Houdini runs): "
                         "Recook it first" % modifier.name)
    _check_blender_applies(obj)


def _check_blender_applies(obj):
    """What Blender's own Apply refuses on a mesh object, checked before the streams become
    objects: refused there, Apply would leave them in the scene."""
    if obj.type != "MESH":
        return  # a Volume object takes no mesh: Blender's Apply is not used
    mesh = obj.data
    if not (obj.is_editable and mesh.is_editable):
        raise ValueError("%s is linked from another file: it cannot be changed here" % obj.name)
    if mesh.users - int(mesh.use_fake_user) > 1:
        raise ValueError("The mesh of %s is shared by other objects: make it single user first "
                         "(Object > Relations > Make Single User)" % obj.name)
    if mesh.shape_keys is not None:
        raise ValueError("%s has shape keys, which Blender cannot keep through Apply: remove them first" % obj.name)


def _blender_apply(context, obj, modifier):
    """Blender's own Apply of one modifier; a refusal of it is a ValueError."""
    try:
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_apply(modifier=modifier.name)
    except RuntimeError as error:
        raise ValueError("Blender could not apply %s: %s" % (modifier.name, str(error).strip())) from error


def _apply_mesh(context, obj, modifier, settings):
    """Write the cooked mesh into a mesh object with Blender's own Apply. A Volume object
    cannot take a mesh: the mesh gets an object of its own, returned (None when the result
    has no mesh), and the modifier is removed."""
    if obj.type == "MESH":
        _blender_apply(context, obj, modifier)
        return None
    mesh = settings.holder.data.copy() if settings.holder else None
    result = None
    if mesh is not None and len(mesh.vertices):
        result = bpy.data.objects.new("%s · mesh" % obj.name, mesh)
        for collection in obj.users_collection:
            collection.objects.link(result)
        result.matrix_world = obj.matrix_world
    elif mesh is not None:
        bpy.data.meshes.remove(mesh)
    obj.modifiers.remove(modifier)
    return result


def _remove_applied(tree, obj, above):
    """Release the applied modifier, and remove the modifiers above that went into its result."""
    cook.release_modifier(tree)
    for other in above:
        group = targets.houdini_tree(other)
        obj.modifiers.remove(other)
        if group is not None and not targets.modifier_users(group):
            cook.release_modifier(group)


def _replace_emptied(obj, made):
    """The object that holds the result. When nothing is left of the object (an empty mesh, or
    a Volume object whose result replaced it), the one object made takes its place, with its
    name and the modifiers below (a Convert VDB keeps reading the volume)."""
    emptied = obj.type != "MESH" or not len(obj.data.vertices)
    if not (len(made) == 1 and emptied and not obj.children and (
            not obj.modifiers
            or (made[0].type in targets.INPUT_TYPES and all(m.type == "NODES" for m in obj.modifiers)))):
        return obj
    moved = _move_modifiers(obj, made[0])
    name, data = obj.name, obj.data
    bpy.data.objects.remove(obj)
    if data is not None and data.users == 0:
        streams.remove_data(data)
    made[0].name = name
    for moved_tree in moved:
        cook.delete_instance(moved_tree)  # its input changed object: cooked again from scratch
        cook.cooks.request(moved_tree, force=True)
    return made[0]


def _select_result(context, obj, made):
    for other in context.selected_objects:
        other.select_set(False)
    for new in made + [obj]:
        if new.name in context.view_layer.objects:
            new.select_set(True)
    context.view_layer.objects.active = obj if obj.name in context.view_layer.objects else None


def apply_all(context, obj):
    """Apply the whole stack of an object: its last Houdini modifier that is not bypassed,
    with everything above it, then the Blender modifiers below it. Bypassed modifiers stay
    on the object, and so do the modifiers above that are off in the viewport only (its
    result left them out). Returns what apply_modifier returns, or None when there is no
    Houdini modifier to apply. Raises ValueError while the Display flag cuts modifiers from
    the viewport (the viewport and the renders then show two different stacks), and for a
    mesh Blender's Apply refuses."""
    if any(stack_cuts.held_by(modifier) for modifier in obj.modifiers):
        raise ValueError("The Display flag cuts modifiers of %s from the viewport: lift the flag first" % obj.name)
    _check_blender_applies(obj)
    houdini = [(modifier, tree) for modifier, tree in houdini_modifiers(obj) if not targets.is_bypassed(modifier)]
    if not houdini:
        return None
    below = [modifier.name for modifier in stack_cuts.below(obj, houdini[-1][0])]
    obj, made, adopted = apply_modifier(context, houdini[-1][1], with_above=True)
    if obj.type == "MESH":
        for modifier in [obj.modifiers.get(name) for name in below]:
            if modifier is None or targets.is_bypassed(modifier):
                continue
            _blender_apply(context, obj, modifier)
    return obj, made, adopted


def remove_modifier(tree):
    """Remove a Houdini modifier from every object using it (their own meshes are untouched)."""
    for obj, modifier in targets.modifier_users(tree):
        obj.modifiers.remove(modifier)
    cook.release_modifier(tree)


def pick_group(tree, name, group):
    """Put a group in the group field `name` of a Houdini modifier (added to the groups
    already there when the field takes several). False when the field or the modifier is
    gone."""
    parm = tree.houdini_bridge.parms.get(name)
    users = targets.modifier_users(tree)
    if parm is None or not users:
        return False
    modifier = users[0][1]
    parameters.write_value(modifier, parm, parameters.pick_group(parm, parameters.read_value(modifier, parm), group))
    return True


def upgrade(tree, tool):
    """Switch a Houdini modifier to another version of its HDA, the shelf tool `tool`
    (parameters with the same name keep their value). Returns the type it ran before."""
    settings = tree.houdini_bridge
    old = settings.node_type
    settings.path = tool["file"]
    settings.node_type = tool["type"]
    cook.cooks.request(tree, force=True)
    return old
