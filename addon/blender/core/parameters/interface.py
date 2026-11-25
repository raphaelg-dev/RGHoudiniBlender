"""The node group of a Houdini modifier: its inputs (one per parameter, in panels),
the usage chain that makes them count as used, the nodes holding the ramps, and who draws
the inputs in the modifier panel: the extension's panel (the Houdini interface) or Blender
(apply_houdini_interface)."""

import bpy

from ....shared import contract
from .. import preferences, targets
from . import kinds, ramps, values

ADDON_NODES = ("NodeGroupInput", "NodeGroupOutput", "GeometryNodeObjectInfo")
# Group node joining the instances, curves, points and volume to the mesh (core/streams/).
STREAMS_NODE = "Houdini Streams"
MAX_TOOLTIP = 400  # characters of a parameter's help kept as its input's tooltip


def build_node_group(name, holder):
    """Geometry Nodes group that outputs the holder's mesh, in the owner's local space."""
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input = tree.nodes.new("NodeGroupInput")
    group_input.location = (-500, 0)
    info = tree.nodes.new("GeometryNodeObjectInfo")
    info.label = "Houdini result"
    info.transform_space = "ORIGINAL"
    info.inputs["Object"].default_value = holder
    info.location = (-200, 0)
    group_output = tree.nodes.new("NodeGroupOutput")
    group_output.location = (100, 0)
    tree.links.new(info.outputs["Geometry"], group_output.inputs[0])
    tree.description = "RG Houdini Bridge: shows the mesh cooked by Houdini. Its inputs are the HDA parameters."
    return tree


def is_hidden(parm, follow=None):
    """Whether a parameter is left out of the modifier: Houdini hides it (its hide-when conditions,
    as the last cook found them) and "Hide Like Houdini" is on (`follow`, the preference when
    not given). Hidden is not removed: the input keeps its value, and the value is still sent."""
    if follow is None:
        follow = preferences.get().follow_hide_when
    return parm.hidden and follow


def houdini_interface_on():
    """Whether our panel draws the inputs of the Houdini modifiers (the preference "Houdini
    Interface"), rather than Blender."""
    return preferences.get().houdini_interface


def _hidden_in_modifier(parm, houdini_interface):
    """hide_in_modifier of a parameter's input: every input with the Houdini interface (our
    panel draws them), else the ones Houdini hides (Blender draws the others)."""
    return True if houdini_interface else is_hidden(parm)


FOLDER_SEPARATOR = "\t"


def _configure_socket(socket, parm):
    if socket.name != parm.label:
        socket.name = parm.label
    if socket.socket_type == "NodeSocketVector":
        dimensions = max(2, min(parm.size, 4))
        if socket.dimensions != dimensions:
            socket.dimensions = dimensions
    socket.description = parm.help[:MAX_TOOLTIP] if parm.help else ""
    socket.force_non_field = True  # plain values: no attribute toggle in the modifier
    values.set_socket_limits(socket, parm)
    hidden = _hidden_in_modifier(parm, houdini_interface_on())
    if socket.hide_in_modifier != hidden:
        socket.hide_in_modifier = hidden


def _panel_path(panel):
    path = []
    while panel is not None and panel.name:
        path.insert(0, panel.name)
        panel = panel.parent
    return tuple(path)


STORE_TYPES = {
    "NodeSocketFloat": "FLOAT",
    "NodeSocketInt": "INT",
    "NodeSocketBool": "BOOLEAN",
    "NodeSocketVector": "FLOAT_VECTOR",
    "NodeSocketColor": "FLOAT_COLOR",
}


