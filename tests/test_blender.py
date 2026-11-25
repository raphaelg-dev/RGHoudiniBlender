"""End-to-end test inside Blender (installed extension), without UI.

    blender --background --factory-startup --python tests/test_blender.py
"""

import difflib
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import bmesh
import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
from support import HDA, ROOT, check, evaluated, face_normals, get_parm, hda, positions, set_parm  # noqa: E402

SNAPSHOT = os.path.join(ROOT, "tests", "snapshots", "registration.json")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "compat_0_17.blend")
FIXTURE_EXPECTED = os.path.join(ROOT, "tests", "fixtures", "compat_0_17.json")
# Who listens to each signal while the extension is enabled: once each, and nobody else.
LISTENERS = {
    "server_started": ["catalog._on_houdini_start"],
    "result_applied": ["groups.sync_result_groups", "template._on_result"],
    "modifier_released": ["cache.clear"],
    "setting_changed": ["cache._on_setting_changed"],
    "modifier_applied": ["groups._on_modifier_applied"],
    "shelf.refreshed": ["assets._on_refreshed"],
}


def test_modifier_mode(cook, modifiers, definitions):
    import bmesh

    print("modifier mode: rock applied to an existing object")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=2, location=(-4, 2, 0))
    base = bpy.context.active_object
    base.rotation_euler = (0.0, 0.0, 0.5)
    base_faces = len(base.data.polygons)
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    tree = modifier.node_group
    check(modifier.type == "NODES" and tree.houdini_bridge.enabled, "a Geometry Nodes modifier carries the HDA")
    check(tree.houdini_bridge.inputs[0].is_self, "input 1 is the object itself")
    cook.cook_now(tree, force=True)
    check(not tree.houdini_bridge.errors, "no error: %r" % tree.houdini_bridge.errors)
    faces, world = evaluated(base)
    check(len(base.data.polygons) == base_faces,
          "the object's own mesh is untouched (%d faces)" % len(base.data.polygons))
    check(faces == 4 * base_faces, "the modifier shows the cooked mesh (%d faces)" % faces)
    centre = world.mean(axis=0)
    check(abs(centre[0] + 4) < 0.1 and abs(centre[1] - 2) < 0.1,
          "result stays on the object (centre %.2f, %.2f)" % tuple(centre[:2]))
    check(tree.houdini_bridge.holder.users_collection == (), "the holder object is in no collection")

    print("a shader node group, which has no settings, is not a Houdini modifier")
    shader_group = bpy.data.node_groups.new("Not Houdini", "ShaderNodeTree")
    found = support.targets().houdini_trees()
    check(tree in found and shader_group not in found, "only the Houdini node groups are found")
    support.maintain()
    check(tree.houdini_bridge.enabled, "the upkeep walks past the shader group")
    bpy.data.node_groups.remove(shader_group)

    print("moving the object does not recook")
    bpy.context.view_layer.update()
    base.location.x += 3.0
    bpy.context.view_layer.update()
    check(support.peek_request(tree) is None, "nothing to send after a move")
    faces, world = evaluated(base)
    check(abs(world.mean(axis=0)[0] + 1) < 0.1, "the result follows the object (x %.2f)" % world.mean(axis=0)[0])

    print("editing the original mesh recooks, and only once")
    base.data.vertices[0].co.z += 1.0
    base.data.update()
    bpy.context.view_layer.update()
    check(support.is_requested(tree), "a change of the original mesh requests a cook")
    answer = cook.cook_now(tree)
    check(bool(answer), "the cook ran")
    bpy.context.view_layer.update()  # the holder changed, so the owner is updated again
    check(support.peek_request(tree) is None, "writing the result does not loop back into a cook")

    print("Edit Mode: live edits of the original mesh")
    bpy.context.view_layer.objects.active = base
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    base.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    edit_mesh = bmesh.from_edit_mesh(base.data)
    edit_mesh.verts.ensure_lookup_table()
    before_edit = positions(tree.houdini_bridge.holder.data)[:, 2].max()
    edit_mesh.verts[5].co.z += 2.0
    bmesh.update_edit_mesh(base.data)
    bpy.context.view_layer.update()
    check(support.is_requested(tree), "an Edit Mode change requests a cook")
    cook.cook_now(tree)
    highest = positions(tree.houdini_bridge.holder.data)[:, 2].max()
    check(highest > before_edit + 0.2,
          "the Edit Mode change reaches the result (highest z %.2f → %.2f)" % (before_edit, highest))
    bpy.context.view_layer.update()
    support.cancel_request(tree)
    bpy.context.view_layer.update()
    check(support.peek_request(tree) is None, "reading the edit mesh does not loop back into a cook")
    bpy.ops.object.mode_set(mode="OBJECT")

    print("stacking: a second HDA receives the first one's result")
    second = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    second_tree = second.node_group
    cook.cook_now(second_tree, force=True)  # the first cook builds the parameter list
    set_parm(second, second_tree.houdini_bridge, "subdivisions", [0])
    cook.cook_now(second_tree)
    first_result = len(tree.houdini_bridge.holder.data.polygons)
    faces, _ = evaluated(base)
    check(faces == first_result, "second modifier works on the first result (%d faces)" % faces)
    check([m.node_group for m in support.target(second_tree).modifiers_above()] == [tree],
          "its input 1 includes the modifier above")

    print("modifiers above are sent to Houdini (Subdivision)")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(8, 0, 0))
    smooth = bpy.context.active_object
    flat_faces = len(smooth.data.polygons)
    subdivision = smooth.modifiers.new("Subdivision", "SUBSURF")
    subdivision.levels = 1
    smooth_modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    smooth_tree = smooth_modifier.node_group
    cook.cook_now(smooth_tree, force=True)
    holder_faces = len(smooth_tree.houdini_bridge.holder.data.polygons)
    check(holder_faces == flat_faces * 4 * 4,
          "level 1 below a 1-subdivision rock: %d faces (expected %d)" % (holder_faces, flat_faces * 16))
    check(len(smooth.data.polygons) == flat_faces, "the object's own mesh is untouched")
    check(all(m.show_viewport for m in smooth.modifiers), "every modifier is shown again after reading")
    bpy.context.view_layer.update()
    subdivision.levels = 2
    bpy.context.view_layer.update()
    check(support.is_requested(smooth_tree), "changing the Subdivision level requests a cook")
    cook.cook_now(smooth_tree)
    holder_faces = len(smooth_tree.houdini_bridge.holder.data.polygons)
    check(holder_faces == flat_faces * 16 * 4, "level 2: %d faces (expected %d)" % (holder_faces, flat_faces * 64))
    bpy.context.view_layer.update()
    bpy.context.view_layer.update()
    support.cancel_request(smooth_tree)
    check(support.peek_request(smooth_tree) is None, "reading the evaluated stack does not loop back into a cook")
    faces, _ = evaluated(smooth)
    check(faces == holder_faces, "the object shows the result (%d faces)" % faces)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    base.select_set(True)
    bpy.context.view_layer.objects.active = base

    print("duplicating the object gives it its own modifiers")
    bpy.ops.object.duplicate()
    copy = bpy.context.active_object
    support.maintain()
    copy_trees = [m.node_group for m in copy.modifiers if m.type == "NODES"]
    check(len(copy_trees) == 2 and not set(copy_trees) & {tree, second_tree}, "the copy has its own node groups")
    check(copy_trees[0].houdini_bridge.holder not in (tree.houdini_bridge.holder, second_tree.houdini_bridge.holder),
          "and its own holders")

    print("removing and applying")
    holder_name = second_tree.houdini_bridge.holder.name
    support.api().remove_modifier(second_tree)
    check(holder_name not in bpy.data.objects, "removing the modifier deletes its holder")
    faces, _ = evaluated(base)
    check(faces == first_result, "the first modifier still shows its result")
    bpy.context.view_layer.objects.active = base
    bpy.ops.houdini_bridge.apply(tree=tree.name)
    check(len(base.modifiers) == 0 and len(base.data.polygons) == first_result,
          "applying writes the result into the mesh")
    check(not tree.houdini_bridge.enabled, "the applied modifier is released")


def test_groups(cook, modifiers, definitions):
    print("groups and attributes, both ways")
    import json

    visualize = support.visualize()
    properties = support.properties()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 12, 0))
    grid = bpy.context.active_object
    mesh = grid.data
    top_row = mesh.attributes.new("top_row", "BOOLEAN", "FACE")
    top_row.data.foreach_set("value", np.array([i < 4 for i in range(len(mesh.polygons))]))
    sharp = mesh.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    sharp.data.foreach_set("value", np.array([i < 3 for i in range(len(mesh.edges))]))
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::groups"], "MODIFIER")
    tree = modifier.node_group
    settings = tree.houdini_bridge
    cook.cook_now(tree, force=True)
    group_parm = settings.parms["group"]
    check(group_parm.kind == "string" and "top_row" in json.loads(group_parm.menu_json),
          "the Group field suggests the input groups %s" % group_parm.menu_json)
    set_parm(modifier, settings, "group", "top_row")
    set_parm(modifier, settings, "distance", [0.5])
    cook.cook_now(tree)
    check(not settings.errors, "no error: %r" % settings.errors)
    result = settings.holder.data
    found = {(a.name, a.domain, a.data_type) for a in result.attributes if not a.name.startswith(".")}
    expected = {("height", "POINT", "FLOAT"), ("id", "POINT", "INT"), ("facing", "FACE", "FLOAT_VECTOR"),
                ("high", "POINT", "BOOLEAN"), ("right", "FACE", "BOOLEAN"), ("right_edges", "EDGE", "BOOLEAN"),
                ("top_row", "FACE", "BOOLEAN")}
    check(expected <= found, "Houdini attributes and groups on the result, missing %s" % sorted(expected - found))
    check(("houdini_sharp_edge", "EDGE", "BOOLEAN") in found,
          "a Blender name is not overwritten (sharp_edge → houdini_sharp_edge)")
    high = result.attributes["high"]
    raised = [result.vertices[i].co.z for i, item in enumerate(high.data) if item.value]
    check(raised and min(raised) > 0.05, "the 'high' group holds the points the input group raised (%d)" % len(raised))
    facing = np.array([item.vector[:] for item in result.attributes["facing"].data])
    check(np.median(np.abs(facing[:, 2])) > 0.9, "vector attributes change axes like P (facing along Z on a flat grid)")
    right_edges = sum(item.value for item in result.attributes["right_edges"].data)
    check(right_edges > 0, "edge group mapped onto Blender edges (%d)" % right_edges)
    names = [o["name"] for o in properties.outputs(settings)]
    check({"high", "right", "right_edges", "height", "facing"} <= set(names), "outputs listed for Visualize")
    for name in ("height", "facing"):
        output = next(o for o in properties.outputs(settings) if o["name"] == name)
        batches, legend = visualize.build(result, output)
        check(bool(batches) and bool(legend), "visualizer for %s: %s" % (name, legend))
    groups = support.groups()
    made = {group.name: group for group in settings.result_groups}
    check({"high", "right", "right_edges"} <= set(made),
          "the result's groups are listed before Apply: %s" % sorted(made))
    check("sharp_edge" not in made, "Blender's own sharp edges are not a group")
    for name, group in made.items():
        check(group.show and group.count > 0, "%s is shown, with its count (%d)" % (name, group.count))
        elements = support.group_preview(grid, group, result)[1]
        check(len(elements) >= group.count, "%s is drawn from the result (%d elements)" % (name, len(elements)))
    check(groups.active_result(grid) == tree, "the active modifier's result is the one previewed")
    check("high · 10 points" in groups.legend(grid), "the viewport badge gives the counts: %s" % groups.legend(grid))
    made["right"].show = False
    high_before = made["high"].count
    set_parm(modifier, settings, "distance", [0.0])
    cook.cook_now(tree)
    entries = {group.name: group for group in settings.result_groups}
    check("high" not in entries or entries["high"].count != high_before, "the count follows the parameters")
    check(not entries["right"].show, "hiding a group survives the next cook")
    entries["right"].show = True
    mine = mesh.houdini_bridge_groups.add()
    mine.attribute = "top_row"
    mine.domain = "FACE"
    cook.cook_now(tree, force=True)
    check("top_row" not in {group.name for group in settings.result_groups},
          "a group of the mesh stays in the mesh's list")
    mesh.houdini_bridge_groups.remove(len(mesh.houdini_bridge_groups) - 1)
    check("high" not in {a.name for a in result.attributes} or not any(i.value for i in result.attributes["high"].data),
          "a group that disappears is removed or emptied")


def test_group_chain(cook, modifiers, definitions):
    print("stacked HDAs: a group made by the first one reaches the second one")
    import json

    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=2, location=(0, 24, 0))
    first = modifiers.place_hda(bpy.context, HDA, definitions["bridge::groups"], "MODIFIER")
    cook.cook_now(first.node_group, force=True)
    set_parm(first, first.node_group.houdini_bridge, "distance", [0.0])
    cook.cook_now(first.node_group)
    made = {o["name"] for o in support.properties().outputs(first.node_group.houdini_bridge)}
    check({"right", "right_edges"} <= made, "the first HDA makes groups %s" % sorted(made))

    second = modifiers.place_hda(bpy.context, HDA, definitions["bridge::groups"], "MODIFIER")
    tree = second.node_group
    cook.cook_now(tree, force=True)
    offered = json.loads(tree.houdini_bridge.parms["group"].menu_json)
    check("right" in offered, "the second HDA is offered the groups of the first one %s" % offered)
    set_parm(second, tree.houdini_bridge, "group", "right")
    set_parm(second, tree.houdini_bridge, "distance", [0.5])
    cook.cook_now(tree)
    result = tree.houdini_bridge.holder.data
    heights = np.array([v.co.z for v in result.vertices])
    xs = np.array([v.co.x for v in result.vertices])
    check(heights[xs > 0.05].max() > 0.3 and abs(heights[xs < -0.05]).max() < 1e-4,
          "the second HDA moved only the faces of the first one's group")
    group = next((g for g in tree.houdini_bridge.result_groups if g.name == "right"), None)
    check(group is not None and group.show and group.count > 0, "the group is listed on the second modifier and shown")


def test_group_preview(cook, modifiers):
    print("group preview follows the active Houdini modifier (native Remesh)")
    groups = support.groups()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, location=(0, 30, 0))
    sphere = bpy.context.active_object
    mesh = sphere.data
    cap = mesh.attributes.new("cap", "BOOLEAN", "FACE")
    centers = np.empty(len(mesh.polygons) * 3, np.float32)
    mesh.polygons.foreach_get("center", centers)
    cap.data.foreach_set("value", centers.reshape(-1, 3)[:, 2] > 0.5)
    group = mesh.houdini_bridge_groups.add()
    group.attribute = "cap"
    group.domain = "FACE"
    group.name = "cap"
    remesh = modifiers.place_hda(bpy.context, "", {"type": "remesh::2.0", "label": "Remesh", "max_inputs": 1},
                                 "MODIFIER")
    cook.cook_now(remesh.node_group, force=True)
    result = remesh.node_group.houdini_bridge.holder.data
    check(not remesh.node_group.houdini_bridge.errors and "cap" in result.attributes,
          "Remesh keeps the face group (%d faces after, %d before)" % (len(result.polygons), len(mesh.polygons)))
    sphere.modifiers.active = remesh
    stage_mesh, label, _ = groups.preview_stage(sphere)
    check(stage_mesh == result, "with Remesh active, the group is previewed on its result (%s)" % label)
    preview_points, triangles = support.group_preview(sphere, group, stage_mesh)
    remeshed_faces = sum(item.value for item in result.attributes["cap"].data)
    check(len(preview_points) == len(result.vertices) and len(triangles) >= remeshed_faces > 0,
          "the preview is built from the remeshed group (%d faces of the result)" % remeshed_faces)
    sphere.modifiers.active = None
    stage_mesh, label, _ = groups.preview_stage(sphere)
    check(stage_mesh == mesh, "without an active Houdini modifier, on the mesh itself (%s)" % label)


def test_group_panel(cook, modifiers, definitions):
    print("Groups panel: make a group, send it to Houdini, get groups back")
    import json

    import bmesh

    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 18, 0))
    grid = bpy.context.active_object
    mesh = grid.data
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(face.index < 4)
    bmesh.update_edit_mesh(mesh)
    bpy.ops.houdini_bridge.group_new(name="Front Row")
    group = mesh.houdini_bridge_groups[0]
    check(group.domain == "FACE" and group.attribute == "Front Row", "a face group from the face selection")
    for face in bm.faces:
        face.select_set(face.index == 5)
    bmesh.update_edit_mesh(mesh)
    bpy.ops.houdini_bridge.group_edit(action="ASSIGN")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.houdini_bridge.group_edit(action="SELECT")
    bm = bmesh.from_edit_mesh(mesh)
    selected = sorted(face.index for face in bm.faces if face.select)
    check(selected == [0, 1, 2, 3, 5], "Assign then Select selects the group again %s" % selected)
    group.name = "front"
    bpy.ops.object.mode_set(mode="OBJECT")
    attribute = mesh.attributes.get("front")
    check(attribute is not None and attribute.data_type == "BOOLEAN" and "Front Row" not in mesh.attributes,
          "renaming the group renames its attribute")

    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::groups"], "MODIFIER")
    tree = modifier.node_group
    cook.cook_now(tree, force=True)
    check("front" in json.loads(tree.houdini_bridge.parms["group"].menu_json), "the group is offered to the HDA")
    set_parm(modifier, tree.houdini_bridge, "group", "front")
    set_parm(modifier, tree.houdini_bridge, "distance", [0.5])
    cook.cook_now(tree)
    high = tree.houdini_bridge.holder.data.attributes.get("high")
    check(high is not None and any(item.value for item in high.data), "Houdini worked on the group")
    bpy.context.view_layer.objects.active = grid
    bpy.ops.houdini_bridge.apply(tree=tree.name)
    names = {g.name: g.domain for g in mesh.houdini_bridge_groups}
    check({"front": "FACE", "high": "POINT", "right": "FACE", "right_edges": "EDGE"}.items() <= names.items(),
          "after Apply, the Houdini groups join the Groups panel %s" % names)
    bpy.ops.object.mode_set(mode="EDIT")
    mesh.houdini_bridge_group_index = list(names).index("right_edges")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.houdini_bridge.group_edit(action="SELECT")
    bm = bmesh.from_edit_mesh(mesh)
    check(any(edge.select for edge in bm.edges) and tuple(bpy.context.tool_settings.mesh_select_mode)
          == (False, True, False),
          "selecting an edge group switches to edge select mode")
    bpy.ops.houdini_bridge.group_delete()
    bpy.ops.object.mode_set(mode="OBJECT")
    check("right_edges" not in mesh.attributes and "right_edges" not in {g.name for g in mesh.houdini_bridge_groups},
          "deleting a group removes its attribute")


def attribute_values(mesh, name):
    attribute = mesh.attributes[name]
    field = {"FLOAT": "value", "INT": "value", "FLOAT2": "vector", "FLOAT_VECTOR": "vector",
             "FLOAT_COLOR": "color"}[attribute.data_type]
    size = {"FLOAT": 1, "INT": 1, "FLOAT2": 2, "FLOAT_VECTOR": 3, "FLOAT_COLOR": 4}[attribute.data_type]
    values = np.empty(len(attribute.data) * size, np.float32)
    attribute.data.foreach_get(field, values)
    return values.reshape(-1, size)


def test_time(cook, modifiers, definitions):
    print("time: an HDA reading $T follows the timeline")
    scene = bpy.context.scene
    scene.frame_set(1)
    bpy.ops.object.select_all(action="DESELECT")
    animated = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::animated"])
    tree = hda(animated)[1]
    settings = tree.houdini_bridge
    cook.cook_now(tree, force=True)
    check(not settings.errors and settings.time_dependent, "the HDA is seen as time dependent")
    first = positions(settings.holder.data).copy()
    scene.frame_set(25)
    check(support.is_requested(tree), "a frame change requests a cook")
    header, _ = support.peek_request(tree)
    check(header["frame"] == 25 and abs(header["fps"] - scene.render.fps / scene.render.fps_base) < 1e-6,
          "the request carries the frame and the frame rate (%s, %.2f)" % (header["frame"], header["fps"]))
    cook.cook_now(tree)
    later = positions(settings.holder.data).copy()
    check(not np.allclose(first, later), "the result changes with the frame")
    cook.cook_now(tree)
    check(support.peek_request(tree) is None, "nothing to send while the frame stays")
    scene.frame_set(1)
    cook.cook_now(tree)
    check(np.allclose(first, positions(settings.holder.data)), "back to frame 1, back to the first shape")

    print("time: a static HDA does not recook on frame change")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 36, 0))
    still = bpy.context.active_object
    rock = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    cook.cook_now(rock.node_group, force=True)
    check(not rock.node_group.houdini_bridge.time_dependent, "the rock is not time dependent")
    scene.frame_set(12)
    check(not support.is_requested(rock.node_group) and support.peek_request(rock.node_group) is None,
          "changing frames leaves it alone")

    print("time: an animated input recooks")
    wave = still.modifiers.new("Wave", "WAVE")
    wave.keyframe_insert("height", frame=1)
    wave.height = 1.5
    wave.keyframe_insert("height", frame=20)
    bpy.ops.object.modifier_move_to_index(modifier="Wave", index=0)
    scene.frame_set(1)
    check(support.animation().changes_shape(still, before=rock), "a keyframed Wave above makes input 1 animated")
    cook.cook_now(rock.node_group, force=True)
    before = positions(rock.node_group.houdini_bridge.holder.data).copy()
    scene.frame_set(20)
    check(0 in support.changed_inputs(rock.node_group), "the frame change marks input 1")
    cook.cook_now(rock.node_group)
    check(not np.allclose(before, positions(rock.node_group.houdini_bridge.holder.data)),
          "the modifier above, animated, reaches Houdini")
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 40, 0))
    mover = bpy.context.active_object
    mover.keyframe_insert("location", frame=1)
    mover.location.z += 2.0
    mover.keyframe_insert("location", frame=10)
    bpy.ops.object.select_all(action="DESELECT")
    mover.select_set(True)
    bpy.context.view_layer.objects.active = mover
    follower = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::rock"])
    follower_tree = hda(follower)[1]
    scene.frame_set(1)
    cook.cook_now(follower_tree, force=True)
    low = positions(follower_tree.houdini_bridge.holder.data)[:, 2].mean()
    scene.frame_set(10)
    check(support.is_requested(follower_tree) and 0 in support.changed_inputs(follower_tree),
          "a keyframed input object is marked on frame change")
    cook.cook_now(follower_tree)
    high = positions(follower_tree.houdini_bridge.holder.data)[:, 2].mean()
    check(high > low + 1.5, "the result follows the animated input (z %.2f → %.2f)" % (low, high))
    scene.frame_set(1)


