"""Groups of points, edges or faces, managed in the Houdini tab.

A group is a plain boolean attribute of the mesh, named like the group, on the
POINT, EDGE or FACE domain: it needs nothing but Blender, and it goes to
Houdini as a point, edge or primitive group. The list on the mesh only adds
what the attribute cannot hold: a color, and whether the group is shown.

The groups a Houdini modifier makes live on its result, not on the mesh: they
have their own list on the modifier, refreshed by every cook, and the viewport
shows those of the active modifier while its parameters are tuned. They join
the mesh's list when the modifier is applied.
"""

import colorsys
import hashlib

import bmesh
import bpy
import gpu
import numpy as np
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty, IntProperty, StringProperty
from gpu_extras.batch import batch_for_shader

from ..core import events, feedback, targets
from ..core.properties import DOMAIN_LABELS

DOMAIN_ITEMS = [
    ("POINT", "Points", "Point group", "VERTEXSEL", 0),
    ("EDGE", "Edges", "Edge group", "EDGESEL", 1),
    ("FACE", "Faces", "Face group", "FACESEL", 2),
]
DOMAIN_ICONS = {"POINT": "VERTEXSEL", "EDGE": "EDGESEL", "FACE": "FACESEL"}
# Both keyed by mesh pointer, emptied by forget_meshes() when a file is loaded and after an
# undo or a redo, when an address may come to belong to another mesh.
_versions = {}  # mesh pointer -> counter, bumped on every geometry update
LIFT = 0.0015  # preview offset along the normals, relative to the object size
# Sent to Houdini so that an HDA can read the edges marked sharp, but not a group anyone made.
UNLISTED = {"sharp_edge"}
_cache = {}  # (mesh pointer, group attribute) -> (what it was drawn from, its batches)


def group_color(name):
    """A stable, saturated color per group name."""
    hue = int(hashlib.md5(name.encode("utf-8")).hexdigest()[:6], 16) / 0xFFFFFF
    return colorsys.hsv_to_rgb(hue, 0.75, 1.0)


def listed_color(index):
    """Colors for the rows of a list: the golden ratio keeps neighbours far apart,
    which a hash of the names does not."""
    return colorsys.hsv_to_rgb((0.08 + index * 0.618034) % 1.0, 0.75, 1.0)


def _bm_items(bm, domain):
    return {"POINT": bm.verts, "EDGE": bm.edges, "FACE": bm.faces}[domain]


def _bm_layer(bm, group, create=False):
    layers = _bm_items(bm, group.domain).layers.bool
    layer = layers.get(group.attribute)
    if layer is None and create:
        layer = layers.new(group.attribute)
    return layer


def _on_rename(self, context):
    """Renaming a group renames its attribute."""
    mesh = self.id_data
    new_name = self.name.strip() or "Group"
    if new_name == self.attribute:
        return
    taken = {a.name for a in mesh.attributes}
    while new_name in taken:
        new_name += "_"
    if self.attribute in mesh.attributes and not mesh.is_editmode:
        mesh.attributes[self.attribute].name = new_name
    elif mesh.is_editmode:
        bm = bmesh.from_edit_mesh(mesh)
        layer = _bm_layer(bm, self)
        if layer is not None:
            # bmesh cannot rename a layer: copy it into a new one.
            new_layer = _bm_items(bm, self.domain).layers.bool.new(new_name)
            for item in _bm_items(bm, self.domain):
                item[new_layer] = item[layer]
            _bm_items(bm, self.domain).layers.bool.remove(layer)
            bmesh.update_edit_mesh(mesh)
    self.attribute = new_name
    if self.name != new_name:
        self.name = new_name


def _redraw(self, context):
    feedback.redraw()