class UsageChain:
    """Makes every parameter input count as used.

    Blender greys out group inputs that do not reach the output. Each input is
    therefore stored as a named attribute on the Instance domain, between Object
    Info and Group Output: a mesh has no instances, so nothing is stored and the
    geometry is unchanged, but the inputs show as active in the modifier. A kind
    whose input cannot be stored as it is (Kind.use) goes through a node of its
    own first.
    """

    def __init__(self, tree):
        self.tree = tree
        self.group_input = next(node for node in tree.nodes if node.bl_idname == "NodeGroupInput")
        info = next(node for node in tree.nodes if node.bl_idname == "GeometryNodeObjectInfo")
        self.group_output = next(node for node in tree.nodes if node.bl_idname == "NodeGroupOutput")
        self.geometry = info.outputs["Geometry"]
        self.count = 0

    def _input_output(self, socket):
        return next(out for out in self.group_input.outputs if out.identifier == socket.identifier)

    def node(self, bl_idname):
        """A node for the next input, placed above its store."""
        node = self.tree.nodes.new(bl_idname)
        node.location = (-100 + 180 * self.count, 420)
        return node

    def link(self, source, destination):
        self.tree.links.new(source, destination)

    def store(self, source, data_type, label):
        node = self.tree.nodes.new("GeometryNodeStoreNamedAttribute")
        node.label = "Use %s" % label
        node.data_type = data_type
        node.domain = "INSTANCE"
        node.inputs["Name"].default_value = "__houdini_bridge_unused"
        node.location = (-100 + 180 * self.count, 260)
        node.hide = True
        self.tree.links.new(self.geometry, node.inputs["Geometry"])
        value = next(socket for socket in node.inputs if socket.name == "Value" and socket.enabled)
        self.tree.links.new(source, value)
        self.geometry = node.outputs["Geometry"]
        self.count += 1

    def add(self, parm, socket, kind):
        source = self._input_output(socket)
        if kind.use is not None:
            kind.use(self, parm, source)
        elif socket.socket_type in STORE_TYPES:
            self.store(source, STORE_TYPES[socket.socket_type], parm.label)

    def close(self):
        # The streams, when the result has some, are joined after the chain:
        # its Instance-domain stores must not reach the instances.
        streams = self.tree.nodes.get(STREAMS_NODE)
        if streams is not None:
            self.tree.links.new(self.geometry, streams.inputs[0])
            self.tree.links.new(streams.outputs[0], self.group_output.inputs[0])
        else:
            self.tree.links.new(self.geometry, self.group_output.inputs[0])


def _panel_for(interface, path, panels, panel_order, old_panels, folders_closed):
    """The panel of a folder path, made (or taken from `old_panels`) with its parents when
    missing. Not a closure of sync_interface: a recursive closure keeps the panels it saw
    alive until the garbage collector frees them, maybe after their node group is gone,
    which crashed Blender 5.2."""
    if path in panels:
        return panels[path]
    parent = _panel_for(interface, path[:-1], panels, panel_order, old_panels, folders_closed)
    panel = old_panels.pop(path, None) or interface.new_panel(path[-1], default_closed=folders_closed)
    interface.move_to_parent(panel, parent, len(parent.interface_items))
    panels[path] = panel
    panel_order.append(path)
    return panel


def sync_interface(tree, settings, previous=None, folders_closed=True):
    """Bring the group inputs in line with settings.parms.

    An input whose parameter keeps its name and socket type is kept with its identifier,
    so keyframes and drivers on it survive a rebuild (a multiparm whose count changes
    rebuilds the inputs). Folders become nested panels, in Houdini's order. `previous` maps
    parameter names to their socket identifiers in the last build. A ramp gets its node,
    kept across rebuilds since it holds the value; a button gets nothing."""
    interface = tree.interface
    previous = previous or {}
    items = list(interface.items_tree)
    sockets = {item.identifier: item for item in items if item.item_type == "SOCKET" and item.in_out == "INPUT"
               and item.socket_type != "NodeSocketGeometry"}
    old_panels = {_panel_path(item): item for item in items if item.item_type == "PANEL"}
    geometry = next(item for item in items if item.item_type == "SOCKET" and item.socket_type == "NodeSocketGeometry")
    root = geometry.parent

    _remove_usage_chain(tree, settings)
    panels = {(): root}
    panel_order = []
    chain = UsageChain(tree)
    order = {}
    kept = set()
    node_count = 0
    for parm in settings.parms:
        parm.socket = ""
        parm.node = ""
        if parm.kind == contract.RAMP:
            parm.node = ramps.ramp_node_for(tree, parm, node_count).name
            node_count += 1
            continue
        kind = kinds.kind_of(parm.kind)
        socket_type = kind.socket(parm) if kind.socket is not None else None
        if socket_type is None:
            continue
        path = tuple(parm.folder.split(FOLDER_SEPARATOR)) if parm.folder else ()
        panel = _panel_for(interface, path, panels, panel_order, old_panels, folders_closed)
        socket = sockets.get(previous.get(parm.name, ""))
        if socket is None or socket.socket_type != socket_type or socket.identifier in kept:
            socket = interface.new_socket(parm.label, in_out="INPUT", socket_type=socket_type,
                                          parent=panel if path else None)
        _configure_socket(socket, parm)
        kept.add(socket.identifier)
        order.setdefault(path, []).append(socket)
        parm.socket = socket.identifier
        chain.add(parm, socket, kind)
    chain.close()

    for identifier, socket in sockets.items():
        if identifier not in kept:
            interface.remove(socket)
    _put_in_houdini_order(interface, root, panels, panel_order, order)


