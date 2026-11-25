"""Whether an object may change from one frame to the next.

The frame handler and the cache ask it to know which inputs to read again on a
new frame; reading them then tells whether anything really changed.
"""


def _uses_scene_time(tree, seen):
    """Whether a Geometry Nodes tree (or one of its node groups) reads the scene time."""
    if tree is None or tree in seen:
        return False
    seen.add(tree)
    for node in tree.nodes:
        if node.bl_idname == "GeometryNodeInputSceneTime":
            return True
        if node.bl_idname == "GeometryNodeGroup" and _uses_scene_time(node.node_tree, seen):
            return True
    return False


def _has_animation(datablock):
    animation = getattr(datablock, "animation_data", None) if datablock is not None else None
    return animation is not None and (animation.action is not None or len(animation.drivers) > 0)


def is_animated(obj, seen=None):
    """Whether an object's evaluated mesh or its placement may change from one
    frame to the next (see moves and changes_shape)."""
    seen = set() if seen is None else seen
    if obj is None or obj.session_uid in seen:
        return False
    seen.add(obj.session_uid)
    return moves(obj, seen) or changes_shape(obj, seen=seen)


def moves(obj, seen):
    """Keyframes or drivers on the object, an animated parent, or a constraint following
    an animated object."""
    if _has_animation(obj) or (obj.parent is not None and is_animated(obj.parent, seen)):
        return True
    return any(_uses_animated_object(constraint, seen) for constraint in obj.constraints)


def changes_shape(obj, before=None, seen=None):
    """Keyframes or drivers on the object, its mesh or its shape keys, or a
    modifier (above `before`) that animates: an armature, hook or lattice
    following an animated object, Geometry Nodes reading the scene time, a
    Houdini modifier whose HDA reads the time.

    Modifier settings are properties of the object: their keyframes are found
    on the object itself."""
    seen = set() if seen is None else seen
    seen.add(obj.session_uid)
    if _has_animation(obj) or _has_animation(obj.data):
        return True
    shape_keys = getattr(obj.data, "shape_keys", None)
    if shape_keys is not None and _has_animation(shape_keys):
        return True
    for modifier in obj.modifiers:
        if modifier == before:
            break
        if not modifier.show_viewport:
            continue
        if modifier.type == "NODES" and modifier.node_group is not None:
            settings = modifier.node_group.houdini_bridge
            if settings.enabled:
                if settings.time_dependent:
                    return True
            elif _uses_scene_time(modifier.node_group, set()):
                return True
        if _uses_animated_object(modifier, seen):
            return True
    return False


def _uses_animated_object(owner, seen):
    """Whether a modifier or a constraint points at an animated object."""
    for prop in owner.bl_rna.properties:
        if prop.type == "POINTER" and getattr(prop.fixed_type, "identifier", "") == "Object":
            other = getattr(owner, prop.identifier, None)
            if other is not None and is_animated(other, seen=seen):
                return True
    return False