class HoudiniBridgeGroup(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", update=_on_rename)
    attribute: StringProperty(description="Name of the boolean attribute holding the group")
    domain: EnumProperty(name="Elements", items=DOMAIN_ITEMS)
    color: FloatVectorProperty(name="Color", subtype="COLOR", size=3, min=0.0, max=1.0, update=_redraw)
    show: BoolProperty(name="Show", description="Show the group in the viewport", default=True, update=_redraw)


# --- operators ------------------------------------------------------------------------


def _active_mesh(context):
    obj = context.active_object
    return obj.data if obj is not None and obj.type == "MESH" else None


def _active_group(context):
    mesh = _active_mesh(context)
    if mesh is None or not 0 <= mesh.houdini_bridge_group_index < len(mesh.houdini_bridge_groups):
        return None
    return mesh.houdini_bridge_groups[mesh.houdini_bridge_group_index]


def _select_domain(context):
    """The element type to group: the select mode, faces first."""
    vertices, edges, faces = context.tool_settings.mesh_select_mode
    return "FACE" if faces else "EDGE" if edges else "POINT"


class _EditModeOperator:
    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and _active_mesh(context) is not None


class HOUDINI_BRIDGE_OT_group_new(_EditModeOperator, bpy.types.Operator):
    """New group from the selected elements (faces, edges or points, as in the select mode)"""

    bl_idname = "houdini_bridge.group_new"
    bl_label = "New Group"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="Group")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "name")
        domain = _select_domain(context)
        row = layout.row()
        row.active = False
        row.label(text="A group of %s (the select mode)" % DOMAIN_LABELS[domain], icon=DOMAIN_ICONS[domain])

    def execute(self, context):
        mesh = _active_mesh(context)
        taken = {a.name for a in mesh.attributes} | {g.attribute for g in mesh.houdini_bridge_groups}
        name = self.name.strip() or "Group"
        while name in taken:
            name += "_"
        group = mesh.houdini_bridge_groups.add()
        group.attribute = name
        group.domain = _select_domain(context)
        group.name = name
        group.color = group_color(name)
        mesh.houdini_bridge_group_index = len(mesh.houdini_bridge_groups) - 1
        bm = bmesh.from_edit_mesh(mesh)
        layer = _bm_layer(bm, group, create=True)
        for item in _bm_items(bm, group.domain):
            item[layer] = item.select
        bmesh.update_edit_mesh(mesh)
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_group_edit(_EditModeOperator, bpy.types.Operator):
    """Add the selection to the active group, remove it, select or deselect the group"""

    bl_idname = "houdini_bridge.group_edit"
    bl_label = "Edit Group"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=[
        ("ASSIGN", "Assign", "Add the selected elements to the group"),
        ("REMOVE", "Remove", "Remove the selected elements from the group"),
        ("SELECT", "Select", "Select the elements of the group"),
        ("DESELECT", "Deselect", "Deselect the elements of the group"),
    ])

    def execute(self, context):
        group = _active_group(context)
        if group is None:
            return {"CANCELLED"}
        mesh = _active_mesh(context)
        bm = bmesh.from_edit_mesh(mesh)
        layer = _bm_layer(bm, group, create=self.action == "ASSIGN")
        if layer is None:
            return {"CANCELLED"}
        items = _bm_items(bm, group.domain)
        if self.action in ("ASSIGN", "REMOVE"):
            value = self.action == "ASSIGN"
            for item in items:
                if item.select:
                    item[layer] = value
        else:
            if self.action == "SELECT":
                # Show the group's elements in their own select mode.
                context.tool_settings.mesh_select_mode = (
                    group.domain == "POINT", group.domain == "EDGE", group.domain == "FACE")
            value = self.action == "SELECT"
            for item in items:
                if item[layer]:
                    item.select_set(value)
            bm.select_flush_mode()
        bmesh.update_edit_mesh(mesh)
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_group_delete(bpy.types.Operator):
    """Delete the active group (the elements stay)"""

    bl_idname = "houdini_bridge.group_delete"
    bl_label = "Delete Group"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_group(context) is not None

    def execute(self, context):
        mesh = _active_mesh(context)
        group = _active_group(context)
        if mesh.is_editmode:
            bm = bmesh.from_edit_mesh(mesh)
            layer = _bm_layer(bm, group)
            if layer is not None:
                _bm_items(bm, group.domain).layers.bool.remove(layer)
                bmesh.update_edit_mesh(mesh)
        elif group.attribute in mesh.attributes:
            mesh.attributes.remove(mesh.attributes[group.attribute])
        mesh.houdini_bridge_groups.remove(mesh.houdini_bridge_group_index)
        mesh.houdini_bridge_group_index = max(0, mesh.houdini_bridge_group_index - 1)
        return {"FINISHED"}


def adopt_groups(mesh, outputs):
    """After applying a Houdini modifier: its groups, now attributes of the mesh, join the list."""
    known = {group.attribute for group in mesh.houdini_bridge_groups}
    added = 0
    for output in outputs:
        attribute = mesh.attributes.get(output["name"])
        if output["kind"] != "group" or attribute is None or output["name"] in known:
            continue
        if attribute.domain not in ("POINT", "EDGE", "FACE") or attribute.data_type != "BOOLEAN":
            continue
        group = mesh.houdini_bridge_groups.add()
        group.attribute = output["name"]
        group.domain = attribute.domain
        group.name = output["name"]
        group.color = group_color(output.get("source", output["name"]))
        added += 1
    return added


