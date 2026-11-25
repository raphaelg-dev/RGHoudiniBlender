"""Targets: the Houdini modifiers, found from their node groups, and their holders.

A target is a Houdini modifier: a Geometry Nodes modifier whose node group
carries the settings and shows a hidden "holder" object where the cooked mesh
is written. A new object driven by an HDA is an empty mesh carrying one.
"""

import bpy

# What a Houdini modifier can sit on and read: meshes, and Volume objects (their .vdb).
INPUT_TYPES = {"MESH", "VOLUME"}


def is_bypassed(modifier):
    """Whether a modifier is bypassed, like a node with Houdini's bypass flag: off both in
    the viewport and in renders. A Houdini modifier is not cooked meanwhile. Only one of the
    two off is not a bypass: the modifier is still needed where it shows, and cooks."""
    return not modifier.show_viewport and not modifier.show_render


def set_bypassed(modifier, bypassed):
    """Bypass a modifier, or turn it back on, in the viewport and in renders together."""
    modifier.show_viewport = modifier.show_render = not bypassed


def is_houdini_tree(tree):
    """Whether a node group is the node group of a Houdini modifier. bpy.data.node_groups
    also holds shader and compositor groups, which have no settings."""
    return tree is not None and tree.bl_idname == "GeometryNodeTree" and tree.houdini_bridge.enabled


def houdini_tree(modifier):
    """The node group of a Houdini modifier, None for any other modifier."""
    tree = modifier.node_group if modifier is not None and modifier.type == "NODES" else None
    return tree if is_houdini_tree(tree) else None


def houdini_trees():
    """The node groups of the Houdini modifiers of the open file, as a list: a caller may add
    node groups while it walks them."""
    return [tree for tree in bpy.data.node_groups if is_houdini_tree(tree)]


def shown_above(obj, modifier):
    """The modifiers before `modifier` in the stack of `obj` that are shown in the viewport."""
    above = []
    for other in obj.modifiers:
        if other == modifier:
            break
        if other.show_viewport:
            above.append(other)
    return above


def modifier_users(tree):
    """(object, modifier) pairs whose Geometry Nodes modifier uses this node group."""
    return users_by_tree([tree])[tree]


def users_by_tree(trees):
    """{node group: its modifier_users} for these node groups, in one walk of every object's
    modifiers. A walk per node group cost 60 ms a tick for 20 Houdini modifiers among 5000
    objects of 2 modifiers each; one walk costs 3 ms."""
    users = {tree: [] for tree in trees}
    if not users:
        return users
    for obj in bpy.data.objects:
        for modifier in obj.modifiers:
            if modifier.type == "NODES":
                found = users.get(modifier.node_group)
                if found is not None:
                    found.append((obj, modifier))
    return users


class Target:
    """A Houdini modifier: its node group (tree, with its settings), owner object and holder."""

    def __init__(self, tree, users=None):
        """`users`: the modifier_users of the node group, when the caller has them already."""
        self.tree = tree
        self.settings = tree.houdini_bridge
        self.key = tree.session_uid
        self.owner = None
        self.modifier = None
        if users is None:
            users = modifier_users(tree)
        if users:
            self.owner, self.modifier = users[0]
        self.holder = self.settings.holder

    @property
    def valid(self):
        return self.settings.enabled and self.holder is not None and self.owner is not None

    @property
    def bypassed(self):
        return self.modifier is not None and is_bypassed(self.modifier)

    def space(self):
        """Matrix from world space to the owner's local space, where the HDA works."""
        return self.owner.matrix_world.inverted()

    def modifiers_above(self):
        return shown_above(self.owner, self.modifier)

    def own_materials(self):
        """The materials of the owner's mesh, which a result Houdini gives no material shows,
        or None: the result comes from the holder, so a material set on the owner would never
        reach it. A Volume object's materials are volume shaders, not meant for a mesh."""
        if self.owner is None or self.owner.type != "MESH":
            return None
        return list(self.owner.data.materials)


def by_uid(uid):
    """The node group whose session_uid is `uid` (a target's key), or None."""
    for tree in bpy.data.node_groups:
        if tree.session_uid == uid:
            return tree
    return None


def by_keys(keys):
    """{key: Target} of the node groups whose session_uid is among `keys` (target keys), the
    others left out; one walk of the modifiers for them all, as all_targets()."""
    keys = set(keys)
    trees = [tree for tree in bpy.data.node_groups if tree.session_uid in keys]
    users = users_by_tree(trees)
    return {tree.session_uid: Target(tree, users[tree]) for tree in trees}


def all_targets():
    trees = houdini_trees()
    users = users_by_tree(trees)
    return [Target(tree, users[tree]) for tree in trees]