def evaluated_local(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_object = obj.evaluated_get(depsgraph)
    mesh = evaluated_object.to_mesh()
    try:
        return positions(mesh).copy(), {m.name for m in mesh.materials if m}, {a.name for a in mesh.attributes}
    finally:
        evaluated_object.to_mesh_clear()


def render_frame(frame, path):
    scene = bpy.context.scene
    scene.frame_set(frame)
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(path, check_existing=False)
    pixels = np.array(image.pixels[:], np.float32)
    bpy.data.images.remove(image)
    return pixels


def realized(obj):
    """(face count, local positions) of everything an object shows, instances realized
    (a Realize Instances modifier is added at the end of its stack for one evaluation)."""
    tree = bpy.data.node_groups.get("test realize")
    if tree is None:
        tree = bpy.data.node_groups.new("test realize", "GeometryNodeTree")
        tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group_input, group_output = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
        realize = tree.nodes.new("GeometryNodeRealizeInstances")
        tree.links.new(group_input.outputs[0], realize.inputs[0])
        tree.links.new(realize.outputs[0], group_output.inputs[0])
    modifier = obj.modifiers.new("test realize", "NODES")
    modifier.node_group = tree
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        evaluated_object = obj.evaluated_get(depsgraph)
        mesh = evaluated_object.to_mesh()
        try:
            return len(mesh.polygons), positions(mesh)
        finally:
            evaluated_object.to_mesh_clear()
    finally:
        obj.modifiers.remove(modifier)


def same_points(a, b, tolerance=1e-4):
    """Same point sets, whatever their order."""
    from mathutils import kdtree

    if a.shape != b.shape:
        return False

    def covered(points, others):
        tree = kdtree.KDTree(len(others))
        for index, point in enumerate(others):
            tree.insert(point, index)
        tree.balance()
        return all(tree.find(point)[2] <= tolerance for point in points)

    return covered(a, b) and covered(b, a)


def stream_cook(cook, tree, mode):
    tree.houdini_bridge.output_mode = mode
    return cook.cook_now(tree, force=True)


def points_through(obj, name, make_nodes):
    """The points of `obj` evaluated through a node group of its own, added as its last
    modifier for one evaluation: `make_nodes(probe, geometry)` adds its nodes to the group and
    returns the socket the group outputs. One point at the origin when nothing comes out."""
    probe = bpy.data.node_groups.new(name, "GeometryNodeTree")
    probe.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    probe.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    output = make_nodes(probe, probe.nodes.new("NodeGroupInput").outputs[0])
    probe.links.new(output, probe.nodes.new("NodeGroupOutput").inputs[0])
    measure = obj.modifiers.new(name, "NODES")
    measure.node_group = probe
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    found = positions(mesh) if len(mesh.vertices) else np.zeros((1, 3))
    evaluated.to_mesh_clear()
    obj.modifiers.remove(measure)
    return found


def test_streams(cook, modifiers, definitions):
    """Each native stream from a test HDA, in the order the objects are made; the later steps
    apply and copy what the earlier ones made."""
    streams_registry()
    ground, scatter = streams_scatter(cook, modifiers, definitions)
    scatter_limits(scatter)
    streams_instances(cook, ground, scatter)
    streams_instances_update(cook, ground, scatter)
    cube, shatter = streams_fragments(cook, modifiers, definitions)
    lawn, grass = streams_curves(cook, modifiers, definitions)
    streams_points(cook, modifiers, definitions)
    box, fog = streams_volume(cook, modifiers, definitions)
    pig, convert_tree = streams_level_set(cook, modifiers)
    streams_apply(cook, modifiers, (box, fog), (lawn, grass), (cube, shatter))
    streams_apply_round_trip(cook, pig, convert_tree)
    limits_on_a_labs_tool(cook, modifiers)
    streams_apply_all(cook, modifiers)
    streams_duplicate_cache_remove(cook, ground, scatter)


def streams_registry():
    streams = support.streams()
    print("streams: the registry")
    contract = support.installed("contract")
    names = [stream.name for stream in streams.STREAMS]
    check(names == list(contract.STREAMS) and list(streams.STREAM_NAMES) == names,
          "the registry holds the streams of contract.STREAMS, in their order %s" % names)
    stored = bpy.types.GeometryNodeTree.bl_rna.properties["houdini_bridge"].fixed_type.properties
    missing = [name for stream in streams.STREAMS for name in (stream.holder, *stream.owned)
               if name not in stored or stored[name].type != "POINTER"]
    check(not missing and all(stored[stream.holder].fixed_type.identifier == "Object" for stream in streams.STREAMS),
          "each stream has its holder (an Object) and the properties it owns in HoudiniBridgeSettings %s" % missing)


def streams_scatter(cook, modifiers, definitions):
    """The scatter HDA on a grid, in Output Native. Returns (the grid, its modifier)."""
    streams = support.streams()
    print("streams: scatter as instances")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=3, location=(0, 90, 0))
    ground = bpy.context.active_object
    ground.rotation_euler = (0.3, 0.0, 0.7)
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::scatter"], "MODIFIER")
    tree = modifier.node_group
    settings = tree.houdini_bridge
    stream_cook(cook, tree, "NATIVE")
    check(not settings.errors, "scatter cooks: %r" % settings.errors)
    found = streams.summary(settings)
    check(found.get("instances") == 500 and found.get("shapes") == 3,
          "Output Native: 500 instances of 3 shapes (rock, box, quadric sphere) %s" % found)
    check(all([m.name for m in obj.data.materials] == ["bridge_instance"] for obj in settings.prototypes.objects),
          "every shape has its material, the quadric sphere too (set on the instance)")
    return ground, modifier


def scatter_limits(modifier):
    """The limits of the scatter's inputs, by the preference Parameter Limits."""
    tree = modifier.node_group
    settings = tree.houdini_bridge
    sockets = {item.name: item for item in tree.interface.items_tree if item.item_type == "SOCKET"}
    check(sockets["Count"].min_value == 1 and abs(sockets["Size"].min_value - 0.001) < 1e-6,
          "strict Houdini minimums become input limits (Count ≥ 1, Size ≥ 0.001)")
    check(sockets["Size Variation"].min_value == 0 and sockets["Size Variation"].max_value > 1e30
          and sockets["Count"].max_value > 100000,
          "by default the slider minimum is a limit too (Size Variation ≥ 0), the maximum stays open")
    preferences = support.preferences()
    preferences.parameter_limits = "STRICT"
    check(sockets["Size Variation"].min_value < -1e30 and sockets["Count"].min_value == 1,
          "Parameter Limits: Strict Bounds frees the slider minimum, keeps the strict one")
    preferences.parameter_limits = "RANGE"
    check(sockets["Size Variation"].max_value == 1 and sockets["Count"].max_value == 100000,
          "Parameter Limits: Slider Range holds both ends")
    preferences.parameter_limits = "MINIMUM"
    set_parm(modifier, settings, "count", [-5])
    support.poll_inputs()  # what the 10 Hz timer does
    raw = support.modifier_input(modifier, settings.parms["count"].socket).value
    check(raw == 1, "the field itself is reset to 1 (%s)" % raw)
    check(get_parm(modifier, settings.parms["count"]) == [1],
          "a negative count (script, driver) reaches Houdini as 1 (%s)" % (
        get_parm(modifier, settings.parms["count"]),))
    set_parm(modifier, settings, "count", [500])


def streams_instances(cook, ground, modifier):
    """The instances realized by Blender, compared with Houdini's own unpack (Output Mesh)."""
    tree = modifier.node_group
    settings = tree.houdini_bridge
    # Convert meshes quadric spheres its own way: the exact comparison leaves them out.
    set_parm(modifier, settings, "spheres", False)
    answer = stream_cook(cook, tree, "MESH")
    mesh_faces, mesh_points = realized(ground)
    check(mesh_faces > 0 and "instances" not in answer, "Output Mesh: the copies are realized (%d faces)" % mesh_faces)

    answer = stream_cook(cook, tree, "NATIVE")
    holder = settings.instances_holder
    check(holder is not None and holder.users_collection == () and len(holder.data.points) == 500,
          "the instances are a hidden point cloud of 500 points")
    check(len(settings.prototypes.objects) == 2, "one prototype object per shape")
    check(len(settings.holder.data.polygons) == 0, "nothing left in the mesh stream")
    native_faces, native_points = realized(ground)
    check(native_faces == mesh_faces and same_points(native_points, mesh_points),
          "realized instances = Houdini's own unpack (%d faces, %d points)" % (native_faces, len(native_points)))
    colors = holder.data.attributes.get("Cd")
    check(colors is not None and colors.data_type == "FLOAT_COLOR", "each instance keeps its color (Cd)")
    check("500 instances (2 shapes)" in settings.status, "status: %s" % settings.status)


def streams_instances_update(cook, ground, modifier):
    """A parameter change, which resends no shape, and the display of the instances in the viewport."""
    streams = support.streams()
    tree = modifier.node_group
    settings = tree.houdini_bridge
    holder = settings.instances_holder
    print("streams: a parameter change resends no shape")
    set_parm(modifier, settings, "count", [600])
    answer = cook.cook_now(tree)
    check(answer.get("instances", {}).get("count") == 600 and answer["instances"]["prototypes"] == [],
          "600 instances, the 2 shapes not sent again")
    check(len(holder.data.points) == 600 and len(settings.prototypes.objects) == 2, "the point cloud follows")
    native_faces, native_points = realized(ground)
    stream_cook(cook, tree, "MESH")
    mesh_faces, mesh_points = realized(ground)
    check(native_faces == mesh_faces and same_points(native_points, mesh_points), "still the same as the unpack")
    stream_cook(cook, tree, "NATIVE")

    print("streams: viewport display")
    proxy = holder.modifiers[streams.INSTANCER_MODIFIER].node_group.nodes["Points in Viewport"]
    check(proxy.inputs[1].default_value is False,
          "Auto below %d: instances in the viewport" % support.preference_defaults()["points_above"])
    settings.instance_display = "POINTS"
    check(proxy.inputs[1].default_value is True, "Points: one point per instance in the viewport")
    settings.instance_display = "AUTO"


def streams_fragments(cook, modifiers, definitions):
    streams = support.streams()
    print("streams: fragments")
    bpy.ops.mesh.primitive_cube_add(size=1, location=(4, 90, 0))
    cube = bpy.context.active_object
    shatter = modifiers.place_hda(bpy.context, HDA, definitions["bridge::shatter"], "MODIFIER").node_group
    stream_cook(cook, shatter, "MESH")
    mesh_faces, mesh_points = realized(cube)
    stream_cook(cook, shatter, "NATIVE")
    found = streams.summary(shatter.houdini_bridge)
    native_faces, native_points = realized(cube)
    check(found.get("instances") == 12 and found.get("shapes") == 12, "12 fragments, 12 shapes %s" % found)
    check(native_faces == mesh_faces and same_points(native_points, mesh_points), "fragments rebuilt exactly")
    shapes = shatter.houdini_bridge.prototypes.objects
    check(all(sorted(m.name for m in obj.data.materials) == ["bridge_inside", "bridge_outside"] for obj in shapes),
          "each fragment keeps its inside and outside materials")
    return cube, shatter


def streams_curves(cook, modifiers, definitions):
    print("streams: open curves")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 95, 0))
    lawn = bpy.context.active_object
    grass = modifiers.place_hda(bpy.context, HDA, definitions["bridge::grass"], "MODIFIER").node_group
    cook.cook_now(grass, force=True)
    curves = grass.houdini_bridge.curves_holder.data
    check(len(curves.curves) == 2000 and len(curves.points) == 2000 * 6, "2000 blades of 6 points as a Curves object")
    radius = np.empty(len(curves.points), np.float32)
    curves.attributes["radius"].data.foreach_get("value", radius)
    check(abs(radius.max() - 0.005) < 1e-4, "radius = width / 2 (%.4f)" % radius.max())
    roots = np.empty(len(curves.points) * 3, np.float32)
    curves.position_data.foreach_get("vector", roots)
    roots = roots.reshape(-1, 6, 3)[:, 0]
    check(np.abs(roots[:, 2]).max() < 1e-4 and np.abs(roots[:, :2]).max() <= 1.0001, "blades grow from the grid (Z up)")
    check("Cd" in curves.attributes and "blade" in curves.attributes, "point and curve attributes kept")
    check([m.name for m in curves.materials] == ["bridge_grass"], "the curves take their shop_materialpath")
    geometry = lawn.evaluated_get(bpy.context.evaluated_depsgraph_get()).evaluated_geometry()
    references = geometry.instance_references() if hasattr(geometry, "instance_references") else []
    check(any(getattr(ref, "curves", None) is not None or getattr(ref, "name", "") for ref in references)
          or len(references) > 0,
          "the modifier shows the curves (as an instance)")
    return lawn, grass


def streams_points(cook, modifiers, definitions):
    print("streams: loose points")
    bpy.ops.mesh.primitive_grid_add(size=2, location=(4, 95, 0))
    fireflies = modifiers.place_hda(bpy.context, HDA, definitions["bridge::fireflies"], "MODIFIER").node_group
    cook.cook_now(fireflies, force=True)
    cloud = fireflies.houdini_bridge.points_holder.data
    check(len(cloud.points) == 3000 and "Cd" in cloud.attributes and "age" in cloud.attributes,
          "3000 points with Cd and age as a point cloud")
    check([m.name for m in cloud.materials] == ["bridge_fireflies"], "the points take their shop_materialpath")
    check(fireflies.houdini_bridge.time_dependent, "fireflies read the time")


def streams_volume(cook, modifiers, definitions):
    print("streams: volume")
    bpy.ops.mesh.primitive_cube_add(size=1, location=(8, 95, 0))
    box = bpy.context.active_object
    # Off-centre in its own space, so a wrong axis shows in the bounds.
    for vertex in box.data.vertices:
        vertex.co += Vector((0.3, 2.0, 0.7))
    box.data.update()
    fog = modifiers.place_hda(bpy.context, HDA, definitions["bridge::fog"], "MODIFIER").node_group
    cook.cook_now(fog, force=True)
    set_parm(hda(box)[0], fog.houdini_bridge, "noise_frequency", [0.0])
    cook.cook_now(fog)
    # The volume's extent, measured on its surface.
    def volume_to_mesh(probe, source):
        to_mesh = probe.nodes.new("GeometryNodeVolumeToMesh")
        to_mesh.inputs["Threshold"].default_value = 0.1
        probe.links.new(source, to_mesh.inputs[0])
        return to_mesh.outputs[0]

    bounds = points_through(box, "test volume to mesh", volume_to_mesh)
    centre = (bounds.min(0) + bounds.max(0)) / 2
    size = bounds.max(0) - bounds.min(0)
    check(np.allclose(centre, (0.3, 2.0, 0.7), atol=0.06) and np.allclose(size, 1.0, atol=0.2),
          "the fog fills the cube, axes right (centre %s, size %s)" % (centre.round(2), size.round(2)))
    volume = fog.houdini_bridge.volume_holder.data
    volume.grids.load()
    check(os.path.exists(bpy.path.abspath(volume.filepath)) and [g.name for g in volume.grids] == ["density"],
          "a .vdb with a density grid read by a Volume object")
    first = volume.filepath
    set_parm(hda(bpy.context.active_object)[0], fog.houdini_bridge, "density", [2.0])
    cook.cook_now(fog)
    check(volume.filepath != first, "a new cook writes a new file (Blender reloads it)")
    return box, fog


def streams_level_set(cook, modifiers):
    """A level set from VDB from Polygons, and the Convert VDB below it, chained. Returns (the
    pig head, the Convert VDB's node group)."""
    streams = support.streams()
    print("streams: a level set stays a distance field (Pig Head, then VDB from Polygons below it)")
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    bpy.context.scene.cursor.location = (12, 95, 0)
    pig = modifiers.create_hda_object(bpy.context, "",
                                      {"type": "testgeometry_pighead", "label": "Pig Head", "max_inputs": 0})
    cook.cook_now(hda(pig)[1], force=True)
    _, pig_points = realized(pig)
    sdf = modifiers.create_hda_modifier(bpy.context, pig, "", {"type": "vdbfrompolygons", "label": "VDB from Polygons",
                                                                 "max_inputs": 2}).node_group
    cook.cook_now(sdf, force=True)
    sdf_volume = sdf.houdini_bridge.volume_holder.data
    check(not sdf.houdini_bridge.errors and streams.summary(sdf.houdini_bridge).get("volume") == ["surface"]
          and len(sdf.houdini_bridge.holder.data.polygons) == 0,
          "the level set comes back as a volume holding the 'surface' grid, no mesh")
    check(realized(pig)[0] == 0, "no polygons are made from it, not even in the viewport")
    check([m.name for m in sdf_volume.materials] == ["Houdini Level Set · surface"],
          "its default material renders the inside of the distance field")
    # Its zero level, rebuilt by Blender's own SDF nodes, wraps the pig head it received.
    def zero_level(probe, source):
        named = probe.nodes.new("GeometryNodeGetNamedGrid")
        named.inputs["Name"].default_value = "surface"
        to_mesh = probe.nodes.new("GeometryNodeGridToMesh")
        to_mesh.inputs["Threshold"].default_value = 0.0
        probe.links.new(source, named.inputs[0])
        probe.links.new(named.outputs["Grid"], to_mesh.inputs["Grid"])
        return to_mesh.outputs[0]

    surface_points = points_through(pig, "test sdf to mesh", zero_level)
    check(np.allclose(surface_points.min(0), pig_points.min(0), atol=0.1)
          and np.allclose(surface_points.max(0), pig_points.max(0), atol=0.1),
          "the distance field is the pig head it received, axes right (%s → %s)" % (
              surface_points.min(0).round(2), surface_points.max(0).round(2)))

    print("streams: a Houdini modifier below gets the VDB itself (Convert VDB to polygons)")
    back = modifiers.create_hda_modifier(bpy.context, pig, "", {"type": "convertvdb", "label": "Convert VDB",
                                                                  "max_inputs": 1})
    back_tree = back.node_group
    check(support.chained_above(back_tree) == sdf, "input 1 is chained to the VDB modifier above")
    cook.cook_now(back_tree, force=True)
    set_parm(back, back_tree.houdini_bridge, "conversion", "poly")
    cook.cook_now(back_tree)
    polygons = back_tree.houdini_bridge.holder.data
    corners = positions(polygons) if len(polygons.vertices) else np.zeros((1, 3))
    check(not back_tree.houdini_bridge.errors and len(polygons.polygons) > 500
          and np.allclose(corners.min(0), pig_points.min(0), atol=0.1),
          "Convert VDB turns the pig head back into polygons (%d faces) %r" % (len(polygons.polygons),
                                                                          back_tree.houdini_bridge.errors))
    sdf_modifier = next(m for m in pig.modifiers if m.type == "NODES" and m.node_group == sdf)
    set_parm(sdf_modifier, sdf.houdini_bridge, "voxelsize", [0.05])
    cook.cook_now(sdf)
    before = len(polygons.polygons)
    request = cook.prepare_request(support.target(back_tree))
    check(request is not None and request[0]["inputs"][0] == "chain", "a new result above recooks the one below")
    support.forget_sent(back_tree)
    cook.cook_now(back_tree, force=True)
    check(len(back_tree.houdini_bridge.holder.data.polygons) > before,
          "finer voxels above, finer polygons below (%d → %d faces)"
          % (before, len(back_tree.houdini_bridge.holder.data.polygons)))
    return pig, back_tree


def streams_apply(cook, modifiers, volume, curves, fragments):
    """Apply on the fog, the grass and the fragments, each an (object, node group)."""
    (box, fog), (lawn, grass), (cube, shatter) = volume, curves, fragments
    streams = support.streams()
    print("streams: Apply makes objects of Blender's own types")
    temporary = os.path.normcase(streams.volume_dir())
    box_matrix = box.matrix_world.copy()
    box_name = box.name
    bpy.ops.houdini_bridge.apply(tree=fog.name)
    applied = bpy.data.objects.get(box_name)
    check(applied is not None and applied.type == "VOLUME" and box_name + " · volume" not in bpy.data.objects,
          "a fog only result becomes a Volume object, in place of the emptied cube")
    kept = bpy.path.abspath(applied.data.filepath) if applied is not None else ""
    check(os.path.exists(kept) and not os.path.normcase(kept).startswith(temporary) and streams.is_waiting(kept),
          "the file is not saved: a kept copy of the .vdb waits in the extension's folder (%s)" % kept)
    check(applied is not None and np.allclose(np.array(applied.matrix_world), np.array(box_matrix), atol=1e-5)
          and applied.data.materials[:] and not applied.modifiers,
          "the grids rewritten Z up by Houdini (no extra rotation), material kept, no modifier left")

    print("streams: a Volume object as input (Convert VDB on the applied fog)")
    for other in bpy.context.selected_objects:
        other.select_set(False)
    applied.select_set(True)
    bpy.context.view_layer.objects.active = applied
    reconvert = modifiers.place_hda(bpy.context, "", {"type": "convertvdb", "label": "Convert VDB", "max_inputs": 1},
                                    "MODIFIER")
    check(reconvert.id_data == applied, "a Houdini modifier goes on the Volume object itself")
    cook.cook_now(reconvert.node_group, force=True)
    set_parm(reconvert, reconvert.node_group.houdini_bridge, "conversion", "poly")
    # A fog needs an isovalue above 0, as in Houdini (0, the default, gives nothing).
    set_parm(reconvert, reconvert.node_group.houdini_bridge, "isovalue", [0.5])
    cook.cook_now(reconvert.node_group)
    shell = reconvert.node_group.houdini_bridge.holder.data
    shell_points = positions(shell) if len(shell.vertices) else np.zeros((1, 3))
    centre = (shell_points.min(0) + shell_points.max(0)) / 2
    check(len(shell.polygons) > 50 and np.allclose(centre, (0.3, 2.0, 0.7), atol=0.06),
          "its .vdb is read: the fog turned back into polygons, in place (%d faces, centre %s) %r" % (
              len(shell.polygons), centre.round(2), reconvert.node_group.houdini_bridge.errors))

    lawn_name = lawn.name
    bpy.ops.houdini_bridge.apply(tree=grass.name)
    applied = bpy.data.objects.get(lawn_name)
    check(applied is not None and applied.type == "CURVES" and len(applied.data.curves) == 2000
          and [m.name for m in applied.data.materials] == ["bridge_grass"],
          "grass becomes a Curves object (2000 curves)")

    cube_name, shapes_before = cube.name, set(obj.name for obj in shatter.houdini_bridge.prototypes.objects)
    bpy.ops.houdini_bridge.apply(tree=shatter.name)
    applied = bpy.data.objects.get(cube_name)
    geometry = applied.evaluated_get(bpy.context.evaluated_depsgraph_get()).evaluated_geometry() if applied else None
    instances = geometry.instances_pointcloud() if geometry is not None else None
    references = {getattr(ref, "name", "") for ref in geometry.instance_references()} if geometry is not None else set()
    check(applied is not None and applied.type == "POINTCLOUD" and len(applied.modifiers) == 1
          and instances is not None and len(instances.points) == 12 and references == shapes_before,
          "fragments become a point cloud instancing its 12 shapes, nothing tied to Houdini")


def streams_apply_round_trip(cook, pig, back_tree):
    print("streams: round trip by Apply (Pig Head, VDB from Polygons, Convert VDB), and undo")
    pig_name = pig.name
    bpy.ops.houdini_bridge.apply(tree=hda(pig)[1].name)  # Pig Head becomes the mesh
    vdb_tree = hda(pig)[1]
    cook.cook_now(vdb_tree, force=True)
    cook.cook_now(back_tree, force=True)
    bpy.ops.houdini_bridge.apply(tree=vdb_tree.name)  # the volume, in the middle of the stack
    volume_object = bpy.data.objects.get(pig_name)
    check(volume_object is not None and volume_object.type == "VOLUME"
          and [m.node_group for m in volume_object.modifiers] == [back_tree],
          "the Volume object takes the object's place, with the Convert VDB below it")
    cook.cook_now(back_tree, force=True)
    faces = len(back_tree.houdini_bridge.holder.data.polygons)
    check(faces > 500 and not back_tree.houdini_bridge.errors,
          "Convert VDB keeps reading the volume: the pig head turned back into polygons (%d faces)" % faces)

    support.mark_released(back_tree)
    support.on_undo()
    check(support.is_requested(back_tree), "after an undo, a modifier whose instance was deleted cooks again")
    support.cancel_request(back_tree)