def _on_modifier_applied(obj, outputs, summary):
    if obj.type == "MESH":
        summary["groups"] = adopt_groups(obj.data, outputs)


def sync_result_groups(target, outputs):
    """After a cook: the list of the result's groups, keeping colors and visibility.

    A group of the mesh that passes through stays in the mesh's list only."""
    settings = target.settings
    own = {group.attribute for group in getattr(target.owner.data, "houdini_bridge_groups", ())}
    wanted = {output["name"]: output for output in outputs
              if output["kind"] == "group" and output["name"] not in own
              and output.get("source") not in UNLISTED}
    entries = settings.result_groups
    for index in reversed(range(len(entries))):
        if entries[index].attribute not in wanted:
            entries.remove(index)
    known = {entry.attribute: entry for entry in entries}
    for name, output in wanted.items():
        entry = known.get(name)
        if entry is None:
            entry = entries.add()
            entry.attribute = name
            entry.name = output.get("source", name)
            entry.color = _free_color(entries)
        entry.domain = output["domain"]
        entry.count = output.get("count", 0)
    settings.result_group_index = min(settings.result_group_index, max(len(entries) - 1, 0))


def _free_color(entries):
    """The first list color no row uses: groups come and go as they empty and fill."""
    used = [entry.color[:] for entry in entries]
    index = 0
    while any(np.allclose(color, listed_color(index), atol=0.01) for color in used):
        index += 1
    return listed_color(index)


def active_result(obj):
    """The node group of the object's active modifier, when it is a Houdini one with a result."""
    modifier = obj.modifiers.active
    tree = targets.houdini_tree(modifier)
    if tree is not None and tree.houdini_bridge.holder is not None and modifier.show_viewport:
        return tree
    return None


def legend(obj):
    """What the shown groups of the active modifier hold."""
    tree = active_result(obj)
    if tree is None or tree.houdini_bridge.use_cache:
        return ""
    shown = [group for group in tree.houdini_bridge.result_groups if group.show]
    parts = ["%s · %d %s" % (group.name, group.count, DOMAIN_LABELS.get(group.domain, "")) for group in shown[:3]]
    if len(shown) > 3:
        parts.append("+%d" % (len(shown) - 3))
    return "   ".join(parts)


# --- sidebar --------------------------------------------------------------------------


def _draw_domain(row, text):
    """What a group holds (points, edges, faces), dimmed at the right of its row."""
    sub = row.row(align=True)
    sub.alignment = "RIGHT"
    sub.active = False
    sub.label(text=text)


class HOUDINI_BRIDGE_UL_result_groups(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "color", text="")
        row.label(text=item.name, icon=DOMAIN_ICONS.get(item.domain, "DOT"))
        _draw_domain(row, "%d %s" % (item.count, DOMAIN_LABELS.get(item.domain, "")))
        row.prop(item, "show", text="", emboss=False, icon="HIDE_OFF" if item.show else "HIDE_ON")


class HOUDINI_BRIDGE_UL_groups(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "color", text="")
        row.prop(item, "name", text="", emboss=False, icon=DOMAIN_ICONS.get(item.domain, "DOT"))
        _draw_domain(row, DOMAIN_LABELS.get(item.domain, ""))
        obj = context.active_object
        stage_mesh = preview_stage(obj)[0] if obj is not None else data
        if item.attribute not in stage_mesh.attributes and not stage_mesh.is_editmode:
            # Not in the previewed stage: the Houdini modifier dropped or renamed it.
            row.label(text="", icon="ERROR")
        row.prop(item, "show", text="", emboss=False, icon="HIDE_OFF" if item.show else "HIDE_ON")


def can_group(context):
    """Whether the Groups tab has something to work on: an active mesh."""
    return _active_mesh(context) is not None