def _remove_usage_chain(tree, settings):
    """Remove the nodes of the usage chain, made again; the nodes holding ramps stay (they
    hold the values)."""
    held = {ramps.node_name(parm) for parm in settings.parms if parm.kind == contract.RAMP}
    for node in list(tree.nodes):
        if node.bl_idname not in ADDON_NODES and node.name != STREAMS_NODE and node.name not in held:
            tree.nodes.remove(node)


def _put_in_houdini_order(interface, root, panels, panel_order, order):
    """Houdini's order: the inputs of each panel (`order`: path -> sockets), then its
    sub-panels. At the root, the Geometry sockets come first. Moving everything in place
    also takes the kept inputs out of the panels left unused, which are then removed."""
    for path, container in panels.items():
        base = 0 if path else sum(1 for item in container.interface_items
                                  if item.item_type == "SOCKET" and item.socket_type == "NodeSocketGeometry")
        members = order.get(path, []) + [panels[child] for child in panel_order if child[:-1] == path]
        for offset, item in enumerate(members):
            if item.parent != container or item.position != base + offset:
                interface.move_to_parent(item, container, base + offset)
    used = {panel.identifier for panel in panels.values() if panel is not root}
    unused = [item for item in interface.items_tree if item.item_type == "PANEL" and item.identifier not in used]
    for panel in sorted(unused, key=lambda item: len(_panel_path(item)), reverse=True):
        interface.remove(panel, move_content_to_parent=False)


def apply_houdini_interface(tree, on=None):
    """Decide who draws the inputs of a Houdini modifier: the extension's panel, in Houdini's
    order, with the Houdini interface on (`on`, the preference when not given); Blender
    otherwise, the preference off or the extension disabled, for the inputs Houdini does not
    hide. Only what differs is written: each change makes the depsgraph evaluate the
    modifiers again, which reads input 1 once more."""
    if on is None:
        on = houdini_interface_on()
    settings = tree.houdini_bridge
    sockets = {item.identifier: item for item in tree.interface.items_tree
               if item.item_type == "SOCKET" and item.in_out == "INPUT"}
    for parm in settings.parms:
        socket = sockets.get(parm.socket)
        if socket is not None:
            hidden = _hidden_in_modifier(parm, on)
            if socket.hide_in_modifier != hidden:
                socket.hide_in_modifier = hidden
    for _, modifier in targets.modifier_users(tree):
        apply_houdini_interface_to_modifier(modifier, on)


def apply_houdini_interface_to_modifier(modifier, on=None):
    """The part of apply_houdini_interface that lives on a modifier: its node group selector
    and its Manage panel, hidden when the Houdini interface is on."""
    if on is None:
        on = houdini_interface_on()
    if modifier.show_group_selector == on:
        modifier.show_group_selector = not on
    if modifier.show_manage_panel == on:
        modifier.show_manage_panel = not on


def apply_houdini_interface_to_all(on=None):
    """Every Houdini modifier of the open file (the preference changed, or the extension is
    enabled or disabled)."""
    for tree in targets.houdini_trees():
        apply_houdini_interface(tree, on)