def limits_on_a_labs_tool(cook, modifiers):
    print("parameter limits on a real HDA: SideFX Labs Mesh Sharpen")
    bpy.ops.mesh.primitive_monkey_add(location=(8, 100, 0))
    sharpen_object = bpy.context.active_object
    sharpen = modifiers.place_hda(bpy.context, "",
                                  {"type": "labs::mesh_sharpen", "label": "Mesh Sharpen", "max_inputs": 1}, "MODIFIER")
    cook.cook_now(sharpen.node_group, force=True)
    step = next((p for p in sharpen.node_group.houdini_bridge.parms if p.name == "stepsize"), None)
    if step is None:
        print("   (SideFX Labs Mesh Sharpen not available: skipped)")
    else:
        socket = next(item for item in sharpen.node_group.interface.items_tree
                      if item.item_type == "SOCKET" and item.identifier == step.socket)
        set_parm(sharpen, sharpen.node_group.houdini_bridge, "stepsize", [-0.3])
        support.poll_inputs()
        check(socket.min_value == 0 and get_parm(sharpen, step) == [0.0],
              "Step Size (slider 0–1, not strict) cannot go negative: -0.3 is held at 0")
    bpy.data.objects.remove(sharpen_object)


def streams_apply_all(cook, modifiers):
    print("streams: Apply All and Apply as a block")

    def pig_combo(x, convert=True):
        for other in bpy.context.selected_objects:
            other.select_set(False)
        bpy.context.scene.cursor.location = (x, 100, 0)
        head = modifiers.create_hda_object(bpy.context, "",
                                           {"type": "testgeometry_pighead", "label": "Pig Head", "max_inputs": 0})
        cook.cook_now(hda(head)[1], force=True)
        for definition in ([{"type": "vdbfrompolygons", "label": "VDB from Polygons", "max_inputs": 2}]
                           + ([{"type": "convertvdb", "label": "Convert VDB", "max_inputs": 1}] if convert else [])):
            modifier = modifiers.create_hda_modifier(bpy.context, head, "", definition)
            cook.cook_now(modifier.node_group, force=True)
            if definition["type"] == "convertvdb":
                set_parm(modifier, modifier.node_group.houdini_bridge, "conversion", "poly")
                cook.cook_now(modifier.node_group)
        return head

    head = pig_combo(0)
    name = head.name
    final_faces = len(head.modifiers[-1].node_group.houdini_bridge.holder.data.polygons)
    bpy.ops.houdini_bridge.apply_all(object=name)
    head = bpy.data.objects.get(name)
    check(head is not None and head.type == "MESH" and not head.modifiers and len(head.data.polygons) == final_faces,
          "Apply All: the whole combo at once, the final mesh (%d faces), no modifier left" % final_faces)
    check(not any(o.name.startswith(name + " ·") for o in bpy.data.objects
              if not o.houdini_bridge_result),
          "no in-between object (no volume split out)")

    head = pig_combo(4)
    name = head.name
    vdb_modifier = head.modifiers[1]
    convert_tree = head.modifiers[2].node_group
    bpy.ops.houdini_bridge.apply(tree=vdb_modifier.node_group.name, with_above=True)
    block = bpy.data.objects.get(name)
    check(block is not None and block.type == "VOLUME" and [m.node_group for m in block.modifiers] == [convert_tree],
          "Apply as a block (Pig Head + VDB): a Volume object carrying the Convert VDB")
    cook.cook_now(convert_tree, force=True)
    bpy.ops.houdini_bridge.apply(tree=convert_tree.name)
    block = bpy.data.objects.get(name)
    check(block is not None and block.type == "MESH" and len(block.data.polygons) > 500 and not block.modifiers,
          "Apply on a Volume object: its result, a mesh, takes its place (%d faces)" % (
              len(block.data.polygons) if block is not None and block.type == "MESH" else 0))


def streams_duplicate_cache_remove(cook, ground, modifier):
    tree = modifier.node_group
    settings = tree.houdini_bridge
    holder = settings.instances_holder
    print("streams: duplicate, cache, remove")
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    ground.select_set(True)
    bpy.context.view_layer.objects.active = ground
    bpy.ops.object.duplicate()
    copy = bpy.context.active_object
    support.maintain()
    copy_tree = hda(copy)[1]
    cook.cook_now(copy_tree, force=True)
    check(copy_tree != tree and copy_tree.houdini_bridge.instances_holder not in (None, holder)
          and copy_tree.houdini_bridge.prototypes != settings.prototypes, "the copy gets its own instances and shapes")
    check(len(holder.data.points) == 600 and len(settings.prototypes.objects) == 2, "the original keeps its own")

    cache = support.cache()
    native_faces, native_points = realized(ground)
    status = cache.cache_now(tree, 1, 2)
    check(settings.use_cache and len(holder.data.points) == 0,
          "cached: the frames hold the realized copies (%s)" % status)
    cached_faces, cached_points = realized(ground)
    check(cached_faces == native_faces and same_points(cached_points, native_points),
          "the cached frame shows the same copies (%d faces, live %d, %d vs %d points)" % (
              cached_faces, native_faces, len(cached_points), len(native_points)))
    cache.clear(settings)
    cook.cook_now(tree, force=True)
    check(len(holder.data.points) == 600, "the cache cleared, the instances come back")

    names = [holder.name] + [obj.name for obj in settings.prototypes.objects]
    ground.modifiers.remove(modifier)
    support.maintain()
    check(not any(name in bpy.data.objects for name in names),
          "removing the modifier removes the stream holders and shapes")


def test_cache(cook, modifiers, definitions):
    print("cache: a frame range cooked once, played back by Blender")
    cache = support.cache()
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        obj.hide_render = True
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 60, 0))
    mover = bpy.context.active_object
    mover.hide_render = True
    mover.keyframe_insert("location", frame=1)
    mover.location.x += 3.0
    mover.keyframe_insert("location", frame=10)
    bpy.ops.object.select_all(action="DESELECT")
    mover.select_set(True)
    bpy.context.view_layer.objects.active = mover
    rock = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::rock"])
    tree = hda(rock)[1]
    settings = tree.houdini_bridge
    live = {}
    for frame in (1, 5, 10):
        scene.frame_set(frame)
        cook.cook_now(tree, force=True)
        live[frame] = evaluated_local(rock)[0]

    start = time.perf_counter()
    status = cache.cache_now(tree, 1, 10)
    print("   ", status, "(%.1f s)" % (time.perf_counter() - start))
    check(settings.use_cache and cache.frame_count(settings) == 10, "10 frames cached and in use (%s)" % status)
    for frame in (1, 5, 10):
        scene.frame_set(frame)
        shown, materials, attributes = evaluated_local(rock)
        check(shown.shape == live[frame].shape and np.allclose(shown, live[frame], atol=1e-5),
              "frame %d: the cache shows what the live cook gave" % frame)
    check(materials == {"rock_dark", "rock_light"} and "Cd" in attributes,
          "materials and attributes are kept %s" % sorted(materials))

    print("cache: a duplicate keeps the frames it was copied with")
    bpy.ops.object.select_all(action="DESELECT")
    rock.select_set(True)
    bpy.context.view_layer.objects.active = rock
    bpy.ops.object.duplicate()
    duplicate = bpy.context.active_object
    support.maintain()
    copied = hda(duplicate)[1].houdini_bridge
    player = copied.holder.modifiers[cache.PLAYER_MODIFIER].node_group
    check(player != settings.holder.modifiers[cache.PLAYER_MODIFIER].node_group, "the copy has a player of its own")
    old_collection = copied.cache_collection
    cache.cache_now(tree, 1, 10)
    check(player.nodes["Frames"].inputs["Collection"].default_value == old_collection == copied.cache_collection
          and settings.cache_collection != old_collection, "caching the original again leaves the copy's frames")
    support.api().remove_modifier(hda(duplicate)[1])
    bpy.data.objects.remove(duplicate)
    scene.frame_set(14)
    check(np.allclose(evaluated_local(rock)[0], live[10], atol=1e-5), "after the range, the last frame stays")
    check(not support.is_requested(tree), "in cache mode a frame change does not cook")
    set_parm(hda(rock)[0], settings, "height", [1.0])
    check(cache.is_stale(support.target(tree)), "changing a parameter marks the cache out of date")
    support.poll_inputs()
    check(not support.is_requested(tree), "and does not cook while the cache is used")

    print("cache: rendering two frames")
    camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    scene.collection.objects.link(camera)
    camera.location = (1.5, 60, 12)
    scene.camera = camera
    rock.hide_render = False
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = 64
    folder = tempfile.mkdtemp(prefix="bridge_render_")
    first = render_frame(1, os.path.join(folder, "f1.png"))
    last = render_frame(10, os.path.join(folder, "f10.png"))
    again = render_frame(1, os.path.join(folder, "f1b.png"))
    check(np.abs(first - last).max() > 0.1 and np.allclose(first, again), "renders follow the cached frames")
    check(not support.is_rendering(), "live updates resume after rendering")

    print("cache: clear")
    collection_name = settings.cache_collection.name
    cache.clear(settings)
    check(not settings.use_cache and collection_name not in bpy.data.collections
          and cache.PLAYER_MODIFIER not in settings.holder.modifiers, "the frames and the player are removed")
    check(support.is_requested(tree), "live cooking resumes")
    scene.frame_set(1)


def test_attributes(cook, modifiers, definitions):
    print("attributes from Blender to Houdini")
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 48, 0))
    grid = bpy.context.active_object
    mesh = grid.data
    count = len(mesh.vertices)
    painted = grid.vertex_groups.new(name="Weight Paint")
    for index in range(count):
        painted.add([index], index / (count - 1), "REPLACE")
    paint = mesh.color_attributes.new("paint", "FLOAT_COLOR", "POINT")
    paint.data.foreach_set("color", np.tile(np.float32([0.1, 0.5, 0.9, 1.0]), count))
    mesh.color_attributes.active_color = paint
    direction = mesh.attributes.new("direction", "FLOAT_VECTOR", "FACE")
    direction.data.foreach_set("vector", np.tile(np.float32([1.0, 2.0, 3.0]), len(mesh.polygons)))
    second_uv = mesh.uv_layers.new(name="detail")
    mesh.uv_layers.active = mesh.uv_layers[0]
    second_uv.uv.foreach_set("vector", np.tile(np.float32([0.25, 0.75]), len(mesh.loops)))

    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::groups"], "MODIFIER")
    tree = modifier.node_group
    header, _ = support.peek_request(tree)
    sent = {a[0]: a for a in header["input_attributes"]["0"]}
    check({"Weight Paint", "Cd", "direction", "detail"} <= set(sent),
          "the request lists the attributes %s" % sorted(sent))
    check(sent["direction"][5] == "vector" and sent["Cd"][5] == "color" and sent["detail"][5] == "texturecoord",
          "qualifiers: vector, color, texture coordinate")
    cook.cook_now(tree, force=True)
    set_parm(modifier, tree.houdini_bridge, "distance", [0.0])
    cook.cook_now(tree)
    check(not tree.houdini_bridge.errors, "no error: %r" % tree.houdini_bridge.errors)
    result = tree.houdini_bridge.holder.data
    found = {a.name: (a.domain, a.data_type) for a in result.attributes}
    check(found.get("Weight_Paint") == ("POINT", "FLOAT"),
          "painted weights come back as a point float (%s)" % (found.get("Weight_Paint"),))
    weights = attribute_values(result, "Weight_Paint")[:, 0]
    check(np.allclose(np.sort(weights), np.linspace(0, 1, count), atol=1e-5), "with their values")
    check("Cd" in result.color_attributes
          and np.allclose(attribute_values(result, "Cd")[0], [0.1, 0.5, 0.9, 1.0], atol=1e-5),
          "the active color comes back as Cd")
    check(found.get("direction") == ("FACE", "FLOAT_VECTOR")
          and np.allclose(attribute_values(result, "direction"), [1, 2, 3], atol=1e-5),
          "a vector survives the round trip (axes converted both ways)")
    check(found.get("detail") == ("CORNER", "FLOAT2") and "detail" in result.uv_layers
          and np.allclose(attribute_values(result, "detail"), [0.25, 0.75], atol=1e-5),
          "a second UV map comes back as a UV map")

    print("changing values only: the light path")
    painted.add([0], 0.5, "REPLACE")
    mesh.update()
    bpy.context.view_layer.update()
    header, arrays = support.peek_request(tree)
    check(header["inputs"] == ["positions"] and "0" in header["input_attributes"] and "in0_counts" not in arrays,
          "a new weight sends the values, not the topology (%s)" % header["inputs"])
    cook.cook_now(tree)
    weights = attribute_values(result, "Weight_Paint")[:, 0]
    check(np.isclose(weights, 0.5).sum() >= 1, "the new weight reaches the result")
    uv = np.empty(len(mesh.loops) * 2, np.float32)
    mesh.uv_layers.active.uv.foreach_get("vector", uv)
    mesh.uv_layers.active.uv.foreach_set("vector", uv * 0.5)
    mesh.update()
    support.mark_input_changed(tree, 0)
    header, arrays = support.peek_request(tree)
    check(header["inputs"] == ["positions"] and "in0_uv" in arrays, "an edited UV map is sent")

    print("attributes flow down the stack")
    second = modifiers.place_hda(bpy.context, HDA, definitions["bridge::groups"], "MODIFIER")
    second_tree = second.node_group
    cook.cook_now(second_tree, force=True)
    set_parm(second, second_tree.houdini_bridge, "distance", [0.0])
    cook.cook_now(second_tree)
    stacked = second_tree.houdini_bridge.holder.data
    check({"Weight_Paint", "direction", "detail", "height"} <= {a.name for a in stacked.attributes},
          "the second HDA receives the attributes of the first result")
    check(np.allclose(attribute_values(stacked, "direction"), [1, 2, 3], atol=1e-5), "unchanged after two trips")


def test_stack_order(cook, modifiers, definitions):
    print("integer menu with text tokens, and a reordered stack")
    settle = support.settle

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    ball = bpy.context.active_object
    names = []
    for node_type, label in (("mountain", "Mountain"), ("color", "Color"), ("subdivide", "Subdivide"),
                             ("measure::2.0", "Measure")):
        modifier = modifiers.place_hda(bpy.context, "", {"type": node_type, "label": label, "max_inputs": 1},
                                       "MODIFIER")
        settle()
        names.append(modifier.name)

    color = ball.modifiers[names[1]]
    settings = color.node_group.houdini_bridge
    check(get_parm(color, settings.parms["class"]) == "point", "an integer menu reads as its token, not its index")
    set_parm(color, settings, "color", [1.0, 0.27, 0.0])
    support.poll_inputs()
    check(settle() and not settings.errors, "a Color node cooks with an integer menu: %r" % settings.errors[-200:])
    colors = attribute_values(settings.holder.data, "Cd")
    check(np.allclose(colors[:, :3], [1.0, 0.27, 0.0], atol=0.01), "the color comes back on the points")

    def fingerprint():
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        return len(ball.evaluated_get(depsgraph).data.vertices)

    def errors():
        return [ball.modifiers[name].node_group.houdini_bridge.errors for name in names
                if ball.modifiers[name].node_group.houdini_bridge.errors]

    for order in ((3, 2, 1, 0), (2, 0, 3, 1), (1, 3, 0, 2), (0, 2, 1, 3)):
        for index, position in enumerate(order):
            ball.modifiers.move(ball.modifiers.find(names[position]), index)
        bpy.context.view_layer.update()
        settled = settle()
        live = fingerprint()
        check(settled and not errors(), "order %s cooks without a loop: %s" % (order, errors()))
        for name in [names[position] for position in order]:
            cook.cook_now(ball.modifiers[name].node_group, force=True)
        settle()
        check(live == fingerprint(),
              "order %s: the live result is the clean one (%d, %d)" % (order, live, fingerprint()))


def test_group_rules(cook, modifiers, definitions):
    print("empty groups are gone, a group of the same name and type replaces the other")
    import json

    groups = support.groups()
    above = {"groupbase": False, "groupbounding": True, "size": [3.0, 1.0, 3.0], "t": [0.0, 0.6, 0.0]}
    below = {"groupbase": False, "groupbounding": True, "size": [3.0, 0.5, 3.0], "t": [0.0, -0.75, 0.0]}
    nowhere = {"groupbase": False, "groupbounding": True, "size": [0.1, 0.1, 0.1], "t": [50.0, 0.0, 0.0]}
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, location=(0, 40, 0))
    ball = bpy.context.active_object

    def add(node_type, parms):
        modifier = modifiers.place_hda(bpy.context, "", {"type": node_type, "label": node_type, "max_inputs": 1},
                                       "MODIFIER")
        cook.cook_now(modifier.node_group, force=True)
        for name, value in parms.items():
            set_parm(modifier, modifier.node_group.houdini_bridge, name, value)
        cook.cook_now(modifier.node_group)
        return modifier

    def listed(modifier):
        return {group.name: group.count for group in modifier.node_group.houdini_bridge.result_groups}

    empty = add("groupcreate", dict(nowhere, groupname="gone"))
    settings = empty.node_group.houdini_bridge
    check("gone" not in listed(empty), "a group with no element is not listed")
    check("gone" not in settings.holder.data.attributes, "a group with no element leaves no attribute")
    check("gone" not in groups.legend(ball), "nor anything in the viewport badge")

    def suggested(modifier):
        return json.loads(modifier.node_group.houdini_bridge.parms["group"].menu_json or "[]")

    first = add("groupcreate", dict(above, groupname="top"))
    second = add("groupcreate", dict(below, groupname="top"))
    counts = listed(first)["top"], listed(second)["top"]
    check(len([name for name in listed(second) if name == "top"]) == 1 and counts[0] != counts[1],
          "the same name and type replaces the group (%d, then %d faces)" % counts)
    peak = add("mountain", {})
    check("top" in suggested(peak), "the Group field below suggests a filled group %s" % suggested(peak))
    check("gone" not in suggested(peak), "and not the empty one %s" % suggested(peak))
    third = add("groupcreate", dict(nowhere, groupname="top"))
    check("top" not in listed(third), "a group emptied further down is gone there")
    check("top" in listed(second), "and stays on the modifier that filled it")


def _panel_paths(tree):
    """{socket name: panel path} of the group inputs."""
    paths = {}
    for item in tree.interface.items_tree:
        if item.item_type != "SOCKET" or item.in_out != "INPUT":
            continue
        path, panel = [], item.parent
        while panel is not None and panel.name:
            path.insert(0, panel.name)
            panel = panel.parent
        paths[item.name + ("" if item.name not in paths else "*")] = " › ".join(path)
    return paths


def test_ramps_multiparms(cook, modifiers, definitions):
    print("ramps, multiparms and nested folders")
    parameters = support.parameters()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=20, y_subdivisions=20, size=2, location=(0, 12, 0))
    grid = bpy.context.active_object
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::ramps"], "MODIFIER")
    tree = modifier.node_group
    settings = tree.houdini_bridge
    cook.cook_now(tree, force=True)
    contract = support.installed("contract")
    check(tuple(parameters.KINDS) == contract.PARM_KINDS
          and all(kind.name == name for name, kind in parameters.KINDS.items()),
          "the registry holds the kinds of contract.PARM_KINDS, in their order")
    check(all(parameters.kind_of(p.kind) is parameters.KINDS[p.kind] for p in settings.parms),
          "every stored parameter has its kind in the registry")
    check(not settings.errors, "no error: %r" % settings.errors)
    kinds = {p.name: p.kind for p in settings.parms}
    check(kinds.get("shape") == "ramp" and kinds.get("tint") == "ramp", "ramps are described %s" % kinds)
    check(settings.parms["bumps"].multiparm and "radius1" in kinds and "ring_size1_1" not in kinds,
          "a multiparm gives its count and one set of parameters per instance")
    paths = _panel_paths(tree)
    check(paths.get("Height") == "Look › Advanced",
          "a folder inside a folder is a nested panel (%s)" % paths.get("Height"))
    check(paths.get("Radius") == "Bumps 1" and paths.get("Number of Bumps") == "",
          "the count stays in its folder, each instance gets a panel (%s)" % paths)

    shape = parameters.ramp_node(tree, settings.parms["shape"])
    tint = parameters.ramp_node(tree, settings.parms["tint"])
    check(shape is not None and shape.bl_idname == "ShaderNodeFloatCurve", "a float ramp is a Float Curve node")
    check(tint is not None and tint.bl_idname == "ShaderNodeValToRGB", "a color ramp is a Color Ramp node")
    value = get_parm(modifier, settings.parms["shape"])
    check(value["keys"] == [0.0, 0.5, 1.0] and value["values"] == [0.0, 1.0, 0.0]
          and value["basis"] == ["Linear", "CatmullRom", "Linear"],
          "the ramp keys and their Houdini interpolation come through %s" % value)
    mesh = settings.holder.data
    lift = attribute_values(mesh, "lift")
    peak = float(lift.max())

    print("editing the float ramp in Blender")
    points = shape.mapping.curves[0].points
    points[1].location = (0.5, 0.25)
    shape.mapping.update()
    support.poll_inputs()
    check(support.is_requested(tree), "a ramp edit requests a cook")
    cook.cook_now(tree)
    lowered = float(attribute_values(mesh, "lift").max())
    check(lowered < peak - 0.3, "the ramp edit reaches Houdini (highest lift %.2f → %.2f)" % (peak, lowered))
    check(get_parm(modifier, settings.parms["shape"])["basis"][1] == "CatmullRom",
          "the interpolation of a key is kept while its handle type matches")

    print("editing the color ramp")
    tint.color_ramp.elements[0].color = (0.0, 1.0, 0.0, 1.0)
    tint.color_ramp.interpolation = "CONSTANT"
    support.poll_inputs()
    cook.cook_now(tree)
    sent = get_parm(modifier, settings.parms["tint"])
    check(sent["basis"] == ["Constant", "Constant"] and sent["values"][0] == [0.0, 1.0, 0.0],
          "Blender's interpolation and colors are sent %s" % sent)
    colors = attribute_values(mesh, "Cd")
    check(float(colors[:, 1].mean()) > 0.5, "the result takes the new colors (mean green %.2f)" % colors[:, 1].mean())

    print("a color ramp whose keys use two interpolations equally often takes the first key's")
    keys, values = [0.0, 0.25, 0.5, 0.75, 1.0], [[0.0, 1.0, 0.0]] * 5
    chosen = []
    for bases in (["CatmullRom", "MonotoneCubic", "MonotoneCubic", "CatmullRom", "Linear"],
                  ["MonotoneCubic", "CatmullRom", "CatmullRom", "MonotoneCubic", "Linear"]):
        set_parm(modifier, settings, "tint", {"keys": keys, "values": values, "basis": bases})
        chosen.append(tint.color_ramp.interpolation)
    check(chosen == ["CARDINAL", "EASE"], "CatmullRom first: Cardinal; MonotoneCubic first: Ease %s" % chosen)

    print("multiparm count: inputs rebuilt, kept inputs keep their identifier")
    radius_socket = settings.parms["radius1"].socket
    item = support.modifier_input(modifier, radius_socket)
    item.value = 0.8
    item.keyframe_insert("value", frame=1)
    item.value = 1.5
    item.keyframe_insert("value", frame=10)
    bpy.context.scene.frame_set(1)
    set_parm(modifier, settings, "bumps", [3])
    support.poll_inputs()
    cook.cook_now(tree)
    names = [p.name for p in settings.parms]
    check("center3" in names and "radius2" in names, "three instances after the count went to 3")
    check(settings.parms["radius1"].socket == radius_socket, "the first instance keeps its input (%s → %s)"
          % (radius_socket, settings.parms["radius1"].socket))
    check(abs(get_parm(modifier, settings.parms["radius1"])[0] - 0.8) < 1e-6, "and its value")
    bpy.context.scene.frame_set(10)
    check(abs(get_parm(modifier, settings.parms["radius1"])[0] - 1.5) < 1e-6, "and its keyframes")
    bpy.context.scene.frame_set(1)
    grid.animation_data_clear()
    check(_panel_paths(tree).get("Center*") is not None, "every instance shows its parameters")
    check(abs(get_parm(modifier, settings.parms["radius3"])[0] - 0.5) < 1e-6, "a new instance takes Houdini's default")

    print("nested multiparm")
    set_parm(modifier, settings, "rings1", [2])
    support.poll_inputs()
    cook.cook_now(tree)
    check("ring_size1_2" in [p.name for p in settings.parms], "a multiparm inside an instance adds its own instances")
    check(settings.parms["ring_size1_2"].folder.split("\t") == ["Bumps 1", "Rings 2"], "in a panel inside the instance")

    set_parm(modifier, settings, "bumps", [1])
    support.poll_inputs()
    cook.cook_now(tree)
    names = [p.name for p in settings.parms]
    check("center2" not in names and "ring_size1_2" in names, "lowering the count removes the last instances only")
    panels = [i.name for i in tree.interface.items_tree if i.item_type == "PANEL"]
    check("Bumps 2" not in panels and "Bumps 3" not in panels, "and their panels %s" % panels)
    unused = [p.name for p in settings.parms if p.socket and not modifier.is_input_used(p.socket)]
    check(not unused, "every input is still active after the rebuilds %s" % unused)
    check(not settings.errors, "no error: %r" % settings.errors)