def draw_tab(layout, context):
    """The Groups tab of the sidebar: the groups of the active mesh, and those of its Houdini result."""
    mesh = _active_mesh(context)
    if mesh is None:
        row = layout.row()
        row.enabled = False
        row.label(text="Select a mesh to group its elements", icon="INFO")
        return
    _, stage, tree = preview_stage(context.active_object)
    row = layout.row()
    row.enabled = False
    row.label(text="Shown on %s" % stage, icon="MODIFIER" if tree is not None else "MESH_DATA")
    row = layout.row()
    row.template_list("HOUDINI_BRIDGE_UL_groups", "", mesh, "houdini_bridge_groups",
                      mesh, "houdini_bridge_group_index", rows=3 if len(mesh.houdini_bridge_groups) else 1)
    side = row.column(align=True)
    side.operator("houdini_bridge.group_new", text="", icon="ADD")
    side.operator("houdini_bridge.group_delete", text="", icon="REMOVE")
    if context.mode == "EDIT_MESH":
        row = layout.row(align=True)
        row.operator("houdini_bridge.group_edit", text="Assign").action = "ASSIGN"
        row.operator("houdini_bridge.group_edit", text="Remove").action = "REMOVE"
        row = layout.row(align=True)
        row.operator("houdini_bridge.group_edit", text="Select").action = "SELECT"
        row.operator("houdini_bridge.group_edit", text="Deselect").action = "DESELECT"
    elif not len(mesh.houdini_bridge_groups):
        layout.label(text="Enter Edit Mode to group a selection", icon="INFO")

    result = active_result(context.active_object)
    if result is not None:
        settings = result.houdini_bridge
        layout.label(text="Made by %s" % (settings.label or result.name), icon="MODIFIER")
        if len(settings.result_groups):
            layout.template_list("HOUDINI_BRIDGE_UL_result_groups", "", settings, "result_groups",
                                 settings, "result_group_index", rows=2)
        else:
            row = layout.row()
            row.enabled = False
            row.label(text="No group in this result")


# --- viewport -------------------------------------------------------------------------


def _mask_and_geometry(obj, group, mesh):
    """(lifted positions, member triangles, edges or points) in local space, or None.

    `mesh` is the object's own mesh, read from bmesh in Edit Mode, or a Houdini result."""
    if mesh == obj.data and mesh.is_editmode:
        bm = bmesh.from_edit_mesh(mesh)
        layer = _bm_layer(bm, group)
        if layer is None:
            return None
        bm.verts.index_update()
        coordinates = np.array([vertex.co[:] for vertex in bm.verts], np.float32).reshape(-1, 3)
        normals = np.array([vertex.normal[:] for vertex in bm.verts], np.float32).reshape(-1, 3)
        size = float(np.linalg.norm(coordinates.max(0) - coordinates.min(0))) if len(coordinates) else 0.0
        # Lifted along the normals, so the preview does not fight with the surface.
        positions = coordinates + normals * LIFT * size
        if group.domain == "FACE":
            triangles = [tuple(loop.vert.index for loop in triangle)
                         for triangle in bm.calc_loop_triangles() if triangle[0].face[layer]]
            return positions, np.array(triangles, np.int32).reshape(-1, 3)
        if group.domain == "EDGE":
            edges = [(edge.verts[0].index, edge.verts[1].index) for edge in bm.edges if edge[layer]]
            return positions, np.array(edges, np.int32).reshape(-1, 2)
        points = [vertex.index for vertex in bm.verts if vertex[layer]]
        return positions, np.array(points, np.int32)
    attribute = mesh.attributes.get(group.attribute)
    if attribute is None or attribute.data_type != "BOOLEAN":
        return None
    mask = np.empty(len(attribute.data), bool)
    attribute.data.foreach_get("value", mask)
    positions = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", positions)
    positions = positions.reshape(-1, 3)
    normals = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertex_normals.foreach_get("vector", normals)
    if len(positions):
        size = float(np.linalg.norm(positions.max(0) - positions.min(0)))
        positions = positions + normals.reshape(-1, 3) * LIFT * size
    if group.domain == "FACE":
        mesh.calc_loop_triangles()
        loops = np.empty(len(mesh.loop_triangles) * 3, np.int32)
        mesh.loop_triangles.foreach_get("vertices", loops)
        polygons = np.empty(len(mesh.loop_triangles), np.int32)
        mesh.loop_triangles.foreach_get("polygon_index", polygons)
        return positions, loops.reshape(-1, 3)[mask[polygons]]
    if group.domain == "EDGE":
        edges = np.empty(len(mesh.edges) * 2, np.int32)
        mesh.edges.foreach_get("vertices", edges)
        return positions, edges.reshape(-1, 2)[mask]
    return positions, np.flatnonzero(mask).astype(np.int32)


def preview_stage(obj):
    """Where the groups are previewed: (mesh, label, Houdini node group or None).

    In Object Mode with a Houdini modifier active, on that modifier's result:
    the group as the modifier left it (a Remesh moves it). Otherwise on the
    object's own mesh, which Edit Mode edits."""
    tree = active_result(obj)
    if obj.mode != "EDIT" and tree is not None:
        return tree.houdini_bridge.holder.data, "%s result" % (tree.houdini_bridge.label or tree.name), tree
    return obj.data, "the mesh" if obj.mode != "EDIT" else "the mesh (Edit Mode)", None