def _hidden_inputs(tree):
    """Names of the parameters whose modifier input is left out: by the modifier panel, which
    draws the inputs (parameters.drawn, every tab), or, the preference Houdini Interface off,
    by Blender (hide_in_modifier)."""
    settings = tree.houdini_bridge
    parameters = support.parameters()
    if parameters.houdini_interface_on():
        drawn = {row[1] for row in parameters.drawn(tree, every_tab=True) if row[0] == "parm"}
        return {p.name for p in settings.parms if p.socket and p.name not in drawn}
    return _hidden_by_houdini(tree)


def _hidden_by_houdini(tree):
    """Names of the parameters whose input Blender hides (hide_in_modifier)."""
    settings = tree.houdini_bridge
    sockets = {item.identifier: item for item in tree.interface.items_tree if item.item_type == "SOCKET"}
    return {p.name for p in settings.parms if p.socket in sockets and sockets[p.socket].hide_in_modifier}


def _native_modifier(modifiers, node_type, location):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=location)
    return modifiers.place_hda(bpy.context, "", {"type": node_type, "label": node_type, "max_inputs": 1}, "MODIFIER")


def test_hide_when(cook, modifiers):
    print("hide-when conditions: what Houdini hides is left out in Blender, and comes back")
    parameters, preferences = support.parameters(), support.preferences()
    modifier = _native_modifier(modifiers, "polyextrude::2.0", (0, 18, 0))
    tree = modifier.node_group
    settings = tree.houdini_bridge
    cook.cook_now(tree, force=True)
    check(not settings.errors, "no error: %r" % settings.errors)
    front = {"xformspace", "rst", "translate", "scale"}  # HideWhen { xformfront == 0 }
    hidden = {p.name for p in settings.parms if p.hidden}
    check(front <= hidden and "xformfront" not in hidden,
          "Poly Extrude: Transform Extruded Front off hides its transform %s" % sorted(hidden))
    check(front <= _hidden_inputs(tree) and "xformfront" not in _hidden_inputs(tree),
          "their inputs are hidden in the modifier")
    check(all(parameters.is_hidden(p) == p.hidden for p in settings.parms),
          "the sidebar asks the same function")
    # Pivot Transform and Pre-Transform: folders hidden by { xformfront == 0 }, whose parameters
    # have no condition of their own.
    held = {"pivot", "pivotrotate", "prexform_rst", "prexform_xyz", "prexform_translate", "prexform_rotate",
            "prexform_scale", "prexform_shear"}
    check(held <= hidden and held <= _hidden_inputs(tree),
          "the parameters of Pivot Transform and Pre-Transform are hidden with their folders %s"
          % sorted(held - _hidden_inputs(tree)))
    identifiers = {p.name: p.socket for p in settings.parms}
    structure = settings.structure

    print("a hidden parameter keeps its value and keyframes, and is still sent")
    item = support.modifier_input(modifier, settings.parms["translate"].socket)
    set_parm(modifier, settings, "translate", [0.0, 0.5, 0.0])
    item.keyframe_insert("value", frame=10)
    set_parm(modifier, settings, "translate", [0.0, 0.25, 0.0])
    item.keyframe_insert("value", frame=1)
    bpy.context.scene.frame_set(1)
    header, _ = support.peek_request(tree)
    check(header["parms"].get("translate") == [0.0, 0.25, 0.0], "its value is in the request %s"
          % header["parms"].get("translate"))
    support.poll_inputs()
    set_parm(modifier, settings, "pivot", [0.0, 0.125, 0.0])
    check(support.peek_request(tree)[0]["parms"].get("pivot") == [0.0, 0.125, 0.0],
          "a parameter hidden with its folder is sent too")
    support.poll_inputs()
    cook.cook_now(tree)
    check(_hidden_inputs(tree) >= front, "still hidden after the cook")

    print("the value that drives the condition changes: shown at the next cook")
    set_parm(modifier, settings, "xformfront", True)
    support.poll_inputs()
    cook.cook_now(tree)
    check(not front & _hidden_inputs(tree) and not any(settings.parms[name].hidden for name in front),
          "the transform shows %s" % sorted(front & _hidden_inputs(tree)))
    check(settings.structure == structure and {p.name: p.socket for p in settings.parms} == identifiers,
          "no rebuild: the same signature, the same identifiers")
    check(get_parm(modifier, settings.parms["translate"]) == [0.0, 0.25, 0.0], "and the value set while hidden")
    check(not held & _hidden_inputs(tree) and not any(settings.parms[name].hidden for name in held)
          and get_parm(modifier, settings.parms["pivot"]) == [0.0, 0.125, 0.0],
          "the parameters of both folders show, with the value set while hidden %s"
          % sorted(held & _hidden_inputs(tree)))
    bpy.context.scene.frame_set(10)
    check(get_parm(modifier, settings.parms["translate"]) == [0.0, 0.5, 0.0], "and its keyframes")
    bpy.context.scene.frame_set(1)
    modifier.id_data.animation_data_clear()
    # Any change of the node group's interface marks input 1 to be read again; the read
    # finds it unchanged, and nothing goes to Houdini.
    bpy.context.view_layer.update()
    support.poll_inputs()
    check(support.peek_request(tree) is None, "showing an input sends nothing more to Houdini")
    set_parm(modifier, settings, "xformfront", False)
    support.poll_inputs()
    cook.cook_now(tree)
    check(front <= _hidden_inputs(tree), "and hidden again when it goes back")
    check(held <= _hidden_inputs(tree) and {p.name: p.socket for p in settings.parms} == identifiers
          and get_parm(modifier, settings.parms["pivot"]) == [0.0, 0.125, 0.0],
          "the parameters of both folders too, with the same identifiers and their value")

    print("Hide Like Houdini off: nothing is hidden, in the modifier or in the sidebar")
    color = _native_modifier(modifiers, "color", (4, 18, 0)).node_group
    cook.cook_now(color, force=True)
    group = _native_modifier(modifiers, "groupcreate", (8, 18, 0)).node_group
    cook.cook_now(group, force=True)
    sidebar = [color.houdini_bridge.parms["ramp"], color.houdini_bridge.parms["rampattribute"],
               group.houdini_bridge.parms["initbounds"]]
    check(all(p.hidden and parameters.is_hidden(p) for p in sidebar),
          "Color's ramp and attribute field, Group Create's Initialize button: hidden by Houdini %s"
          % [(p.name, p.hidden) for p in sidebar])
    preferences.follow_hide_when = False
    check(not _hidden_inputs(tree) and not any(parameters.is_hidden(p) for p in settings.parms),
          "off: every input shows %s" % sorted(_hidden_inputs(tree)))
    check(not any(parameters.is_hidden(p) for p in sidebar) and all(p.hidden for p in sidebar),
          "and the sidebar shows the ramp, the field and the button, still hidden by Houdini")
    preferences.follow_hide_when = True
    bpy.context.view_layer.update()
    support.poll_inputs()
    check(front <= _hidden_inputs(tree) and all(parameters.is_hidden(p) for p in sidebar)
          and all(support.peek_request(t) is None for t in (tree, color, group)),
          "on again: hidden again, without a cook")
    for t in (tree, color, group):
        support.cancel_request(t)


def test_code_editor(cook, modifiers):
    print("a code parameter: an Attribute Wrangle's snippet is VEX, edited in the Text Editor")
    editor, parameters = support.code_editor(), support.parameters()
    rows = support.installed("parameter_rows")
    modifier = _native_modifier(modifiers, "attribwrangle", (0, 30, 0))
    owner, tree, settings = modifier.id_data, modifier.node_group, modifier.node_group.houdini_bridge
    cook.cook_now(tree, force=True)
    snippet = settings.parms["snippet"]
    check(snippet.editor == "vex" and [p.name for p in settings.parms if p.editor] == ["snippet"],
          "the snippet is the one code parameter, in VEX %s" % [(p.name, p.editor) for p in settings.parms if p.editor])
    check(not snippet.menu_json and not snippet.group_selection,
          "its code presets are not suggestions: no group picker")
    check(("parm", "snippet") in [row[:2] for row in parameters.drawn(tree)], "the panel draws its row")
    check(snippet in [parm for parm, _ in rows.beside_inputs(tree)],
          "Houdini interface off, the panel still draws its block")
    check(rows.code_preview("a\n\tb\n" + "c\n" * 9) == (["a", "    b", "c", "c", "c", "c"], 5),
          "the block shows the first lines, tabs as spaces, and counts the others")
    python = _native_modifier(modifiers, "python", (4, 30, 0)).node_group
    cook.cook_now(python, force=True)
    script = python.houdini_bridge.parms["python"]
    check(script.editor == "python" and editor.text_name(python, script).endswith(".py"),
          "a Python SOP's script is Python, highlighted as Python")
    check(editor.text_name(tree, snippet).endswith(".osl"), "VEX is highlighted as OSL")

    set_parm(modifier, settings, "snippet", "@P.y += 1;")
    support.poll_inputs()
    cook.cook_now(tree)
    low = positions(settings.holder.data)[:, 2].mean()

    print("the mirror: made from the parameter, once")
    text = editor.open_editor(bpy.context, tree, "snippet")
    check(text is not None and text.is_runtime_data and text.as_string() == "@P.y += 1;",
          "a runtime Text holding the parameter's code")
    again = editor.open_editor(bpy.context, tree, "snippet")
    check(again == text and len(editor.mirrors()) == 1 and [t.name for t in bpy.data.texts] == [text.name],
          "opened again: the same text, no second one %s" % [t.name for t in bpy.data.texts])

    print("what is typed reaches the parameter once typing stops, and cooks")
    text.from_string("@P.y += 2;")
    editor.follow(now=100.0)
    check(get_parm(modifier, snippet) == "@P.y += 1;", "not while it is being typed")
    editor.follow(now=100.0 + editor.DELAY)
    check(get_parm(modifier, snippet) == "@P.y += 2;", "written once typing stopped")
    support.poll_inputs()
    check(support.is_requested(tree), "which asks for a cook")
    cook.cook_now(tree)
    high = positions(settings.holder.data)[:, 2].mean()
    check(not settings.errors and abs(high - low - 1.0) < 1e-4, "cooked: the points one higher (%.3f)" % (high - low))

    print("the parameter changed elsewhere: the text follows")
    set_parm(modifier, settings, "snippet", "@P.y += 3;")
    editor.follow(now=200.0)
    check(text.as_string() == "@P.y += 3;", "the text holds the new value %r" % text.as_string())

    print("a VEX error: shown in the panel")
    text.from_string("@P.y += ;")
    editor.follow(now=300.0)
    editor.follow(now=301.0)
    support.poll_inputs()
    cook.cook_now(tree)
    check(settings.errors and "syntax" in settings.errors.lower(), "the error is the modifier's %r" % settings.errors)
    shown = rows.error_lines(settings.errors)
    check(shown and "(1,9)" in " ".join(shown) and all(len(line) <= rows.ERROR_LINE_WIDTH for line in shown),
          "the code block shows it whole under the code, line and column included %s" % shown)
    text.from_string("@P.y += 1;")
    editor.follow(now=400.0)
    editor.follow(now=401.0)
    support.poll_inputs()
    cook.cook_now(tree)
    check(not settings.errors, "fixed: gone %r" % settings.errors)

    print("a saved file holds no mirror, and the code typed")
    text.from_string("@P.y += 4;")
    editor.follow(now=500.0)
    path = os.path.join(tempfile.mkdtemp(), "code.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    check(get_parm(modifier, snippet) == "@P.y += 4;", "what was typed reached the parameter before the save")
    with bpy.data.libraries.load(path) as (data_from, data_to):
        check(list(data_from.texts) == [], "no Text in the file %s" % list(data_from.texts))

    print("the modifier removed: its mirror goes")
    owner.modifiers.remove(modifier)
    editor.follow()
    check(not editor.mirrors() and not bpy.data.texts, "no mirror and no Text left %s"
          % [t.name for t in bpy.data.texts])


def _interface_state(tree):
    """(whether every parameter input is hidden from Blender's drawing, whether every modifier
    using the node group hides its selector and its Manage panel)."""
    settings = tree.houdini_bridge
    sockets = {item.identifier: item for item in tree.interface.items_tree if item.item_type == "SOCKET"}
    inputs = [sockets[p.socket].hide_in_modifier for p in settings.parms if p.socket in sockets]
    users = support.targets().modifier_users(tree)
    flags = [not m.show_group_selector and not m.show_manage_panel for _, m in users]
    return bool(inputs) and all(inputs), bool(flags) and all(flags)


def _rows(parameters, tree, kind, every_tab=True):
    return [row for row in parameters.drawn(tree, every_tab) if row[0] == kind]


def _homonym_folders(parameters, tree):
    """Two sibling folders of one label (unlabelled Simple folders as separators, two "Settings")
    stay two, in Houdini's order, told apart by their Houdini names; a file whose folder types
    hold no name yet merges them until its next cook, as before."""
    print("two folders with the same label: their Houdini names keep them apart")
    parms = [p for p in tree.houdini_bridge.parms if not parameters.is_hidden(p)][:3]
    saved = [(p.folder, p.folder_types) for p in parms]
    first, middle, last = parms
    middle.folder, middle.folder_types = "", "[]"
    for parm, name in ((first, "settings1"), (last, "settings2")):
        parm.folder_types = json.dumps([["simple", "", name]])
        parm.folder = "Settings"
    parameters.forget_layout(tree)
    rows = [row for row in parameters.drawn(tree) if row[0] != "parm" or row[1] in {p.name for p in parms}]
    wanted = [("folder", "simple", ("Settings",)), ("parm", first.name), ("parm", middle.name),
              ("folder", "simple", ("Settings",)), ("parm", last.name)]
    check([row[:3] if row[0] == "folder" else row[:2] for row in rows if row[0] == "parm" or row[2] == ("Settings",)]
          == wanted, "named: Settings, %s, then Settings again, in Houdini's order" % middle.name)
    for parm in (first, last):
        parm.folder_types = json.dumps([["simple", ""]])
    parameters.forget_layout(tree)
    merged = [row for row in parameters.drawn(tree) if row[0] == "folder" and row[2] == ("Settings",)]
    check(len(merged) == 1, "without names (a file not cooked again yet): one folder, as before")
    for parm, (folder, folder_types) in zip(parms, saved, strict=True):
        parm.folder, parm.folder_types = folder, folder_types
    parameters.forget_layout(tree)


def test_native(cook, modifiers, definitions):
    print("the Houdini modifier drawn by the extension: the Houdini interface")
    parameters, preferences, contract = support.parameters(), support.preferences(), support.installed("contract")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=2, location=(0, 24, 0))
    grid = bpy.context.active_object
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::ramps"], "MODIFIER")
    tree, settings = modifier.node_group, modifier.node_group.houdini_bridge
    check(_interface_state(tree)[1], "a new Houdini modifier hides its node group selector and its Manage panel")
    cook.cook_now(tree, force=True)
    check(_interface_state(tree) == (True, True), "after its first cook, Blender draws none of its inputs %s"
          % (_interface_state(tree),))

    print("what the panel draws: Houdini's order, folders, tabs, multiparm instances, ramps")
    rows = parameters.drawn(tree, every_tab=True)
    drawn = [row[1] for row in rows if row[0] == "parm"]
    shown = [p.name for p in settings.parms if not parameters.is_hidden(p)]
    check(drawn == shown, "every parameter, in Houdini's order, ramps and the multiparm count included %s"
          % drawn)
    check(all(json.loads(p.folder_types) and all(level[0] in contract.FOLDER_TYPES
                                                 for level in json.loads(p.folder_types))
              for p in settings.parms if p.folder), "each folder level has its type, as the server described it")
    check(("folder", "instance", ("Bumps 1",)) in rows, "a multiparm instance is a box %s"
          % [row for row in rows if row[0] == "folder"])
    tabs = _rows(parameters, tree, "tabs")
    check(any(row[3] == ["Look"] for row in tabs), "the folder Look is a tab %s" % tabs)
    check(drawn.index("shape") < drawn.index("bumps") < drawn.index("radius1") < drawn.index("tint"),
          "the ramps at their place, before and after the multiparm")
    _homonym_folders(parameters, tree)

    print("the + and - of a multiparm, then a rebuild: the Houdini interface stays")
    bpy.ops.houdini_bridge.multiparm(tree=tree.name, parm="bumps", step=1)
    check(get_parm(modifier, settings.parms["bumps"]) == [2], "+ adds an instance (the count is 2)")
    support.poll_inputs()
    check(support.is_requested(tree), "which asks for a cook, as a typed count does")
    cook.cook_now(tree)
    check(("folder", "instance", ("Bumps 2",)) in parameters.drawn(tree, every_tab=True)
          and _interface_state(tree) == (True, True),
          "the rebuilt inputs are hidden from Blender, the new instance drawn")
    bpy.ops.houdini_bridge.multiparm(tree=tree.name, parm="bumps", step=-1)
    support.poll_inputs()
    cook.cook_now(tree)
    check(get_parm(modifier, settings.parms["bumps"]) == [1] and "radius2" not in settings.parms,
          "- removes the last instance")

    print("a keyframe and a driver on an input the panel draws")
    socket = settings.parms["height"].socket
    item = support.modifier_input(modifier, socket)
    item.value = 0.5
    item.keyframe_insert("value", frame=1)
    item.value = 1.5
    item.keyframe_insert("value", frame=10)
    paths = [curve.data_path for curve in support.fcurves(grid)]
    expected = 'modifiers["%s"].properties.inputs.%s.value' % (modifier.name, socket)
    check(paths == [expected], "the F-curve is the one Blender makes when it draws the input %s" % paths)
    bpy.context.scene.frame_set(1)
    cook.cook_now(tree)
    bpy.context.scene.frame_set(10)
    check(abs(get_parm(modifier, settings.parms["height"])[0] - 1.5) < 1e-6, "the keyed value follows the frame")
    support.poll_inputs()
    check(support.is_requested(tree), "and asks for a cook")
    cook.cook_now(tree)
    radius = support.modifier_input(modifier, settings.parms["radius1"].socket)
    curve = radius.driver_add("value")
    curve.driver.expression = "frame / 40"
    bpy.context.scene.frame_set(8)
    check(abs(get_parm(modifier, settings.parms["radius1"])[0] - 0.2) < 1e-6
          and abs(support.peek_request(tree)[0]["parms"]["radius1"][0] - 0.2) < 1e-6,
          "a driver is evaluated and sent (%s)" % get_parm(modifier, settings.parms["radius1"]))
    cook.cook_now(tree)
    grid.animation_data_clear()
    bpy.context.scene.frame_set(1)
    cook.cook_now(tree)

    print("the preference Houdini Interface: off, Blender draws the inputs")
    polyextrude = _native_modifier(modifiers, "polyextrude::2.0", (4, 24, 0))
    extrude = polyextrude.node_group
    cook.cook_now(extrude, force=True)
    bpy.context.view_layer.update()
    support.poll_inputs()
    preferences.houdini_interface = False
    check(_interface_state(extrude) == (False, False)
          and _hidden_by_houdini(extrude) == {p.name for p in extrude.houdini_bridge.parms
                                             if p.socket and parameters.is_hidden(p)},
          "off: each input is hidden as Houdini hides it, the selector and Manage show")
    rows = support.installed("parameter_rows")
    ramps = {p.name for p in settings.parms if p.kind == "ramp" and not parameters.is_hidden(p)}
    group_fields = {p.name for p in extrude.houdini_bridge.parms if p.menu_json and p.socket and p.kind == "string"
                    and not parameters.is_hidden(p)}
    check({parm.name for parm, _ in rows.beside_inputs(tree)} == ramps and ramps
          and group_fields and group_fields <= {parm.name for parm, _ in rows.beside_inputs(extrude)},
          "off: the panel still draws what an input cannot show, the ramps %s and the group fields with their "
          "picker and arrow %s" % (sorted(ramps), sorted(group_fields)))
    bpy.context.view_layer.update()
    support.poll_inputs()
    check(support.peek_request(extrude) is None and support.peek_request(tree) is None,
          "and nothing is sent to Houdini")
    preferences.houdini_interface = True
    check(_interface_state(extrude) == (True, True) and _interface_state(tree) == (True, True),
          "on again: the Houdini interface is back")

    print("Poly Extrude: hidden, greyed out, tabs")
    extrude_settings = extrude.houdini_bridge
    names = {row[1]: row[2] for row in parameters.drawn(extrude, every_tab=True) if row[0] == "parm"}
    check("translate" not in names and "xformfront" in names, "a parameter Houdini hides is not drawn")
    greyed = {p.name for p in extrude_settings.parms if p.disabled and not parameters.is_hidden(p)}
    check(greyed and all(names.get(name) for name in greyed),
          "a parameter Houdini greys out is drawn faded %s" % sorted(greyed))
    tabs = _rows(parameters, extrude, "tabs", every_tab=False)
    check(tabs and tabs[0][4] == tabs[0][3][0], "the first tab of a set is open at first %s" % tabs[:1])
    first_tab = {row[1] for row in parameters.drawn(extrude) if row[0] == "parm"}
    key, tab_set, labels, tab_keys = tabs[0][5], tabs[0][2], tabs[0][3], tabs[0][6]
    check(all(tab_keys) and len(set(tab_keys)) == len(tab_keys) and tab_keys != labels,
          "each tab is named by its Houdini folder, not its label %s" % tab_keys)
    bpy.context.view_layer.update()  # what the interface change wrote is evaluated first
    support.poll_inputs()
    support.settle()
    updates = support.count_depsgraph_updates((extrude, polyextrude.id_data))
    bpy.ops.houdini_bridge.folder_tab(tree=extrude.name, path="\t".join(key), tab_set=tab_set, tab=tab_keys[1])
    bpy.context.view_layer.update()
    support.poll_inputs()
    second_tab = {row[1] for row in parameters.drawn(extrude) if row[0] == "parm"}
    opened = [row[4] for row in _rows(parameters, extrude, "tabs", every_tab=False) if row[2] == tab_set]
    check(opened == [labels[1]] and second_tab != first_tab,
          "a click on a tab opens it (%s), and the panel draws its content" % opened)
    count = updates()
    check(count == 0 and support.peek_request(extrude) is None and not support.is_requested(extrude),
          "changing tabs touches no data: no depsgraph update of the modifier, nothing to cook (%d updates)" % count)

    print("Lock: a locked modifier cooks only on Recook")
    settings.live = False
    support.cancel_request(tree)
    cook.start_server()
    check(not support.is_requested(tree), "the server started again: a locked modifier is not cooked")
    settings.output_mode = "MESH"
    check(not support.is_requested(tree), "nor when its output mode changes")
    settings.output_mode = "NATIVE"
    support.mark_released(tree)
    support.on_undo()
    check(not support.is_requested(tree), "nor after an undo that brings it back")
    set_parm(modifier, settings, "height", [0.75])
    support.poll_inputs()
    check(not support.is_requested(tree), "nor when a parameter changes")
    settings.live = True
    check(support.is_requested(tree), "lifting the lock cooks it, as in Houdini")
    support.settle()

    print("navigation: the sidebar shows a modifier in the Properties editor")
    modifier.show_expanded = False
    bpy.ops.houdini_bridge.show_parameters(tree=tree.name)
    check(grid.modifiers.active == modifier and modifier.show_expanded, "the modifier is made active and opened")

    print("Add Modifier: the favorites in a Houdini sub-menu")
    catalog = support.catalog()
    check(support.menu_draws("OBJECT_MT_modifier_add", "_draw_modifier_add_menu"),
          "the Houdini sub-menu is in Blender's Add Modifier menu")
    rings = {"file": HDA, "type": "bridge::rings", "label": "Bridge Rings", "group": "hda", "max_inputs": 0,
             "icon": ""}
    saved_tools = support.replace_catalog([rings])
    try:
        before = len(grid.modifiers)
        with bpy.context.temp_override(object=grid, active_object=grid):
            bpy.ops.houdini_bridge.tool(key=catalog.tool_key(rings), target="MODIFIER")
        check(len(grid.modifiers) == before + 1 and grid.modifiers[-1].node_group.houdini_bridge.node_type
              == "bridge::rings", "a favorite goes on the object as a modifier, a generator too")
    finally:
        support.replace_catalog(*saved_tools)
    support.settle()


def test_lock_inputs(cook, modifiers):
    print("Lock: what changed while a modifier was locked is read when the lock is lifted")
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(40, 4, 0))
    cube = bpy.context.active_object
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=2, y_subdivisions=2, size=2, location=(40, 0, 0))
    grid = bpy.context.active_object
    merge = modifiers.place_hda(bpy.context, "", {"type": "merge", "label": "merge", "max_inputs": 2}, "MODIFIER")
    tree, settings = merge.node_group, merge.node_group.houdini_bridge
    settings.inputs[1].object = cube
    cook.cook_now(tree, force=True)
    before = positions(settings.holder.data)
    settings.live = False
    grid.data.vertices[0].co.z += 1.0
    grid.data.update()
    cube.location.x += 2.0
    bpy.context.view_layer.update()
    check(not support.is_requested(tree), "locked: neither the edit nor the move cooks it")
    settings.live = True
    check(support.is_requested(tree), "lifting the lock asks for a cook")
    cook.cook_now(tree)
    after = positions(settings.holder.data)
    check(len(after) == len(before) and before[:, 2].max() < 0.3 < 0.9 < after[:, 2].max()
          and before[:, 0].max() < 1.1 < 2.1 < after[:, 0].max(),
          "the cook reads the vertex raised and the cube moved while locked (highest z %.2f -> %.2f, x %.2f -> %.2f)"
          % (before[:, 2].max(), after[:, 2].max(), before[:, 0].max(), after[:, 0].max()))
    support.settle()


def test_missing_hda(cook, modifiers, definitions):
    print("a missing HDA file: its error comes first, and a parameter changed once it is back cooks again")
    path = os.path.join(tempfile.mkdtemp(prefix="bridge_missing_"), "moved.hda")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(44, 0, 0))
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    tree, settings = modifier.node_group, modifier.node_group.houdini_bridge
    cook.cook_now(tree, force=True)
    settings.path = path  # as in a file whose HDA was moved away
    cook.cook_now(tree, force=True)
    first = settings.errors.splitlines()[0] if settings.errors else ""
    check(settings.status == "failed" and first == "HDA file not found: %s" % os.path.normpath(path),
          "the error names the missing file on its first line, which the panel shows %r" % first)
    shutil.copyfile(HDA, path)
    set_parm(modifier, settings, "height", [0.9])
    support.poll_inputs()
    check(support.is_requested(tree), "the file back, changing a parameter asks for a cook")
    cook.cook_now(tree)
    check(not settings.errors and settings.status != "failed", "which cooks (%s)" % settings.status)
    error = support.apply_error(tree, "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\n"
                                      "ValueError: the cause")
    check(error.splitlines()[0] == "ValueError: the cause" and "Traceback" in error,
          "an error that is a traceback starts with its cause, then the rest %r" % error)
    cook.cook_now(tree, force=True)


def test_apply_refused(cook, modifiers, definitions):
    print("Apply refused before anything changes: a mesh shared by two objects, a mesh with shape keys")
    for case in ("two users", "shape keys"):
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(48, 0, 0))
        grid = bpy.context.active_object
        modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::scatter"], "MODIFIER")
        tree, settings = modifier.node_group, modifier.node_group.houdini_bridge
        cook.cook_now(tree, force=True)
        twin = None
        if case == "two users":
            twin = bpy.data.objects.new("Twin", grid.data)
            bpy.context.collection.objects.link(twin)
        else:
            grid.shape_key_add(name="Basis")
        objects = set(bpy.data.objects)
        prototypes = settings.prototypes
        try:
            modifiers.apply_modifier(bpy.context, tree)
            refusal = None
        except Exception as error:
            refusal = error
        check(isinstance(refusal, ValueError) and set(bpy.data.objects) == objects and prototypes is not None
              and settings.prototypes == prototypes and grid.modifiers.get(modifier.name) is not None,
              "%s: refused with a message %r, no object made, the shapes and the modifier still there"
              % (case, refusal))
        modifiers.remove_modifier(tree)
        for obj in (twin, grid):
            if obj is not None:
                bpy.data.objects.remove(obj)

    print("Apply refused before anything changes: a volume whose live .vdb file is gone")
    bpy.ops.mesh.primitive_cube_add(size=1, location=(48, 6, 0))
    box = bpy.context.active_object
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::fog"], "MODIFIER")
    tree, settings = modifier.node_group, modifier.node_group.houdini_bridge
    cook.cook_now(tree, force=True)
    path = bpy.path.abspath(settings.volume_holder.data.filepath) if settings.volume_holder else ""
    check(os.path.exists(path), "the fog is cooked into a live .vdb file (%s)" % path)
    if os.path.exists(path):
        os.remove(path)  # as when a file is opened again after the session that wrote it ended
    objects = set(bpy.data.objects)
    try:
        modifiers.apply_modifier(bpy.context, tree)
        refusal = None
    except Exception as error:
        refusal = error
    check(isinstance(refusal, ValueError) and "Recook" in str(refusal) and set(bpy.data.objects) == objects
          and box.modifiers.get(modifier.name), "refused with a message %r, no object made, the modifier still there"
          % refusal)
    cook.cook_now(tree, force=True)
    check(os.path.exists(bpy.path.abspath(settings.volume_holder.data.filepath)), "a recook writes the file again")
    modifiers.remove_modifier(tree)
    bpy.data.objects.remove(box)


def test_driven_parms(cook, modifiers):
    print("a parameter Houdini drives: Time Shift's frame follows $F until it changes in Blender")

    def described(answer):
        return next((p["value"][0] for p in answer.get("parms", []) if p["name"] == "frame"), None)

    def sent(header):
        return header[0]["parms"].get("frame") if header else None

    bpy.context.scene.frame_set(1)
    modifier = _native_modifier(modifiers, "timeshift", (52, 0, 0))
    tree, settings = modifier.node_group, modifier.node_group.houdini_bridge
    cook.cook_now(tree, force=True)
    check(getattr(settings.parms.get("frame"), "expression", False), "its frame is described as driven by Houdini")
    bpy.context.scene.frame_set(10)  # a Time Shift of a still input is not time dependent: the cooks are forced
    header = cook.peek_request(support.target(tree), force=True)
    check(header is not None and sent(header) is None, "at frame 10 the cook sends no value for it %s"
          % (sent(header),))
    frame = described(cook.cook_now(tree, force=True))
    check(frame == 10.0, "and Houdini evaluates $F (%s)" % frame)
    set_parm(modifier, settings, "frame", [5.0])
    support.poll_inputs()
    check(support.is_requested(tree) and sent(support.peek_request(tree)) == [5.0],
          "changed in Blender, it cooks and sends the value")
    frame = described(cook.cook_now(tree))
    check(frame == 5.0 and not settings.parms["frame"].expression, "which replaces the expression (%s)" % frame)
    bpy.context.scene.frame_set(20)
    frame = described(cook.cook_now(tree, force=True))
    check(frame == 5.0, "and holds at frame 20 (%s)" % frame)
    bpy.context.scene.frame_set(1)
    support.settle()


def test_lock_on_load(cook, modifiers, definitions):
    print("a file opened: the modifiers get the Houdini interface, the live ones cook, a locked one keeps its result")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 30, 0))
    grid = bpy.context.active_object
    grid.name = "Locked Rock"
    locked = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    cook.cook_now(locked.node_group, force=True)
    faces = len(locked.node_group.houdini_bridge.holder.data.polygons)
    locked.node_group.houdini_bridge.live = False
    support.settle()
    path = os.path.join(tempfile.mkdtemp(prefix="bridge_lock_"), "locked.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    obj = bpy.data.objects["Locked Rock"]
    tree = obj.modifiers[-1].node_group
    others = [t for t in bpy.data.node_groups if getattr(t, "houdini_bridge", None) and t.houdini_bridge.enabled
              and t != tree and t.houdini_bridge.live and not t.houdini_bridge.use_cache]
    check(not support.is_requested(tree) and len(tree.houdini_bridge.holder.data.polygons) == faces,
          "the locked modifier is not cooked and keeps its result (%d faces)" % faces)
    check(others and all(support.is_requested(t) for t in others), "the live ones are cooked again")
    check(all(_interface_state(t) == (True, True) for t in [tree] + others),
          "every Houdini modifier has the Houdini interface")
    support.settle()


def test_own_materials(cook, modifiers, definitions):
    print("a material set on the object stays on a result Houdini assigns no material to, also in a saved file")
    view_layer = bpy.context.view_layer
    mine, other = bpy.data.materials.new("bridge_own"), bpy.data.materials.new("bridge_own_other")
    mine.use_fake_user = other.use_fake_user = True  # each one is left unused at some point, and must be saved
    grid = _native_modifier(modifiers, "subdivide", (0, 36, 0)).id_data
    grid.name = "Own Material"
    grid.data.materials.append(mine)
    tree = hda(grid)[1]
    cook.cook_now(tree, force=True)
    check(evaluated_local(grid)[1] == {"bridge_own"}, "the result shows the object's material")
    grid.data.vertices[0].co.z += 0.5
    grid.data.update()
    view_layer.update()
    check(bool(cook.cook_now(tree)), "a moved vertex cooks")
    cook.cook_now(tree, force=True)
    check(evaluated_local(grid)[1] == {"bridge_own"}, "it stays, cook after cook")
    grid.material_slots[0].material = other
    view_layer.update()
    check(evaluated_local(grid)[1] == {"bridge_own_other"}, "another material on the object shows without a cook")
    check(support.peek_request(tree) is None, "and sends nothing to Houdini")

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(4, 36, 0))
    painted = bpy.context.active_object
    painted.data.materials.append(mine)
    rock = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER").node_group
    cook.cook_now(rock, force=True)
    painted.material_slots[0].material = other
    view_layer.update()
    check(evaluated_local(painted)[1] == {"rock_dark", "rock_light"}, "the materials Houdini names win, as before")

    wrangled = _native_modifier(modifiers, "attribwrangle", (8, 36, 0))
    slotted = wrangled.id_data
    slotted.data.materials.append(mine)
    slotted.data.materials.append(other)
    wrangle = wrangled.node_group
    cook.cook_now(wrangle, force=True)
    set_parm(wrangled, wrangle.houdini_bridge, "class", "primitive")
    set_parm(wrangled, wrangle.houdini_bridge, "snippet",
             's@shop_materialpath = @primnum % 2 ? "/mat/bridge_odd" : "/mat/bridge_even";')
    cook.cook_now(wrangle)

    def slots():
        attribute = wrangle.houdini_bridge.holder.data.attributes.get("material_index")
        return {item.value for item in attribute.data} if attribute else {0}

    check(slots() == {0, 1}, "a wrangle names two materials, on alternate faces %s" % slots())
    set_parm(wrangled, wrangle.houdini_bridge, "snippet", "")
    cook.cook_now(wrangle)
    check(slots() == {0} and evaluated_local(slotted)[1] == {"bridge_own", "bridge_own_other"},
          "once it names none, every face of the same topology takes the object's first slot %s" % slots())
    modifiers.remove_modifier(wrangle)
    bpy.data.objects.remove(slotted)

    support.settle()
    path = os.path.join(tempfile.mkdtemp(prefix="bridge_materials_"), "materials.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    grid = bpy.data.objects["Own Material"]
    tree = hda(grid)[1]
    check(evaluated_local(grid)[1] == {"bridge_own_other"}, "the file opened again shows it")
    cook.cook_now(tree, force=True)
    check(evaluated_local(grid)[1] == {"bridge_own_other"}, "and keeps it once it cooks again")
    grid.material_slots[0].material = bpy.data.materials["bridge_own"]
    view_layer = bpy.context.view_layer
    view_layer.update()
    check(evaluated_local(grid)[1] == {"bridge_own"}, "the result still follows the object")

    cache = support.cache()
    cache.cache_now(tree, 1, 2)
    check(evaluated_local(grid)[1] == {"bridge_own"}, "a cached frame shows it too")
    grid.material_slots[0].material = bpy.data.materials["bridge_own_other"]
    view_layer.update()
    check(evaluated_local(grid)[1] == {"bridge_own_other"}, "and follows it")
    cache.clear(tree.houdini_bridge)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.objects.active = grid
    bpy.ops.houdini_bridge.apply(tree=tree.name)
    check([m.name for m in grid.data.materials] == ["bridge_own_other"], "Apply keeps it on the mesh")
    support.settle()


def _edit_selection(obj, domain, numbers):
    """Select these elements of an object in Edit Mode, by the API, as a user would click them."""
    edit = bmesh.from_edit_mesh(obj.data)
    for sequence in (edit.verts, edit.edges, edit.faces):
        for element in sequence:
            element.select_set(False)
    sequence = {"VERT": edit.verts, "FACE": edit.faces}[domain]
    sequence.ensure_lookup_table()
    for number in numbers:
        sequence[number].select_set(True)
    edit.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def _edit_selected(obj, domain):
    edit = bmesh.from_edit_mesh(obj.data)
    return [i for i, element in enumerate({"VERT": edit.verts, "FACE": edit.faces}[domain]) if element.select]


def _scene_state(obj):
    """What a group selection must leave as it found it."""
    view_layer = bpy.context.view_layer
    return {
        "active": view_layer.objects.active.name if view_layer.objects.active else None,
        "selected": sorted(o.name for o in view_layer.objects if o.select_get()),
        "mode": obj.mode,
        "select_mode": tuple(bpy.context.scene.tool_settings.mesh_select_mode),
        "modifiers": [(m.name, m.show_viewport) for m in obj.modifiers],
        "hidden": obj.hide_get(),
        "objects": sorted(o.name for o in bpy.data.objects),
        "meshes": sorted(m.name for m in bpy.data.meshes),
        "face_selection": [p.select for p in obj.data.polygons],
    }


def test_group_selection(cook, modifiers):
    print("selecting a group in the viewport: the input itself, no modifier above")
    selection, parameters = support.selection(), support.parameters()
    bpy.ops.object.select_all(action="DESELECT")
    modifier = _native_modifier(modifiers, "groupcreate", (0, 24, 0))
    grid = bpy.context.active_object
    tree = modifier.node_group
    settings = tree.houdini_bridge
    cook.cook_now(tree, force=True)
    field = settings.parms["basegroup"]
    check(parameters.group_selection(field) == {"input": 0, "types": [], "type_parm": "grouptype"},
          "Group Create's Base Group is selected in the viewport, its type given by grouptype %r"
          % field.group_selection)
    set_parm(modifier, settings, "basegroup", "0-3")
    support.poll_inputs()
    cook.cook_now(tree)
    grid.data.polygons[15].select = True  # a selection of the user's own, to be restored
    bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)
    before = _scene_state(grid)

    selection.begin(bpy.context, tree, "basegroup")
    check(selection.active() is not None and grid.mode == "EDIT" and bpy.context.active_object == grid,
          "the grid itself is in Edit Mode")
    check(not modifier.show_viewport and cook.cooks.is_paused(tree), "the modifier is hidden and paused")
    check(tuple(bpy.context.scene.tool_settings.mesh_select_mode) == (False, False, True),
          "face mode: grouptype is primitive")
    check(_edit_selected(grid, "FACE") == [0, 1, 2, 3], "the field's faces are preselected %s"
          % _edit_selected(grid, "FACE"))
    message = selection.message(bpy.context)
    check(message.startswith("Selecting Base Group · ") and " · faces · Enter to confirm · Esc to cancel" in message,
          "the badge names the field, the node and the elements: %s" % message)
    _edit_selection(grid, "FACE", [5, 6, 7, 10])
    text = selection.finish(bpy.context, confirm=True)
    check(text == "5-7 10" and get_parm(modifier, field) == "5-7 10", "Enter writes %r" % text)
    after = _scene_state(grid)
    check(after == before, "everything is as before %s"
          % {key: (before[key], after[key]) for key in before if before[key] != after[key]})
    check(not cook.cooks.is_paused(tree) and support.is_requested(tree), "the modifier resumes and cooks again")
    cook.cook_now(tree)
    made = {o["name"]: o for o in support.properties().outputs(settings)}
    check(not settings.errors and "group1" in made, "Houdini makes the group %s %r" % (sorted(made), settings.errors))
    members = [i for i, item in enumerate(settings.holder.data.attributes["group1"].data) if item.value]
    check(members == [5, 6, 7, 10], "from the selected faces %s" % members)

    print("the type follows the selection mode: points, and grouptype becomes point")
    selection.begin(bpy.context, tree, "basegroup")
    check(_edit_selected(grid, "FACE") == [5, 6, 7, 10], "the faces written are preselected")
    bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)
    _edit_selection(grid, "VERT", [0, 1, 2, 24])
    text = selection.finish(bpy.context, confirm=True)
    check(text == "0-2 24" and get_parm(modifier, settings.parms["grouptype"]) == "point",
          "points written %r, grouptype %r" % (text, get_parm(modifier, settings.parms["grouptype"])))
    check(_scene_state(grid) == before, "everything is as before")

    print("Esc writes nothing; a group name is kept and preselects nothing")
    set_parm(modifier, settings, "basegroup", "top_row 3")
    selection.begin(bpy.context, tree, "basegroup")
    check(_edit_selected(grid, "VERT") == [], "nothing preselected for a group name")
    check("“top_row 3” is kept until Enter" in selection.message(bpy.context), selection.message(bpy.context))
    _edit_selection(grid, "VERT", [4])
    check(selection.finish(bpy.context, confirm=False) is None and get_parm(modifier, field) == "top_row 3",
          "Esc keeps the field %r" % get_parm(modifier, field))
    check(_scene_state(grid) == before, "everything is as before")

    print("another object becoming active ends it, and everything is restored")
    selection.begin(bpy.context, tree, "basegroup")
    other = bpy.data.objects.new("Selection Other", None)
    bpy.context.scene.collection.objects.link(other)
    bpy.context.view_layer.objects.active = other
    reason = selection.is_broken(bpy.context)
    check(reason == "another object became active", "the session sees it: %r" % reason)
    selection.finish(bpy.context, confirm=False)
    bpy.data.objects.remove(other)
    check(_scene_state(grid) == before and get_parm(modifier, field) == "top_row 3", "everything is as before")
    support.cancel_request(tree)

    print("a modifier above: a temporary copy of the mesh Houdini receives")
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(4, 24, 0))
    owner = bpy.context.active_object
    subdivision = owner.modifiers.new("Subdivision", "SUBSURF")
    subdivision.levels = 1
    modifier = modifiers.place_hda(bpy.context, "", {"type": "polyextrude::2.0", "label": "Poly Extrude",
                                                     "max_inputs": 1}, "MODIFIER")
    tree = modifier.node_group
    settings = tree.houdini_bridge
    cook.cook_now(tree, force=True)
    _, sent = cook.peek_request(support.target(tree), force=True)
    before = _scene_state(owner)
    selection.begin(bpy.context, tree, "group")
    copy = bpy.context.active_object
    check(copy != owner and copy.mode == "EDIT" and owner.hide_get(), "a copy is edited, the object is hidden")
    check(subdivision.show_viewport and not modifier.show_viewport, "the modifier above stays, this one is hidden")
    counts = np.empty(len(copy.data.polygons), np.int32)
    copy.data.polygons.foreach_get("loop_total", counts)
    points = np.array([v.co[:] for v in copy.data.vertices], np.float32)
    houdini = np.stack([points[:, 0], points[:, 2], -points[:, 1]], 1)  # as mesh_io sends them
    check(np.array_equal(counts, sent["in0_counts"]) and np.allclose(houdini.ravel(), sent["in0_P"], atol=1e-5),
          "the copy is what a cook sends: %d faces and %d points, in the same order"
          % (len(counts), len(points)))
    check("geometry edits are not kept" in selection.message(bpy.context), selection.message(bpy.context))
    check(tuple(bpy.context.scene.tool_settings.mesh_select_mode) == (False, False, True),
          "Poly Extrude's group takes primitives first")
    _edit_selection(copy, "FACE", [60, 61, 62, 63])
    text = selection.finish(bpy.context, confirm=True)
    check(text == "60-63" and get_parm(modifier, settings.parms["group"]) == "60-63", "Enter writes %r" % text)
    after = _scene_state(owner)
    check(after == before, "the copy is gone, everything is as before %s"
          % {key: (before[key], after[key]) for key in before if before[key] != after[key]})
    set_parm(modifier, settings, "dist", [0.2])
    cook.cook_now(tree)
    check(not settings.errors and len(settings.holder.data.polygons) > 64,
          "Poly Extrude extrudes the selected faces: %d faces from 64 %r %s"
          % (len(settings.holder.data.polygons), settings.errors, settings.status))