def _batches(obj, group, mesh, tree):
    pointer = mesh.as_pointer()
    # A Houdini result changes at each cook: its status and timings change with it.
    cook = (tree.houdini_bridge.status, tree.houdini_bridge.timings) if tree is not None else None
    key = (pointer, group.attribute, group.domain, _versions.get(pointer, 0), cook)
    cached = _cache.get((obj.data.as_pointer(), group.attribute))
    if cached is not None and cached[0] == key:
        return cached[1]
    found = _mask_and_geometry(obj, group, mesh)
    batches = []
    if found is not None:
        positions, elements = found
        if len(elements):
            if group.domain == "FACE":
                shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                batches.append(("TRIS", shader, batch_for_shader(shader, "TRIS", {"pos": positions[elements.ravel()]})))
            elif group.domain == "EDGE":
                shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
                batches.append(("LINES", shader,
                                batch_for_shader(shader, "LINES", {"pos": positions[elements.ravel()]})))
            else:
                shader = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
                batches.append(("POINTS", shader, batch_for_shader(shader, "POINTS", {"pos": positions[elements]})))
    _cache[(obj.data.as_pointer(), group.attribute)] = (key, batches)
    return batches


def draw_groups():
    """POST_VIEW: the shown groups of the active object, in their color."""
    context = bpy.context
    obj = context.active_object
    if obj is None or obj.type != "MESH" or not obj.visible_get():
        return
    mesh, _, tree = preview_stage(obj)
    shown = [(group, mesh, tree) for group in obj.data.houdini_bridge_groups if group.show]
    result = active_result(obj)
    # Cached frames replace the live result, so its groups would be drawn on the wrong shape.
    if result is not None and not result.houdini_bridge.use_cache:
        holder = result.houdini_bridge.holder.data
        shown += [(group, holder, result) for group in result.houdini_bridge.result_groups if group.show]
    if not shown:
        return
    region = context.region
    gpu.matrix.push()
    gpu.matrix.multiply_matrix(obj.matrix_world)
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.point_size_set(7.0)
    for group, stage, stage_tree in shown:
        for kind, shader, batch in _batches(obj, group, stage, stage_tree):
            shader.bind()
            if kind == "LINES":
                shader.uniform_float("viewportSize", (region.width, region.height))
                shader.uniform_float("lineWidth", 3.0)
                shader.uniform_float("color", (*group.color, 1.0))
            elif kind == "TRIS":
                shader.uniform_float("color", (*group.color, 0.4))
            else:
                shader.uniform_float("color", (*group.color, 1.0))
            batch.draw(shader)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")
    gpu.matrix.pop()


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph):
    """An edited mesh gets a new version, so its groups are drawn again."""
    for update in depsgraph.updates:
        datablock = update.id
        if isinstance(datablock, bpy.types.Object) and update.is_updated_geometry and datablock.type == "MESH":
            pointer = datablock.original.data.as_pointer()
            _versions[pointer] = _versions.get(pointer, 0) + 1


def forget_meshes():
    """Drop the drawings and geometry versions kept per mesh pointer."""
    _versions.clear()
    _cache.clear()


@bpy.app.handlers.persistent
def _on_meshes_replaced(*_):
    """After a load, an undo or a redo, a pointer may name another mesh."""
    forget_meshes()


# (name of a bpy.app.handlers list, function), in the order they are added.
HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph_update),
    ("load_post", _on_meshes_replaced),
    ("undo_post", _on_meshes_replaced),
    ("redo_post", _on_meshes_replaced),
)

CLASSES = (
    HoudiniBridgeGroup,
    HOUDINI_BRIDGE_OT_group_new,
    HOUDINI_BRIDGE_OT_group_edit,
    HOUDINI_BRIDGE_OT_group_delete,
    HOUDINI_BRIDGE_UL_groups,
    HOUDINI_BRIDGE_UL_result_groups,
)
_handles = []


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Mesh.houdini_bridge_groups = CollectionProperty(type=HoudiniBridgeGroup)
    bpy.types.Mesh.houdini_bridge_group_index = IntProperty()
    for name, function in HANDLERS:
        getattr(bpy.app.handlers, name).append(function)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw_groups, (), "WINDOW", "POST_VIEW"))
    events.result_applied.connect(sync_result_groups)
    events.modifier_applied.connect(_on_modifier_applied)


def unregister():
    events.modifier_applied.disconnect(_on_modifier_applied)
    events.result_applied.disconnect(sync_result_groups)
    while _handles:
        bpy.types.SpaceView3D.draw_handler_remove(_handles.pop(), "WINDOW")
    for name, function in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    forget_meshes()
    del bpy.types.Mesh.houdini_bridge_group_index
    del bpy.types.Mesh.houdini_bridge_groups
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