def test_group_selection_lock(cook, modifiers):
    """The group selection in a stack: Lock respected when it ends."""
    print("selecting a group: a locked modifier below is not cooked when the selection ends")
    selection = support.selection()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(8, 24, 0))
    owner = bpy.context.active_object
    group = modifiers.place_hda(bpy.context, "", {"type": "groupcreate", "label": "Group Create", "max_inputs": 1},
                                "MODIFIER")
    extrude = modifiers.place_hda(bpy.context, "", {"type": "polyextrude::2.0", "label": "Poly Extrude",
                                                    "max_inputs": 1}, "MODIFIER")
    group_tree, extrude_tree = group.node_group, extrude.node_group
    cook.cook_now(group_tree, force=True)
    cook.cook_now(extrude_tree, force=True)
    extrude_tree.houdini_bridge.live = False  # Lock on the modifier below
    support.cancel_request(extrude_tree)
    selection.begin(bpy.context, group_tree, "basegroup")
    check(cook.cooks.is_paused(extrude_tree), "the locked modifier below is paused with the stack it is in")
    _edit_selection(owner, "FACE", [1, 2])
    selection.finish(bpy.context, confirm=True)
    check(not cook.cooks.is_paused(extrude_tree) and not support.is_requested(extrude_tree),
          "it resumes, and is not asked to cook: locked, it cooks only on Recook")
    check(support.is_requested(group_tree), "the modifier whose field was written cooks")
    group_tree.houdini_bridge.live = False
    support.cancel_request(group_tree)
    selection.begin(bpy.context, group_tree, "basegroup")
    _edit_selection(owner, "FACE", [3])
    selection.finish(bpy.context, confirm=True)
    check(get_parm(group, group_tree.houdini_bridge.parms["basegroup"]) == "3" and not support.is_requested(group_tree),
          "the field of a locked modifier is written, and it is not cooked either")
    group_tree.houdini_bridge.live = extrude_tree.houdini_bridge.live = True
    support.api().remove_modifier(extrude_tree)


def test_group_selection_chain(cook, modifiers, definitions):
    """The group selection under a chained Houdini modifier, and the temporary copy kept out of
    renders."""
    print("selecting a group under a chained Houdini modifier: refused when Houdini numbers it otherwise")
    selection = support.selection()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(12, 24, 0))
    owner = bpy.context.active_object
    rock = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    group = modifiers.place_hda(bpy.context, "", {"type": "groupcreate", "label": "Group Create", "max_inputs": 1},
                                "MODIFIER")
    group_tree = group.node_group
    cook.cook_now(rock.node_group, force=True)
    cook.cook_now(group_tree, force=True)
    check(cook.chained_above(support.target(group_tree)) == rock.node_group
          and not cook.sent.is_renumbered(rock.node_group.session_uid),
          "a rock above, chained: polygons only, the numbers stay")
    selection.begin(bpy.context, group_tree, "basegroup")
    copy = bpy.context.active_object
    check(selection.active() is not None and copy != owner and copy.hide_render,
          "the selection starts, on a copy kept out of renders")
    selection.finish(bpy.context, confirm=False)
    owner.modifiers.remove(rock)
    support.maintain()
    scatter = modifiers.place_hda(bpy.context, HDA, definitions["bridge::scatter"], "MODIFIER")
    bpy.ops.object.modifier_move_to_index(modifier=scatter.name, index=0)
    cook.cook_now(scatter.node_group, force=True)
    cook.cook_now(group_tree, force=True)
    before = _scene_state(owner)
    refused = ""
    try:
        selection.begin(bpy.context, group_tree, "basegroup")
    except support.selection().SelectionError as error:
        refused = str(error)
    check(cook.chained_above(support.target(group_tree)) == scatter.node_group
          and cook.sent.is_renumbered(scatter.node_group.session_uid) and "numbered differently" in refused
          and selection.active() is None and _scene_state(owner) == before,
          "a scatter of instances above: refused, nothing changed (%r)" % refused)
    support.api().remove_modifier(group_tree)
    support.api().remove_modifier(scatter.node_group)


def test_bypass(cook, modifiers, definitions):
    print("bypass: off in the viewport and in renders, nothing cooked, the stack goes on")
    settle, targets, panel = support.settle, support.targets(), support.panel()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=2, location=(0, 12, 0))
    base = bpy.context.active_object
    base_faces = len(base.data.polygons)
    first = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    first_tree, first_settings = first.node_group, first.node_group.houdini_bridge
    cook.cook_now(first_tree, force=True)
    second = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    second_tree, second_settings = second.node_group, second.node_group.houdini_bridge
    cook.cook_now(second_tree, force=True)
    set_parm(second, second_settings, "subdivisions", [0])
    cook.cook_now(second_tree)
    settle()
    check(support.chained_above(second_tree) == first_tree and evaluated(base)[0] == 4 * base_faces,
          "two rocks: the second one is chained on the first (%d faces)" % evaluated(base)[0])
    check(panel.modifier_state(first_tree, first, set()) == "READY", "a cooked modifier is up to date")
    check(set(panel.STATE_ICONS) <= support.state_icon_keys(), "the sidebar loaded every state icon")

    sent = []
    stop_recording = support.record_requests(sent)
    first_uid = str(first_tree.session_uid)
    try:
        bpy.ops.houdini_bridge.bypass(tree=first_tree.name)
        check(not first.show_viewport and not first.show_render and targets.is_bypassed(first),
              "Bypass turns the modifier off in the viewport and in renders")
        check(panel.modifier_state(first_tree, first, set()) == "BYPASSED", "the sidebar says bypassed")
        holder_faces = len(first_settings.holder.data.polygons)
        set_parm(first, first_settings, "subdivisions", [2])
        bpy.context.view_layer.update()
        support.poll_inputs()
        check(settle(), "the engine settles")
        check(cook.cook_now(first_tree, force=True) == {}, "cook_now sends nothing for a bypassed modifier")
        check(len(first_settings.holder.data.polygons) == holder_faces, "its last result stays as it was")
        check(support.chained_above(second_tree) is None, "the modifier below is no longer chained on it")
        check(len(second_settings.holder.data.polygons) == base_faces and evaluated(base)[0] == base_faces,
              "the stack goes on without it: the second rock cooks the grid itself (%d faces)"
              % evaluated(base)[0])

        print("bypass: a render cooks nothing")
        scene = bpy.context.scene
        engine, size = scene.render.engine, (scene.render.resolution_x, scene.render.resolution_y)
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x = scene.render.resolution_y = 32
        scene_camera = scene.camera
        camera = bpy.data.objects.new("bypass camera", bpy.data.cameras.new("bypass camera"))
        scene.collection.objects.link(camera)
        scene.camera = camera
        bpy.ops.render.render()
        scene.camera = scene_camera
        bpy.data.objects.remove(camera)
        scene.render.engine = engine
        scene.render.resolution_x, scene.render.resolution_y = size
        support.poll_inputs()
        check(settle() and not support.is_rendering(), "the render ended")

        print("bypass: Apply and the cache refuse it, Apply All leaves it")
        try:
            modifiers.apply_modifier(bpy.context, first_tree)
            check(False, "a bypassed modifier is not applied")
        except ValueError as error:
            check("bypassed" in str(error), "Apply refuses a bypassed modifier: %s" % error)
        try:
            refused = bpy.ops.houdini_bridge.cache(tree=first_tree.name, action="START")
        except RuntimeError:
            refused = {"CANCELLED"}
        check(refused == {"CANCELLED"} and first_settings.cache_collection is None, "the cache refuses it too")
        cooked = [instance for command, instance in sent if instance == first_uid]
        check(not cooked, "nothing at all was sent to Houdini for the bypassed modifier (%d requests)" % len(cooked))
        check(any(instance == str(second_tree.session_uid) for _, instance in sent),
              "while the modifier below did cook")

        print("bypass off: it cooks again")
        sent.clear()
        bpy.ops.houdini_bridge.bypass(tree=first_tree.name)
        check(first.show_viewport and first.show_render and not targets.is_bypassed(first), "both are back on")
        support.poll_inputs()
        check(support.is_requested(first_tree), "turning the bypass off asks for a cook")
        check(settle() and not first_settings.errors, "it cooks: %r" % first_settings.errors[-200:])
        check(any(instance == first_uid for _, instance in sent), "the cook reaches Houdini")
        check(len(first_settings.holder.data.polygons) == 16 * base_faces,
              "with the value set while bypassed: 2 subdivisions (%d faces)" % len(first_settings.holder.data.polygons))
        check(support.chained_above(second_tree) == first_tree and evaluated(base)[0] == 16 * base_faces,
              "the modifier below is chained on it again (%d faces)" % evaluated(base)[0])
    finally:
        stop_recording()

    print("bypass: one switch off only, from the Properties editor")
    first.show_render = False
    check(not targets.is_bypassed(first) and cook.can_cook(support.target(first_tree)),
          "off in renders only: not bypassed, still cooked for the viewport")
    bpy.ops.houdini_bridge.bypass(tree=first_tree.name)
    check(targets.is_bypassed(first), "a click then bypasses it: both off")
    bpy.ops.houdini_bridge.bypass(tree=first_tree.name)
    check(first.show_viewport and first.show_render, "and a second click turns both on")
    settle()

    print("bypass: keyframed switches")
    for frame, shown in ((1, True), (5, False)):
        first.show_viewport = first.show_render = shown
        first.keyframe_insert("show_viewport", frame=frame)
        first.keyframe_insert("show_render", frame=frame)
    scene.frame_set(5)
    support.poll_inputs()
    check(targets.is_bypassed(first) and not cook.can_cook(support.target(first_tree)), "bypassed at frame 5")
    set_parm(first, first_settings, "subdivisions", [1])
    support.poll_inputs()
    scene.frame_set(1)
    support.poll_inputs()
    check(not targets.is_bypassed(first) and support.is_requested(first_tree), "back at frame 1, it asks for a cook")
    check(settle() and len(first_settings.holder.data.polygons) == 4 * base_faces, "and cooks the new value")
    base.animation_data_clear()

    print("bypass: Apply All applies the stack without a bypassed modifier")
    targets.set_bypassed(first, True)
    support.poll_inputs()
    settle()
    result_faces = len(second_settings.holder.data.polygons)
    bpy.ops.houdini_bridge.apply_all(object=base.name)
    check([m.name for m in base.modifiers] == [first.name] and len(base.data.polygons) == result_faces,
          "the second rock is applied, the bypassed one stays (%s, %d faces)"
          % ([m.name for m in base.modifiers], len(base.data.polygons)))
    support.api().remove_modifier(first_tree)

    print("the HDA's icon in the sidebar")
    icon_file = os.path.join(tempfile.mkdtemp(), "rock.png")
    support.source("icons").write_png(icon_file, 32, bytes([200, 120, 40, 255]) * 32 * 32)
    other_file = os.path.join(os.path.dirname(icon_file), "other.hda")
    saved_tools = support.replace_catalog([
        {"file": other_file, "type": "bridge::rock", "label": "Other Rock", "group": "hda", "max_inputs": 1,
         "icon": ""},
        {"file": HDA, "type": "bridge::rock", "label": "Bridge Rock", "group": "hda", "max_inputs": 1,
         "icon": icon_file},
        {"file": "", "type": "color", "label": "Color", "group": "Houdini", "max_inputs": 1, "icon": ""},
    ])
    try:
        bpy.ops.mesh.primitive_plane_add(location=(0, 16, 0))
        rock = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
        color = modifiers.place_hda(bpy.context, "", {"type": "color", "label": "Color", "max_inputs": 1},
                                    "MODIFIER")
        subdivide = modifiers.place_hda(bpy.context, "", {"type": "subdivide", "label": "Subdivide", "max_inputs": 1},
                                        "MODIFIER")
        catalog = support.catalog()
        tool = catalog.tool_of(rock.node_group.houdini_bridge)
        check(tool is not None and tool["label"] == "Bridge Rock",
              "the modifier's tool comes from its own HDA file, one of two of that type")
        rock_icon = panel.tool_icon(rock.node_group.houdini_bridge)
        check(support.has_tool_icon(icon_file), "the sidebar loads the shelf's icon of the HDA")
        # Blender gives every preview the id 0 in background mode: the id drawn is checked in a window.
        check(rock_icon == ({"icon_value": catalog.icon(tool)} if catalog.icon(tool) else {"icon": "MODIFIER"}),
              "the header takes that icon (%s)" % rock_icon)
        check(catalog.tool_of(color.node_group.houdini_bridge)["icon"] == ""
              and panel.tool_icon(color.node_group.houdini_bridge) == {"icon": "MODIFIER"},
              "a tool without an icon keeps MODIFIER")
        check(catalog.tool_of(subdivide.node_group.houdini_bridge) is None
              and panel.tool_icon(subdivide.node_group.houdini_bridge) == {"icon": "MODIFIER"},
              "a tool the catalog does not list keeps MODIFIER")
        settle()
    finally:
        support.replace_catalog(*saved_tools)


def _stack_state(obj):
    """{modifier: (viewport, render)} of an object: what the Display flag must give back."""
    return {m.name: (m.show_viewport, m.show_render) for m in obj.modifiers}


def test_template(cook, modifiers, definitions):
    print("template flag: stored on the modifier, and what it draws")
    template, targets = support.template(), support.targets()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=2, location=(0, 36, 0))
    base = bpy.context.active_object
    first = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    first_tree, first_settings = first.node_group, first.node_group.houdini_bridge

    def drawn():
        return {target.key: mesh for target, mesh in template.templated()}

    check(not first_settings.template, "a new modifier has no template flag")
    first_settings.template = True
    check(first_tree.session_uid not in drawn(), "not cooked yet: nothing to draw")
    cook.cook_now(first_tree, force=True)
    check(drawn().get(first_tree.session_uid) == first_settings.holder.data,
          "cooked: the wireframe of its holder's mesh, the result it keeps")
    second = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    second_tree, second_settings = second.node_group, second.node_group.houdini_bridge
    cook.cook_now(second_tree, force=True)
    second_settings.template = True
    check({first_tree.session_uid, second_tree.session_uid} <= set(drawn()),
          "two modifiers of one object templated at once")

    targets.set_bypassed(first, True)
    check(first_tree.session_uid not in drawn(), "bypassed: nothing")
    targets.set_bypassed(first, False)
    first_settings.errors = "a Houdini error"  # as a failed cook leaves it
    check(first_tree.session_uid not in drawn(), "in error: nothing")
    first_settings.errors = ""
    base.hide_set(True)
    check(not drawn().keys() & {first_tree.session_uid, second_tree.session_uid}, "the object hidden: nothing")
    base.hide_set(False)
    check(first_tree.session_uid in drawn() and support.template_stats(template)["batches"] == 0,
          "shown again; no batch is made without a viewport (background mode)")
    first_settings.template = second_settings.template = False
    check(not drawn().keys() & {first_tree.session_uid, second_tree.session_uid}, "the flags lifted: nothing")
    support.settle()


def test_display(cook, modifiers, definitions):
    print("display flag: the viewport shows the output of this HDA, the modifiers below are cut")
    display, cuts, targets, panel = support.display(), support.stack_cuts(), support.targets(), support.panel()
    template = support.template()
    settle_display = display.settle_all
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=2, location=(0, 40, 0))
    base = bpy.context.active_object
    base_name, grid_faces = base.name, len(base.data.polygons)
    first = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    first_tree = first.node_group
    subdivision = base.modifiers.new("Subdivision", "SUBSURF")
    subdivision.levels = 1
    second = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    second_tree = second.node_group
    smooth = base.modifiers.new("Smooth", "SMOOTH")
    triangulate = base.modifiers.new("Triangulate", "TRIANGULATE")
    triangulate.show_viewport = False  # the user's own choice, before any flag
    cook.cook_now(first_tree, force=True)
    support.settle()
    cook.cook_now(second_tree, force=True)
    before = _stack_state(base)
    first_faces = len(first_tree.houdini_bridge.holder.data.polygons)
    check(display.cut_by(base, first) == [subdivision, second, smooth, triangulate],
          "the rule: every modifier below the flagged one, whatever its type")

    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    check(display.is_flagged(first) and not display.is_flagged(second), "the flag is on the first rock")
    check([m.show_viewport for m in base.modifiers] == [True, False, False, False, False]
          and all(m.show_render for m in base.modifiers), "the modifiers below are off in the viewport, renders "
          "untouched %s" % _stack_state(base))
    check(evaluated(base)[0] == first_faces, "the viewport shows the output of the first rock (%d faces)"
          % evaluated(base)[0])
    check(display.is_cut(subdivision) and display.is_cut(second) and not display.is_cut(triangulate),
          "a modifier the user had turned off is not held")
    check(cook.can_cook(support.target(second_tree)),
          "a Houdini modifier cut in the viewport only still cooks, for renders (the Bypass rule)")
    check(panel.status_text(second_tree, second).endswith("below the Display flag")
          and panel.bypass_look(second) == "BYPASS_OFF", "a cut modifier says why it is off, and is not "
          "shown partly bypassed (%r, %s)" % (panel.status_text(second_tree, second), panel.bypass_look(second)))
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    check(not display.is_flagged(first) and _stack_state(base) == before and not base.houdini_bridge_cuts,
          "the flag lifted: the stack is restored exactly, nothing left on the object %s" % _stack_state(base))

    print("display flag: one per object, set elsewhere it moves")
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    bpy.ops.houdini_bridge.display(tree=second_tree.name)
    check(display.is_flagged(second) and not display.is_flagged(first), "the flag moved to the second rock")
    check(subdivision.show_viewport and second.show_viewport and not smooth.show_viewport
          and not triangulate.show_viewport, "above it, shown again; below it, cut %s" % _stack_state(base))
    bpy.ops.houdini_bridge.display(tree=second_tree.name)
    check(_stack_state(base) == before, "lifted: the stack as before")

    print("display flag: modifiers moved, added and removed under it")
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    base.modifiers.move(base.modifiers.find(smooth.name), 0)
    settle_display()
    check(smooth.show_viewport and not display.is_cut(smooth), "a modifier moved above the flag is turned back on")
    base.modifiers.move(0, len(base.modifiers) - 1)
    settle_display()
    check(not smooth.show_viewport and display.is_cut(smooth), "moved below it again, it is cut again")
    added = base.modifiers.new("Added", "WELD")
    settle_display()
    check(not added.show_viewport and display.is_cut(added), "a modifier added below is cut")
    base.modifiers.remove(added)
    settle_display()
    check(display.is_flagged(first), "a cut modifier removed: the flag stays")
    print("display flag: a cut modifier turned on by hand lifts the flag")
    subdivision.show_viewport = True
    settle_display()
    check(not display.is_flagged(first) and not base.houdini_bridge_cuts and _stack_state(base) == before,
          "the flag is lifted, the stack restored as before, nothing left on the object %s" % _stack_state(base))

    print("display flag and bypass")
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    bpy.ops.houdini_bridge.bypass(tree=first_tree.name)
    check(targets.is_bypassed(first) and display.is_flagged(first) and not subdivision.show_viewport,
          "the flagged modifier bypassed: the flag stays, the modifiers below stay cut")
    check(evaluated(base)[0] == grid_faces, "the viewport shows its input passed through (%d faces)"
          % evaluated(base)[0])
    bpy.ops.houdini_bridge.bypass(tree=first_tree.name)
    check(not targets.is_bypassed(first) and evaluated(base)[0] == first_faces, "turned back on: its output again")
    bpy.ops.houdini_bridge.bypass(tree=second_tree.name)
    settle_display()
    check(targets.is_bypassed(second) and not display.is_flagged(first) and not display.is_cut(second)
          and subdivision.show_viewport and smooth.show_viewport,
          "a cut modifier bypassed by hand lifts the flag: it stays bypassed, the others come back %s"
          % _stack_state(base))
    bpy.ops.houdini_bridge.bypass(tree=second_tree.name)
    check(_stack_state(base) == before, "its bypass undone: the stack as before")
    support.settle()

    print("display flag and template: independent")
    second_tree.houdini_bridge.template = True
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    check(second_tree.session_uid in {target.key for target, _ in template.templated()},
          "a modifier cut by the Display flag still draws its template")
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    check(second_tree.houdini_bridge.template and _stack_state(base) == before,
          "lifting the Display flag leaves the Template flag")
    second_tree.houdini_bridge.template = False

    print("display flag: a duplicated object carries its flag and its cut")
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    bpy.ops.object.select_all(action="DESELECT")
    base.select_set(True)
    bpy.context.view_layer.objects.active = base
    bpy.ops.object.duplicate()
    copy = bpy.context.active_object
    support.maintain()  # a node group shared by the copy is split
    copy_first = copy.modifiers[0]
    check(copy != base and copy_first.node_group != first_tree and display.is_flagged(copy_first)
          and not copy.modifiers[1].show_viewport, "the copy has the flag on its own first rock, and its cut")
    bpy.ops.houdini_bridge.display(tree=copy_first.node_group.name)
    check(_stack_state(copy) == before and display.is_flagged(first) and not subdivision.show_viewport,
          "lifted on the copy: the copy's stack as before, the original keeps its flag")
    bpy.data.objects.remove(copy)
    support.maintain()
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    check(_stack_state(base) == before, "lifted: the stack as before")
    support.settle()

    print("display flag: undo and redo around setting and lifting it")
    first_name = first_tree.name
    bpy.ops.ed.undo_push(message="Before the Display flag")
    bpy.ops.houdini_bridge.display("EXEC_DEFAULT", True, tree=first_name)
    bpy.ops.ed.undo()
    base = bpy.data.objects[base_name]
    check(not display.is_flagged(base.modifiers[0]) and _stack_state(base) == before,
          "undone: no flag, the stack as before %s" % _stack_state(base))
    bpy.ops.ed.redo()
    base = bpy.data.objects[base_name]
    check(display.is_flagged(base.modifiers[0]) and not base.modifiers[1].show_viewport,
          "redone: the flag and its cut")
    bpy.ops.houdini_bridge.display("EXEC_DEFAULT", True, tree=first_name)
    base = bpy.data.objects[base_name]
    check(_stack_state(base) == before, "lifted")
    bpy.ops.ed.undo()
    base = bpy.data.objects[base_name]
    check(display.is_flagged(base.modifiers[0]) and not base.modifiers[1].show_viewport,
          "the lift undone: the flag and its cut again")
    bpy.ops.ed.redo()
    base = bpy.data.objects[base_name]
    check(not display.is_flagged(base.modifiers[0]) and _stack_state(base) == before,
          "redone: lifted, the stack as before")
    support.settle()
    first, first_tree = base.modifiers[0], bpy.data.node_groups[first_name]
    second = next(m for m in base.modifiers if m.type == "NODES" and m != first)
    second_tree = second.node_group

    print("display flag: Apply refuses what the flag cuts, and Apply All the whole stack, until it is lifted")
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    flagged = _stack_state(base)
    for what, call in (("Apply All", lambda: modifiers.apply_all(bpy.context, base)),
                       ("Apply of a cut Houdini modifier", lambda: modifiers.apply_modifier(bpy.context, second_tree))):
        try:
            call()
            refused = False
        except ValueError as error:
            refused = "lift the flag first" in str(error)
        check(refused and _stack_state(base) == flagged and len(base.modifiers) == len(flagged),
              "%s refused while the flag cuts the stack, nothing applied" % what)
    try:
        said = bpy.ops.houdini_bridge.apply_all(object=base.name) == {"CANCELLED"}
    except RuntimeError as error:  # an operator that reports an error raises in a script
        said = "lift the flag first" in str(error)
    check(said and _stack_state(base) == flagged, "the Apply All button says so and changes nothing")
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    check(_stack_state(base) == before, "lifted again")

    print("display flag: removing or applying the flagged modifier restores the stack")
    bpy.ops.houdini_bridge.display(tree=second_tree.name)
    base.modifiers.remove(second)
    support.maintain()
    settle_display()
    check(not cuts.has(base, display.CUT) and base.modifiers["Smooth"].show_viewport,
          "removed: no flag, the modifiers below it shown again")
    bpy.ops.houdini_bridge.display(tree=first_tree.name)
    modifiers.apply_modifier(bpy.context, first_tree)
    settle_display()
    check(not cuts.has(base, display.CUT) and all(m.show_viewport for m in base.modifiers if m.name != "Triangulate")
          and not base.modifiers["Triangulate"].show_viewport,
          "applied: no flag, the stack restored, the modifier the user turned off still off %s" % _stack_state(base))
    support.settle()


def test_display_and_selection(cook, modifiers, definitions):
    print("display flag and viewport group selection: one mechanism, each restores only what it cut")
    display, selection = support.display(), support.selection()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 44, 0))
    owner = bpy.context.active_object
    rock = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    group = modifiers.place_hda(bpy.context, "", {"type": "groupcreate", "label": "Group Create", "max_inputs": 1},
                                "MODIFIER")
    smooth = owner.modifiers.new("Smooth", "SMOOTH")
    rock_tree, group_tree = rock.node_group, group.node_group
    cook.cook_now(rock_tree, force=True)
    cook.cook_now(group_tree, force=True)
    before = _stack_state(owner)

    bpy.ops.houdini_bridge.display(tree=rock_tree.name)
    selection.begin(bpy.context, group_tree, "basegroup")
    check(selection.active() is not None and not group.show_viewport and not smooth.show_viewport,
          "a selection begun under the flag")
    selection.finish(bpy.context, confirm=False)
    check(display.is_flagged(rock) and not group.show_viewport and not smooth.show_viewport,
          "the selection ended: the flag's cut stays")
    bpy.ops.houdini_bridge.display(tree=rock_tree.name)
    check(_stack_state(owner) == before, "the flag lifted: the stack as before %s" % _stack_state(owner))

    selection.begin(bpy.context, group_tree, "basegroup")
    bpy.ops.houdini_bridge.display(tree=rock_tree.name)
    check(display.is_flagged(rock) and display.is_cut(group) and display.is_cut(smooth),
          "the flag set during a selection holds what it cuts too")
    selection.finish(bpy.context, confirm=False)
    check(rock.show_viewport and not group.show_viewport and not smooth.show_viewport,
          "the selection ended: the flag's cut stays %s" % _stack_state(owner))
    bpy.ops.houdini_bridge.display(tree=rock_tree.name)
    check(_stack_state(owner) == before and not owner.houdini_bridge_cuts,
          "the flag lifted: the stack as before, nothing left on the object %s" % _stack_state(owner))
    support.settle()


def test_auto_run(cook, modifiers, definitions):
    print("Auto Run Python Scripts off: an opened file cooks nothing and starts no Houdini until it is allowed")
    trust = support.installed("trust")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 42, 0))
    bpy.context.active_object.name = "Untrusted Rock"
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    cook.cook_now(modifier.node_group, force=True)
    faces = len(modifier.node_group.houdini_bridge.holder.data.polygons)
    support.settle()
    folder = tempfile.mkdtemp(prefix="bridge_trust_")
    path = os.path.join(folder, "untrusted.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    preferences = bpy.context.preferences
    link = support.link()

    def open_untrusted():
        cook.stop_server()
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        return bpy.data.objects["Untrusted Rock"].modifiers[-1].node_group

    preferences.filepaths.use_scripts_auto_execute = False
    try:
        tree = open_untrusted()
        check(trust.is_blocked() and not support.is_requested(tree), "the file is blocked and asks for no cook")
        cook.cooks.request(tree)  # as a moved input or a changed parameter would
        support.settle()
        check(not link.server.is_running() and not support.is_requested(tree),
              "a cook asked for is dropped, and hython does not start")
        check(len(tree.houdini_bridge.holder.data.polygons) == faces, "the result saved with the file shows")
        check(support.status().state()[0] == "STOPPED", "the header indicator says nothing runs")
        result = bpy.ops.houdini_bridge.allow_file()
        check(result == {"FINISHED"} and not trust.is_blocked() and support.is_requested(tree),
              "Allow Execution allows the file and cooks its live modifiers")
        settled = support.settle()
        check(settled and link.server.is_running() and not tree.houdini_bridge.errors
              and len(tree.houdini_bridge.holder.data.polygons) == faces, "the file cooks again %r"
              % tree.houdini_bridge.errors[-200:])
        check(bpy.ops.houdini_bridge.allow_file.poll() is False, "and Allow Execution is not offered any more")

        tree = open_untrusted()
        check(trust.is_blocked(), "opened again, it is blocked again")
        bpy.ops.houdini_bridge.recook(tree=tree.name)
        check(not trust.is_blocked() and support.is_requested(tree), "Recook, the user's own action, allows it")
        support.settle()

        preferences.filepaths.use_scripts_auto_execute = True
        bpy.ops.preferences.autoexec_path_add()
        preferences.autoexec_paths[-1].path = folder.upper() + os.sep
        open_untrusted()
        check(trust.is_blocked(), "Auto Run on, a file in an Excluded Path is blocked (compared without case)")
        bpy.ops.preferences.autoexec_path_remove(index=len(preferences.autoexec_paths) - 1)
        tree = open_untrusted()
        check(not trust.is_blocked() and support.is_requested(tree), "Auto Run on, a file elsewhere cooks at once")
    finally:
        preferences.filepaths.use_scripts_auto_execute = True
        while len(preferences.autoexec_paths):
            bpy.ops.preferences.autoexec_path_remove(index=0)
    support.settle()


def test_display_files(definitions):
    print("display flag: a file saved with the flag, opened again with and without the extension")
    cook, modifiers, display = support.cook(), support.modifiers(), support.display()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 52, 0))
    owner = bpy.context.active_object
    rock = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    owner.modifiers.new("Smooth", "SMOOTH")
    cook.cook_now(rock.node_group, force=True)
    names = owner.name, rock.name, rock.node_group.name
    rock.node_group.houdini_bridge.template = True
    bpy.ops.houdini_bridge.display(tree=rock.node_group.name)
    path = os.path.join(tempfile.mkdtemp(), "display_flag.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    owner = bpy.data.objects[names[0]]
    rock, smooth = owner.modifiers[names[1]], owner.modifiers["Smooth"]
    check(display.is_flagged(rock) and not smooth.show_viewport and smooth.show_render
          and bpy.data.node_groups[names[2]].houdini_bridge.template,
          "opened again: the flag, its cut and the template flag are there")
    bpy.ops.houdini_bridge.display(tree=names[2])
    check(smooth.show_viewport and not owner.houdini_bridge_cuts, "lifted: the stack is restored")
    bpy.ops.houdini_bridge.display(tree=names[2])  # left set: test_reenable disables the extension with it

    # A Blender of its own, where the extension is not enabled (factory settings), opens the file.
    script = ("import bpy, json; o = bpy.data.objects[%r]; m = o.modifiers['Smooth']; "
              "kept = bool(o.bl_system_properties_get().get('houdini_bridge_cuts')); "
              "print('DISPLAY_FILE', json.dumps([m.show_viewport, m.show_render, kept]))" % names[0])
    run = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", path, "--python-expr", script],
                         capture_output=True, text=True, timeout=180)
    line = next((line for line in run.stdout.splitlines() if line.startswith("DISPLAY_FILE ")), "")
    found = json.loads(line.split(" ", 1)[1]) if line else None
    check(found == [False, True, True], "opened without the extension: the modifiers below stay off in the viewport "
          "as saved, renders keep them, and the cut is kept in the file %s" % found)


def test_preferences(cook, modifiers, definitions):
    print("preferences: Houdini versions")
    hython, catalog = support.hython(), support.catalog()
    preferences = support.preferences()
    installations = hython.find_installations()
    check(installations and installations[0][1] == hython.find_hython(),
          "installed versions found, newest first %s" % installations)
    check(os.path.normcase(support.link().server.running_hython()) == os.path.normcase(installations[0][1]),
          "the latest one runs when none is chosen")

    print("preferences: hidden tools")
    saved_tools = support.replace_catalog([{"file": HDA, "type": t, "label": d["label"], "group": "hda",
                                            "max_inputs": d["max_inputs"]} for t, d in definitions.items()])
    preferences.hidden_tools = "bridge::r*, Bridge Fog"
    listed = [tool["type"] for tool in catalog.listed_tools()]
    check(not any(t.startswith("bridge::r") or t == "bridge::fog" for t in listed) and "bridge::grass" in listed,
          "hidden by type or name, with a wildcard (%d listed)" % len(listed))
    window_manager = bpy.context.window_manager
    window_manager.houdini_bridge_search = "bridge"
    found = [tool["type"] for tool in catalog.visible_tools(window_manager)]
    check("bridge::rock" not in found and "bridge::grass" in found, "the search leaves them out too")
    window_manager.houdini_bridge_search = ""
    preferences.hidden_tools = ""
    support.replace_catalog(*saved_tools)

    print("preferences: sphere detail")
    bpy.ops.mesh.primitive_plane_add(size=2, location=(0, -14, 0))
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::scatter"], "MODIFIER")
    tree = modifier.node_group
    settings = tree.houdini_bridge
    cook.cook_now(tree, force=True)
    set_parm(modifier, settings, "count", [40])
    cook.cook_now(tree, force=True)

    def sphere_faces():
        shapes = [obj.data for obj in settings.prototypes.objects]
        return max(len(mesh.polygons) for mesh in shapes if len(mesh.polygons) > 100) if shapes else 0

    detailed = sphere_faces()
    preferences.sphere_frequency = 2
    check(support.is_requested(tree), "a new sphere detail recooks the modifiers with instances")
    cook.cook_now(tree, force=True)
    coarse = sphere_faces()
    check(0 < coarse < detailed, "the sphere instances follow the detail (%d → %d faces)" % (detailed, coarse))
    preferences.sphere_frequency = 4
    support.clear_requests()

    print("preferences: curve radius")
    preferences.curve_radius = 0.05
    bpy.ops.mesh.primitive_plane_add(size=2, location=(4, -14, 0))
    grass = modifiers.place_hda(bpy.context, HDA, definitions["bridge::grass"], "MODIFIER").node_group
    cook.cook_now(grass, force=True)
    curves = grass.houdini_bridge.curves_holder.data
    values = np.empty(len(curves.points), np.float32)
    curves.attributes["radius"].data.foreach_get("value", values)
    check(abs(float(values.max()) - 0.005) < 1e-4, "a width attribute still wins over the default (%.4f)"
          % float(values.max()))
    preferences.curve_radius = 0.005

    print("preferences: applied volumes")
    folder = tempfile.mkdtemp(prefix="bridge_volumes_")

    def applied_fog(mode):
        preferences.applied_volumes = mode
        bpy.ops.mesh.primitive_cube_add(size=1, location=(8, -14, 0))
        cube = bpy.context.active_object
        tree = modifiers.place_hda(bpy.context, HDA, definitions["bridge::fog"], "MODIFIER").node_group
        cook.cook_now(tree, force=True)
        name = cube.name
        bpy.ops.houdini_bridge.apply(tree=tree.name)
        return bpy.data.objects[name]

    waiting = applied_fog("BESIDE") if not bpy.data.filepath else None
    preferences.applied_folder = folder
    in_folder = applied_fog("FOLDER")
    path = bpy.path.abspath(in_folder.data.filepath)
    check(os.path.normcase(os.path.dirname(path)) == os.path.normcase(folder) and os.path.exists(path),
          "In a Folder: written there (%s)" % path)
    preferences.applied_folder = ""  # In a Folder, but none set: as Next to the .blend, its first save included
    blend = os.path.join(folder, "scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    if waiting is not None:
        moved = bpy.path.abspath(waiting.data.filepath)
        check(waiting.data.filepath.startswith("//houdini_volumes") and os.path.exists(moved)
              and waiting.data.grids.load(),
              "the first save moves a waiting volume next to the .blend, also In a Folder with none set (%s)"
              % waiting.data.filepath)
    preferences.applied_volumes = "BESIDE"
    check(in_folder.data.filepath == bpy.path.relpath(path)
          or os.path.exists(bpy.path.abspath(in_folder.data.filepath)),
          "a volume of another folder stays where it is")
    beside = applied_fog("BESIDE")
    check(beside.data.filepath.startswith("//houdini_volumes")
          and os.path.exists(bpy.path.abspath(beside.data.filepath)),
          "Next to the .blend: a relative path into houdini_volumes (%s)" % beside.data.filepath)

    print("preferences: stop when idle")
    live_folder = support.streams().volume_dir()
    os.makedirs(live_folder, exist_ok=True)
    orphan, kept = (os.path.join(live_folder, "volume_test_%s_1.vdb" % name) for name in ("orphan", "kept"))
    for path in (orphan, kept):
        with open(path, "wb") as file:
            file.write(b"not a volume")
    bpy.data.volumes.new("Kept Volume").filepath = kept
    # The folders of other Blenders, named by their process ids: one still running, one ended.
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    others = {}
    for name, process in (("running", running), ("ended", ended)):
        folder = os.path.join(os.path.dirname(live_folder), str(process.pid))
        os.makedirs(folder, exist_ok=True)
        others[name] = os.path.join(folder, "volume_test_%s_1.vdb" % name)
        with open(others[name], "wb") as file:
            file.write(b"not a volume")
    preferences.idle_minutes = 1
    support.simulate_idle(2)
    check(support.link().server.running_hython() is None and "Stopped after 1 min" in support.link().server.status()[0],
          "Houdini stops after the idle time (%s)" % support.link().server.status()[0])
    preferences.idle_minutes = 0
    cook.cook_now(tree, force=True)
    check(support.link().server.running_hython() is not None and not settings.errors,
          "and starts again on the next cook")
    check(not os.path.exists(orphan) and os.path.exists(kept),
          "a new session removes the live .vdb files no volume reads, and keeps the one a volume reads")
    check(os.path.exists(others["running"]) and not os.path.exists(os.path.dirname(others["ended"])),
          "it leaves the folder of another Blender still running, and removes the one of a Blender that ended")
    running.kill()
    running.wait()
    shutil.rmtree(os.path.dirname(others["running"]), ignore_errors=True)
    bpy.data.volumes.remove(bpy.data.volumes["Kept Volume"])
    os.remove(kept)


def test_api(definitions):
    print("scripting: placed, set, cooked, cached and applied through api.py alone")
    api = support.api()
    check(api.start_server(), "api.start_server: Houdini answers")
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=2, location=(0, -40, 0))
    grid = bpy.context.active_object
    faces = len(grid.data.polygons)
    modifier = api.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    check([found for found, _ in api.houdini_modifiers(grid)] == [modifier],
          "api.place_hda puts a Houdini modifier on the active object, and api.houdini_modifiers lists it")
    tree = modifier.node_group
    settings = tree.houdini_bridge
    api.cook_now(tree, force=True)
    mesh = settings.holder.data
    check(not settings.errors and len(mesh.polygons) == 4 * faces,
          "api.cook_now writes the result: %d faces for %d" % (len(mesh.polygons), faces))
    api.set_parameter(modifier, "subdivisions", [2])
    check(api.cook_now(tree) != {} and len(mesh.polygons) == 16 * faces,
          "a value set by api.set_parameter is sent by the next api.cook_now (%d faces)" % len(mesh.polygons))
    check(api.cook_now(tree) == {}, "api.cook_now sends nothing when nothing changed")

    bpy.ops.object.select_all(action="DESELECT")
    rings = api.place_hda(bpy.context, HDA, definitions["bridge::rings"], "NEW")
    _, rings_tree = api.houdini_modifiers(rings)[0]
    api.cook_now(rings_tree, force=True)
    status = api.cache_now(rings_tree, 1, 2)
    check(rings_tree.houdini_bridge.use_cache and support.cache().frame_count(rings_tree.houdini_bridge) == 2,
          "a generator placed as a new object, cached by api.cache_now (%s)" % status)

    obj, made, _ = api.apply_modifier(bpy.context, tree)
    check(obj == grid and not api.houdini_modifiers(grid) and len(grid.data.polygons) == 16 * faces and not made,
          "api.apply_modifier: the result is the object's mesh (%d faces)" % len(grid.data.polygons))

    holder = rings_tree.houdini_bridge.holder.name
    api.remove_modifier(rings_tree)
    check(not api.houdini_modifiers(rings) and holder not in bpy.data.objects and not rings_tree.houdini_bridge.enabled,
          "api.remove_modifier: the modifier is gone and released at once, its holder deleted")


def test_registration():
    print("registration: the names stored in files and keymaps do not move")
    found = json.dumps(support.describe_registration(), indent=1, sort_keys=True) + "\n"
    if os.environ.get("BRIDGE_UPDATE_SNAPSHOT"):
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        with open(SNAPSHOT, "w", encoding="utf-8", newline="\n") as file:
            file.write(found)
        print("   snapshot rewritten:", SNAPSHOT)
    try:
        with open(SNAPSHOT, encoding="utf-8") as file:
            expected = file.read()
    except OSError:
        expected = ""
    if found != expected:
        difference = difflib.unified_diff(expected.splitlines(), found.splitlines(), "registration.json", "registered",
                                          lineterm="", n=2)
        print("\n".join("     " + line for line in list(difference)[:200]))
    check(found == expected, "what the extension registers matches tests/snapshots/registration.json "
          "(BRIDGE_UPDATE_SNAPSHOT=1 rewrites it when the change is intended)")
    described = json.loads(found)
    check(len(described["operators"]) > 20 and len(described["property_groups"]) >= 6
          and len(described["attached_properties"]) >= 5 and len(described["hotkeys"]) == 2,
          "the snapshot covers operators, property groups, attached properties and hotkeys")
    package = support.package_name()
    check(package == support.INSTALLED_PACKAGE and package in bpy.context.preferences.addons,
          "paths.PACKAGE is the name the extension is enabled under (%s)" % package)


def test_temp_data():
    print("temporary data: what temp_data() gives outlives its block (Blender 5.2 owed it a reference)")
    assets = support.assets()
    for _ in range(3):
        with assets.temp_data() as data:
            data.objects.new("Probe", None)
        alone = object()
        check(sys.getrefcount(data) == sys.getrefcount(alone),
              "after the block, its name alone holds the data (%d references)" % (sys.getrefcount(data) - 1))
        # Cyclic garbage collected while that name holds the data: Blender 5.2 crashed here.
        cycle = type("Cycle", (), {})()
        cycle.itself = cycle
        del cycle
        gc.collect()
        check("invalid" in repr(data), "a collection passes, and the data reads as freed by Blender %s" % repr(data))
        del data


def test_pointer_caches(cook):
    print("caches keyed by mesh pointer: an address may belong to another mesh after an undo")
    caches = support.pointer_caches()
    check(caches["written topologies"] > 0, "the cooks have filled them %s" % caches)
    support.run_undo_handlers()
    caches = support.pointer_caches()
    check(not any(caches.values()), "an undo empties them %s" % caches)
    live = [target for target in support.targets().all_targets() if target.valid and not target.settings.use_cache]
    if live:
        cook.cook_now(live[0].tree, force=True)
    check(support.pointer_caches()["written topologies"] > 0, "the next write fills them again")


def test_compat(cook):
    print("compatibility: a file saved before 1.0 opens, keeps its values and cooks again")
    if not (os.path.exists(FIXTURE) and os.path.exists(FIXTURE_EXPECTED)):
        check(False, "tests/fixtures/compat_0_17.blend and .json exist")
        return
    with open(FIXTURE_EXPECTED, encoding="utf-8") as file:
        expected = json.load(file)
    bpy.ops.wm.open_mainfile(filepath=FIXTURE, load_ui=False)
    caches = support.pointer_caches()
    check(not any(caches.values()), "opening a file empties the caches keyed by mesh pointer %s" % caches)
    scene = bpy.context.scene
    scene.frame_set(expected["frame"])
    found = support.describe_houdini_scene()
    check(sorted(found) == sorted(expected["objects"]), "the objects carrying Houdini modifiers %s" % sorted(found))
    houdini_trees = [t for t in bpy.data.node_groups if getattr(t, "houdini_bridge", None) and t.houdini_bridge.enabled]
    check(houdini_trees and all(_interface_state(t) == (True, True) for t in houdini_trees),
          "the file saved before 1.0 gets the Houdini interface when it opens: the extension's panel draws its inputs")
    for name, entry in sorted(expected["objects"].items()):
        here = found.get(name, {})
        difference = support.differences(entry.get("groups"), here.get("groups"))
        check(not difference, "%s: the Groups panel is read back %s" % (name, difference[:4]))
        mine = here.get("modifiers", [])
        check(len(mine) == len(entry["modifiers"]), "%s: %d Houdini modifiers" % (name, len(entry["modifiers"])))
        for wanted, modifier in zip(entry["modifiers"], mine, strict=False):
            for section in ("hda", "settings", "inputs", "parms", "values", "streams", "result", "cache"):
                difference = support.differences(wanted.get(section), modifier.get(section))
                check(not difference,
                      "%s › %s: %s read back %s" % (name, wanted["hda"]["name"], section, difference[:4]))

    print("compatibility: keyframes, cache")
    animated = expected["animated"]
    obj = bpy.data.objects[animated["object"]]
    modifier = obj.modifiers[animated["modifier"]]
    parm = modifier.node_group.houdini_bridge.parms[animated["parm"]]
    values = {}
    for frame, value in sorted(animated["frames"].items()):
        scene.frame_set(int(frame))
        values[frame] = get_parm(modifier, parm)
        check(not support.differences(value, values[frame]),
              "the keyed %s is %s at frame %s (%s)" % (animated["parm"], value, frame, values[frame]))
    check(len({json.dumps(value) for value in values.values()}) == len(values),
          "and changes from one frame to the next")
    scene.frame_set(expected["frame"])

    cached = expected["cached"]
    cache_object = bpy.data.objects[cached["object"]]
    _, cache_tree = hda(cache_object)
    shapes = []
    for frame in cached["frames"]:
        scene.frame_set(frame)
        shapes.append(evaluated(cache_object)[1].copy())
    check(all(len(shape) == cached["points"] for shape in shapes),
          "the cache plays without Houdini: %d points on each cached frame" % cached["points"])
    check(not np.allclose(shapes[0], shapes[-1]), "and the frames differ")
    check(not support.is_requested(cache_tree), "a cached modifier asks for no cook")
    scene.frame_set(expected["frame"])

    print("compatibility: every live modifier cooks again")
    for name, entry in sorted(expected["objects"].items()):
        obj = bpy.data.objects[name]
        for wanted in entry["modifiers"]:
            if wanted["settings"]["use_cache"]:
                continue
            tree = obj.modifiers[wanted["hda"]["name"]].node_group
            cook.cook_now(tree, force=True)
            check(not tree.houdini_bridge.errors,
                  "%s › %s cooks: %r" % (name, wanted["hda"]["name"], tree.houdini_bridge.errors[-300:]))
    found = support.describe_houdini_scene()
    for name, entry in sorted(expected["objects"].items()):
        for wanted, modifier in zip(entry["modifiers"], found.get(name, {}).get("modifiers", []), strict=False):
            for section in ("values", "streams", "result"):
                difference = support.differences(wanted.get(section), modifier.get(section))
                check(not difference, "%s › %s: after the cook, %s unchanged %s"
                      % (name, wanted["hda"]["name"], section, difference[:4]))


def test_reenable(definitions):
    print("disable and enable the extension again")
    old_link = support.link()
    functions = support.extension_functions()
    handlers = support.extension_handlers()
    registration = support.describe_registration()
    timers = support.registered_timers(functions)
    check(len(handlers) >= 8 and "scheduler.tick" in timers,
          "while enabled, its handlers and timers are installed (%d handlers, timers %s)" % (len(handlers), timers))
    signals = support.signals()
    listening = support.listeners(signals)
    check(listening == LISTENERS, "while enabled, each signal has its listeners, once %s" % listening)
    houdini_trees = [t for t in bpy.data.node_groups if getattr(t, "houdini_bridge", None) and t.houdini_bridge.enabled]
    hidden_by_houdini = {t.name: {p.socket for p in t.houdini_bridge.parms if p.socket and p.hidden}
                         for t in houdini_trees}
    check(houdini_trees and all(_interface_state(t) == (True, True) for t in houdini_trees),
          "while enabled, the Houdini modifiers have the Houdini interface")
    display = support.display()
    cut = [(obj.name, m.name) for obj in bpy.data.objects for m in obj.modifiers if display.is_cut(m)]
    check(cut, "while enabled, a Display flag cuts modifiers (test_display_files left one) %s" % cut)
    support.disable()
    check(all(bpy.data.objects[name].modifiers[modifier].show_viewport for name, modifier in cut)
          and not any((obj.bl_system_properties_get() or {}).get("houdini_bridge_cuts") for obj in bpy.data.objects),
          "disabled with a Display flag set: the flag is lifted, the stack restored %s" % cut)
    blender_draws = []
    for name, hidden in hidden_by_houdini.items():
        tree = bpy.data.node_groups[name]
        sockets = {i.identifier: i.hide_in_modifier for i in tree.interface.items_tree
                   if i.item_type == "SOCKET" and i.in_out == "INPUT" and i.socket_type != "NodeSocketGeometry"}
        users = [m for o in bpy.data.objects for m in o.modifiers if m.type == "NODES" and m.node_group == tree]
        blender_draws.append({k for k, v in sockets.items() if v} == hidden
                         and all(m.show_group_selector and m.show_manage_panel for m in users))
    check(blender_draws and all(blender_draws), "disabled: Blender draws the inputs of every Houdini modifier again, "
          "with its selector and Manage")
    check(not hasattr(bpy.types, "HOUDINI_BRIDGE_PT_modifier")
          and not support.menu_draws("OBJECT_MT_modifier_add", "_draw_modifier_add_menu"),
          "disabled: no modifier panel, no Houdini sub-menu in Add Modifier")
    left = {name: names for name, names in support.listeners(signals).items() if names}
    check(not left, "disabled: no function listens to a signal any more %s" % left)
    check(support.extension_handlers() == [], "disabled: no handler left %s" % support.extension_handlers())
    leftover = support.registered_timers(functions)
    check(not leftover, "disabled: no timer left %s" % leftover)
    check("houdini_bridge" not in bpy.types.GeometryNodeTree.bl_rna.properties,
          "disabled: GeometryNodeTree has no houdini_bridge any more")
    check(not hasattr(bpy.types, "HOUDINI_BRIDGE_PT_hda"), "disabled: the panels are gone")
    check(old_link.server.running_hython() is None, "disabled: Houdini is stopped")
    threads = support.bridge_threads()
    check(not threads, "disabled: no houdini-bridge thread is left %s" % threads)

    support.enable()
    support.redirect_user_dir()
    cook, modifiers = support.cook(), support.modifiers()
    check("houdini_bridge" in bpy.types.GeometryNodeTree.bl_rna.properties, "enabled again: the settings are back")
    check(support.extension_handlers() == handlers, "the same handlers are back")
    check(support.describe_registration() == registration, "everything is registered again, identically")
    support.panel().apply_houdini_interface_after_register()  # the timer that background mode does not run
    states = {name: _interface_state(bpy.data.node_groups[name]) for name in hidden_by_houdini}
    check(all(found == (True, True) for found in states.values()),
          "enabled again: the Houdini modifiers have the Houdini interface again %s"
          % {name: found for name, found in states.items() if found != (True, True)})
    listening = support.listeners(support.signals())
    check(listening == LISTENERS, "enabled again: each signal has its listeners, once %s" % listening)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, -30, 0))
    grid = bpy.context.active_object
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    tree = modifier.node_group
    cook.cook_now(tree, force=True)
    faces = len(tree.houdini_bridge.holder.data.polygons) if tree.houdini_bridge.holder else 0
    check(not tree.houdini_bridge.errors and faces == 4 * len(grid.data.polygons),
          "a new Houdini modifier cooks (%d faces) %r" % (faces, tree.houdini_bridge.errors[-200:]))


def test_auto_run_reload(definitions):
    print("Auto Run off: a blocked file stays blocked when the extension is installed, updated or reloaded")
    cook, modifiers = support.cook(), support.modifiers()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 48, 0))
    bpy.context.active_object.name = "Reloaded Rock"
    modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    cook.cook_now(modifier.node_group, force=True)
    support.settle()
    path = os.path.join(tempfile.mkdtemp(prefix="bridge_trust_"), "reloaded.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    preferences = bpy.context.preferences
    cook.stop_server()
    preferences.filepaths.use_scripts_auto_execute = False
    try:
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        check(support.installed("trust").is_blocked(), "the file opens blocked")
        support.reload(auto_run=False)  # its modules imported anew, and no load_post
        support.redirect_user_dir()
        cook, link = support.cook(), support.link()
        tree = bpy.data.objects["Reloaded Rock"].modifiers[-1].node_group
        support.panel().apply_houdini_interface_after_register()  # the timer that background mode does not run
        cook.cooks.request(tree)  # as the depsgraph update that interface causes would
        support.settle()
        check(support.installed("trust").is_blocked(), "reloaded with the file open, it is still blocked")
        check(not link.server.is_running() and link.server.running_hython() is None
              and not support.is_requested(tree), "nothing cooks: the cook is dropped and hython does not start")
    finally:
        preferences.filepaths.use_scripts_auto_execute = True
    support.cook().allow_file()
    support.settle()


def finish():
    """Stop the server, then leave Blender with the verdict as exit code."""
    support.cook().stop_server()
    print("failures:", len(support.failures))
    sys.stdout.flush()
    os._exit(1 if support.failures else 0)


def main():
    support.enable()
    cook, modifiers, catalog = support.cook(), support.modifiers(), support.catalog()
    temporary = support.redirect_user_dir()  # never the user's catalog, favorites or applied volumes

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)

    start = time.perf_counter()
    cook.ensure_server(blocking=True)
    print("server ready in %.1f s" % (time.perf_counter() - start))
    definitions = support.definitions()

    only = os.environ.get("BRIDGE_TEST")  # e.g. BRIDGE_TEST=ramps_multiparms,streams: just those
    if only:
        for name in only.split(","):
            test = globals()["test_" + name.strip()]
            test(*[{"cook": cook, "modifiers": modifiers, "definitions": definitions}[arg]
                   for arg in test.__code__.co_varnames[:test.__code__.co_argcount]])
        finish()

    test_registration()
    test_temp_data()

    print("connection indicator")
    status = support.status()
    check(status.state()[0] == "READY", "the header indicator says connected (%s)" % status.state()[0])
    check(set(status.COLORS) <= support.status_icon_keys(), "one colored dot per state")

    print("new object: rock on a grid moved to x = 3")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=10, size=2, location=(3, 0, 0))
    grid = bpy.context.active_object
    input_faces = len(grid.data.polygons)
    rock = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::rock"])
    modifier, tree = hda(rock)
    settings = tree.houdini_bridge
    check(len(rock.data.vertices) == 0, "the new object is an empty mesh carrying a Houdini modifier")
    check(settings.inputs[0].object == grid and not settings.inputs[0].is_self, "the selected grid becomes input 1")
    cook.cook_now(tree, force=True)
    mesh = settings.holder.data
    check(not settings.errors, "no error: %r" % settings.errors)
    check(len(mesh.polygons) == 4 * input_faces, "1 subdivision: %d faces for %d" % (len(mesh.polygons), input_faces))
    check(evaluated(rock)[0] == len(mesh.polygons), "the object shows the result")
    names = [p.name for p in settings.parms]
    check(names == ["height", "noise_size", "subdivisions", "effect", "color", "smooth_normals"], "parms %s" % names)
    mean_x = positions(mesh)[:, 0].mean()
    check(abs(mean_x - 3.0) < 0.05, "world position kept (mean x %.2f)" % mean_x)
    check(np.ptp(positions(mesh)[:, 2]) > 0.01, "relief along Blender Z (%.3f)" % np.ptp(positions(mesh)[:, 2]))
    check(face_normals(mesh)[:, 2].mean() > 0.5, "faces point to +Z (winding is right)")
    check("uv" in mesh.uv_layers, "UVs back from Houdini")
    check("Cd" in mesh.color_attributes, "Cd color attribute")
    check([m.name for m in mesh.materials] == ["rock_dark", "rock_light"],
          "materials %s" % [m.name for m in mesh.materials])
    print("  ", settings.timings)

    print("parameters are inputs of the modifier")
    items = tree.interface.items_tree
    sockets = [(i.name, i.socket_type) for i in items if i.item_type == "SOCKET" and i.in_out == "INPUT"]
    check([name for name, _ in sockets]
          == ["Geometry", "Height", "Noise Size", "Subdivisions", "Effect", "Color", "Smooth Normals"],
          "group inputs %s" % [name for name, _ in sockets])
    types = dict(sockets)
    check(types["Height"] == "NodeSocketFloat" and types["Subdivisions"] == "NodeSocketInt"
          and types["Effect"] == "NodeSocketMenu" and types["Color"] == "NodeSocketColor"
          and types["Smooth Normals"] == "NodeSocketBool", "socket types follow the HDA")
    panels = [i.name for i in items if i.item_type == "PANEL"]
    check(panels == ["Look"], "folder Look becomes a panel %s" % panels)
    unused = [p.name for p in settings.parms if p.socket and not modifier.is_input_used(p.socket)]
    check(not unused, "every parameter is active (not greyed out) in the modifier %s" % unused)
    check(evaluated(rock)[0] == len(mesh.polygons), "the usage chain leaves the geometry unchanged")
    effect = settings.parms["effect"]
    check(get_parm(modifier, effect) == "mountain", "menu input reads as the Houdini token")

    print("parameters changed in the modifier")
    set_parm(modifier, settings, "subdivisions", [2])
    set_parm(modifier, settings, "effect", "inflate")
    set_parm(modifier, settings, "smooth_normals", False)
    support.poll_inputs()
    check(support.is_requested(tree), "a modifier input change requests a cook")
    cook.cook_now(tree)
    check(len(mesh.polygons) == 16 * input_faces, "2 subdivisions: %d faces" % len(mesh.polygons))
    support.poll_inputs()
    check(not support.is_requested(tree), "no further cook once the values are sent")

    print("keyframed parameter")
    item = support.modifier_input(modifier, settings.parms["height"].socket)
    item.value = 0.0
    item.keyframe_insert("value", frame=1)
    item.value = 1.5
    item.keyframe_insert("value", frame=10)
    bpy.context.scene.frame_set(1)
    support.poll_inputs()
    cook.cook_now(tree)
    flat = positions(mesh)[:, 2].mean()
    bpy.context.scene.frame_set(10)
    support.poll_inputs()
    check(support.is_requested(tree), "a frame change on an animated input requests a cook")
    cook.cook_now(tree)
    tall = positions(mesh)[:, 2].mean()
    check(tall > flat + 0.5, "the animation reaches Houdini (mean height %.2f → %.2f)" % (flat, tall))
    bpy.context.scene.frame_set(1)
    modifier.id_data.animation_data_clear()
    set_parm(modifier, settings, "height", [0.3])

    print("input vertex moved (positions only)")
    cook.cook_now(tree)
    grid.data.vertices[0].co.z += 1.0
    grid.data.update()
    bpy.context.view_layer.update()
    check(support.changed_inputs(tree) == {0}, "the depsgraph marks input 0 as changed")
    header, _ = support.peek_request(tree)
    check(header["inputs"] == ["positions"], "only the positions are sent (%s)" % header["inputs"])
    check(support.changed_inputs(tree) == {0} and support.peek_request(tree)[0]["inputs"] == ["positions"],
          "a peek records nothing: input 0 is still marked, and a second peek sees the same")
    cook.cook_now(tree)
    highest = positions(mesh)[:, 2].max()
    check(highest > 0.5, "the raised vertex reaches the output (highest z %.2f)" % highest)

    print("rings: generator without input")
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    rings = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::rings"])
    rings_modifier, rings_tree = hda(rings)
    cook.cook_now(rings_tree)
    check(len(rings_tree.houdini_bridge.inputs) == 0, "no input")
    faces_12 = realized(rings)[0]
    check(faces_12 > 0, "%d faces" % faces_12)
    set_parm(rings_modifier, rings_tree.houdini_bridge, "count", [24])
    cook.cook_now(rings_tree)
    check(realized(rings)[0] == 2 * faces_12, "24 rings: twice the faces")

    print("timing: input of 250,000 quads")
    big = bpy.data.objects.new("big", bpy.data.meshes.new("big"))
    bpy.context.collection.objects.link(big)
    bpy.context.view_layer.objects.active = big
    big.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=500, y_subdivisions=500, size=4)
    bpy.ops.object.mode_set(mode="OBJECT")
    print("   input faces:", len(big.data.polygons))
    settings.inputs[0].object = big
    set_parm(modifier, settings, "subdivisions", [0])
    for _ in range(2):
        start = time.perf_counter()
        cook.cook_now(tree, force=True)
        print("   full       %.0f ms | %s" % ((time.perf_counter() - start) * 1000, settings.timings))
    coordinates = np.empty(len(big.data.vertices) * 3, np.float32)
    big.data.vertices.foreach_get("co", coordinates)
    for _ in range(2):
        coordinates[2::3] += 0.01
        big.data.vertices.foreach_set("co", coordinates)
        big.data.update()
        support.mark_input_changed(tree, 0)
        start = time.perf_counter()
        cook.cook_now(tree)
        print("   positions  %.0f ms | %s" % ((time.perf_counter() - start) * 1000, settings.timings))

    print("shelf (temporary user folder)")
    preferences = support.preferences()
    preferences.folders.clear()
    preferences.folders.add().path = os.path.dirname(HDA)
    preferences.include_packages = True
    start = time.perf_counter()
    catalog.refresh_now()
    all_tools = catalog.state.tools
    print("   %d tools in %.1f s (%s)"
          % (len(all_tools), time.perf_counter() - start, support.catalog_state()["error"] or "no error"))
    check(len(all_tools) > 2, "the catalog holds the folder and the packages")
    check(os.path.exists(os.path.join(temporary, "catalog.json")), "catalog cached")
    assets = support.assets()
    library = os.path.join(support.library_dir(), assets.LIBRARY_FILE)
    check(os.path.exists(library), "the refresh writes the asset library")
    with open(os.path.join(support.library_dir(), "blender_assets.cats.txt"), encoding="utf-8") as file:
        catalogs = [line.split(":")[1] for line in file if line.count(":") == 2]
    check("hda" in catalogs and any(c.startswith("Labs/") for c in catalogs),
          "one catalog per group (%d)" % len(catalogs))
    check(max(c.count("/") for c in catalogs) <= 1, "2 category levels by default (%d catalogs)" % len(catalogs))
    preferences.catalog_depth = 1
    with open(os.path.join(support.library_dir(), "blender_assets.cats.txt"), encoding="utf-8") as file:
        top = [line.split(":")[1] for line in file if line.count(":") == 2]
    check("/" not in "".join(top) and "Labs" in top and "Houdini" in top, "1 level: only the main categories %s" % top)
    preferences.catalog_depth = 2
    with bpy.data.libraries.load(library, assets_only=True) as (data_from, data_to):
        check(len(data_from.objects) == len(all_tools), "one asset per tool (%d)" % len(data_from.objects))
        data_to.objects = [name for name in data_from.objects if name == "Bridge Rings"]
    loaded = data_to.objects[0]
    check(loaded.asset_data["houdini_bridge_type"] == "bridge::rings"
          and loaded.asset_data["houdini_bridge_max_inputs"] == 0,
          "the asset carries the tool reference")
    check(tuple(loaded.preview.image_size) == (128, 128), "the asset preview is the HDA icon")
    registered = [lib.name for lib in bpy.context.preferences.filepaths.asset_libraries
                  if os.path.normcase(os.path.normpath(lib.path))
                  == os.path.normcase(os.path.normpath(support.library_dir()))]
    check(registered == [assets.LIBRARY_NAME], "the library is registered in Blender %s" % registered)
    bpy.data.objects.remove(loaded)

    print("favorites follow the version kept")
    rock_tool = catalog.get_tool(next(catalog.tool_key(t) for t in all_tools if t["type"] == "bridge::rock"))
    old_key = "%s|bridge::rock::0.5" % rock_tool["file"]
    support.replace_favorites({old_key})
    support.follow_favorites()
    check(support.favorites() == {catalog.tool_key(rock_tool)}, "a favorite on an old version moves to the kept one")
    support.replace_favorites(set())

    print("asset shelf click (the properties Blender passes to the activate operator)")
    bpy.ops.mesh.primitive_cube_add(location=(0, -6, 0))
    clicked = bpy.context.active_object
    support.preferences().default_target = "MODIFIER"
    result = bpy.ops.houdini_bridge.asset_apply(
        asset_library_type="CUSTOM",
        asset_library_identifier=assets.LIBRARY_NAME,
        relative_asset_identifier="%s/Object/Bridge Rock" % assets.LIBRARY_FILE,
    )
    check(result == {"FINISHED"} and any(m.type == "NODES" for m in clicked.modifiers),
          "clicking the Rock asset adds a Houdini modifier to the active object")
    index = support.asset_index()
    duplicates = [name for name in index if name.endswith(".001")]
    check(all(catalog.get_tool(index[name]) for name in duplicates),
          "assets with duplicate labels still map to their tool (%d)" % len(duplicates))

    rings_key = next(catalog.tool_key(t) for t in all_tools if t["type"] == "bridge::rings")
    rock_key = next(catalog.tool_key(t) for t in all_tools if t["type"] == "bridge::rock")
    catalog.icon(catalog.get_tool(rings_key))
    check(support.has_tool_icon(catalog.get_tool(rings_key)["icon"]), "icon loaded as a Blender preview")
    bpy.ops.houdini_bridge.toggle_favorite(key=rock_key)
    window_manager = bpy.context.window_manager
    window_manager.houdini_bridge_search = ""
    favorites = [t["type"] for t in catalog.visible_tools(window_manager)]
    check(favorites == ["bridge::rock"], "without a search, the sidebar lists the favorites %s" % favorites)
    window_manager.houdini_bridge_search = "rings bridge"
    found = [t["type"] for t in catalog.visible_tools(window_manager)]
    check(found == ["bridge::rings"], "a search looks through every tool, favorite or not %s" % found)
    window_manager.houdini_bridge_search = ""
    support.replace_favorites(set())
    catalog.load()
    check(catalog.is_favorite(rock_key) and len(catalog.state.tools) == len(all_tools),
          "favorites and catalog read back from disk")

    bpy.context.scene.cursor.location = (5.0, 0.0, 0.0)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    bpy.ops.houdini_bridge.tool(key=rings_key)
    generator = bpy.context.active_object
    cook.cook_now(hda(generator)[1])
    check(tuple(generator.location) == (5.0, 0.0, 0.0), "generator placed at the 3D cursor")
    check(realized(generator)[0] > 0, "generator created from the shelf: %d faces" % realized(generator)[0])
    labs = [t for t in all_tools if t["type"].startswith("labs::") and t["max_inputs"] == 0]
    print("   Labs generators available:", len(labs))

    print("apply (new object)")
    bpy.ops.houdini_bridge.apply(tree=tree.name)
    check(len(rock.modifiers) == 0 and len(rock.data.polygons) > 0 and not tree.houdini_bridge.enabled,
          "the new object applied: the result is its mesh")

    test_modifier_mode(cook, modifiers, definitions)
    test_groups(cook, modifiers, definitions)
    test_group_chain(cook, modifiers, definitions)
    test_group_preview(cook, modifiers)
    test_group_panel(cook, modifiers, definitions)
    test_time(cook, modifiers, definitions)
    test_attributes(cook, modifiers, definitions)
    test_stack_order(cook, modifiers, definitions)
    test_group_rules(cook, modifiers, definitions)
    test_cache(cook, modifiers, definitions)
    test_streams(cook, modifiers, definitions)
    test_ramps_multiparms(cook, modifiers, definitions)
    test_hide_when(cook, modifiers)
    test_native(cook, modifiers, definitions)
    test_lock_inputs(cook, modifiers)
    test_missing_hda(cook, modifiers, definitions)
    test_apply_refused(cook, modifiers, definitions)
    test_driven_parms(cook, modifiers)
    test_code_editor(cook, modifiers)
    test_group_selection(cook, modifiers)
    test_group_selection_lock(cook, modifiers)
    test_group_selection_chain(cook, modifiers, definitions)
    test_bypass(cook, modifiers, definitions)
    test_template(cook, modifiers, definitions)
    test_display(cook, modifiers, definitions)
    test_display_and_selection(cook, modifiers, definitions)
    test_preferences(cook, modifiers, definitions)
    test_api(definitions)
    test_pointer_caches(cook)
    test_compat(cook)  # opens a file: the scene is replaced
    test_lock_on_load(cook, modifiers, definitions)  # opens a file too
    test_own_materials(cook, modifiers, definitions)  # opens a file too
    test_auto_run(cook, modifiers, definitions)  # opens a file too
    test_display_files(definitions)  # opens a file, and leaves a Display flag set for test_reenable
    test_reenable(definitions)  # the extension is disabled and enabled again
    test_auto_run_reload(definitions)  # last: opens a file, and imports the extension's modules anew
    finish()


try:
    main()
except Exception:
    import traceback

    traceback.print_exc()
    sys.stdout.flush()
    os._exit(2)
