"""Build demos/visualization_demo.blend.

First row: one object per kind of visualization. Second row: one per native
stream (instances, curves, points, volume, fragments).

    blender --background --factory-startup --python demos/make_visualization_demo.py

Needs the installed extension and Houdini (it cooks every demo once). Open the
file afterwards: the Houdini modifiers recook on load.
"""

import importlib
import math
import os
import sys
import traceback

import addon_utils
import bpy
import numpy as np

MODULE = "bl_ext.user_default.rg_houdini_bridge"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_HDA = os.path.join(ROOT, "tests", "hda", "bridge_test.hda")
OUTPUT = os.path.join(ROOT, "demos", "visualization_demo.blend")

# Where each module of the extension used here lives in its package, beyond its scripting interface
# (api.py, whose names do not move): a module that moves is fixed here.
EXTENSION_MODULES = {"streams": "blender.core.streams"}


def extension(name):
    """A module of the installed extension."""
    return importlib.import_module(MODULE + "." + EXTENSION_MODULES[name])


addon_utils.enable(MODULE, default_set=True)
api = importlib.import_module(MODULE + ".api")


def native(node_type, label):
    return {"type": node_type, "label": label, "max_inputs": 1}


def label(text, x, y, size=0.32):
    bpy.ops.object.text_add(location=(x, y - 2.2, 0))
    title = bpy.context.active_object
    title.data.body = text
    title.data.size = size
    title.data.align_x = "CENTER"
    title.name = "Label · " + text.split("\n")[0]


def select_only(obj):
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def houdini(obj, path, definition, parms=None, visualize=None):
    """Add a Houdini modifier, cook it, set parameters, cook again, pick a visualizer."""
    select_only(obj)
    modifier = api.place_hda(bpy.context, path, definition, "MODIFIER")
    tree = modifier.node_group
    api.cook_now(tree, force=True)
    for name, value in (parms or {}).items():
        api.set_parameter(modifier, name, value)
    if parms:
        api.cook_now(tree)
    if visualize:
        tree.houdini_bridge.visualize = visualize
    status = tree.houdini_bridge.errors or tree.houdini_bridge.status
    print("  %-26s %s" % (definition["label"], status), flush=True)
    return modifier


def face_group(obj, name, keep):
    """A Groups panel face group from a test on face centers."""
    mesh = obj.data
    centers = np.empty(len(mesh.polygons) * 3, np.float32)
    mesh.polygons.foreach_get("center", centers)
    attribute = mesh.attributes.new(name, "BOOLEAN", "FACE")
    attribute.data.foreach_set("value", keep(centers.reshape(-1, 3)))
    group = mesh.houdini_bridge_groups.add()
    group.attribute = name
    group.domain = "FACE"
    group.name = name
    group.color = (0.15, 0.75, 1.0)


def material(name, build):
    """A material the HDAs name in shop_materialpath (/mat/<name>): Blender finds it by name."""
    datablock = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if datablock.node_tree is None:
        datablock.use_nodes = True  # Blender before 5.0
    nodes, links = datablock.node_tree.nodes, datablock.node_tree.links
    shader = nodes["Principled BSDF"]
    build(nodes, links, shader)
    return datablock


def attribute(nodes, name, kind):
    node = nodes.new("ShaderNodeAttribute")
    node.attribute_name = name
    node.attribute_type = kind
    node.location = (-300, 200)
    return node


def stream_materials():
    def instance_color(nodes, links, shader):
        # Instances: the color of each copy, read from the instancer.
        links.new(attribute(nodes, "Cd", "INSTANCER").outputs["Color"], shader.inputs["Base Color"])
        shader.inputs["Roughness"].default_value = 0.6

    def grass(nodes, links, shader):
        links.new(attribute(nodes, "Cd", "GEOMETRY").outputs["Color"], shader.inputs["Base Color"])
        shader.inputs["Roughness"].default_value = 0.8

    def fireflies(nodes, links, shader):
        color = attribute(nodes, "Cd", "GEOMETRY")
        links.new(color.outputs["Color"], shader.inputs["Base Color"])
        links.new(color.outputs["Color"], shader.inputs["Emission Color"])
        shader.inputs["Emission Strength"].default_value = 6.0

    def plain(color, roughness):
        def build(nodes, links, shader):
            shader.inputs["Base Color"].default_value = color
            shader.inputs["Roughness"].default_value = roughness
        return build

    material("bridge_instance", instance_color)
    material("bridge_grass", grass)
    material("bridge_fireflies", fireflies)
    material("bridge_outside", plain((0.55, 0.55, 0.6, 1.0), 0.5))
    material("bridge_inside", plain((0.9, 0.35, 0.08, 1.0), 0.9))
    material("demo_ground", plain((0.12, 0.1, 0.08, 1.0), 0.9))


def ground_under(x, y, size=3.2):
    """A plain ground below an HDA whose result replaces its input (scatter, grass…)."""
    bpy.ops.mesh.primitive_plane_add(size=size, location=(x, y, -0.002))
    plane = bpy.context.active_object
    plane.name = "Ground · %d" % x
    plane.data.materials.append(bpy.data.materials["demo_ground"])
    return plane


def test_hda(node_type, label_text):
    return {"type": node_type, "label": label_text, "max_inputs": 1}


def native_streams(scene):
    """Second row: what an HDA returns besides a mesh, in Blender's own types."""
    streams = extension("streams")
    stream_materials()
    y = -7.0

    # 8. Instances: rocks, boxes and quadric spheres copied onto a grid, one color each.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=12, y_subdivisions=12, size=3, location=(0, y, 0))
    field = bpy.context.active_object
    field.name = "8 Scatter (instances)"
    ground_under(0, y)
    houdini(field, TEST_HDA, test_hda("bridge::scatter", "Bridge Scatter"), {"count": [1500], "size": [0.09]})
    label("8. Instances\n1500 copies, 3 shapes sent once", 0, y, 0.22)

    # 9. Open curves: grass blades with their color and width, animated by the wind.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=12, y_subdivisions=12, size=3, location=(4, y, 0))
    lawn = bpy.context.active_object
    lawn.name = "9 Grass (curves)"
    ground_under(4, y)
    houdini(lawn, TEST_HDA, test_hda("bridge::grass", "Bridge Grass"),
            {"count": [6000], "height": [0.35], "animate": True, "wind": [0.25]})
    label("9. Open curves\nCd, width, wind follows $T", 4, y, 0.22)

    # 10. Loose points: fireflies rising from a disc, live with the timeline.
    bpy.ops.mesh.primitive_circle_add(vertices=48, radius=1.4, fill_type="NGON", location=(8, y, 0))
    disc = bpy.context.active_object
    disc.name = "10 Fireflies (points)"
    ground_under(8, y)
    houdini(disc, TEST_HDA, test_hda("bridge::fireflies", "Bridge Fireflies"), {"count": [4000], "height": [1.6]})
    label("10. Loose points\npscale = radius, animated", 8, y, 0.22)

    # 11. Volume: a fog VDB filling a sphere (a closed mesh), read by a Volume object.
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0, location=(12, y, 1.1))
    cloud = bpy.context.active_object
    cloud.name = "11 Fog (volume)"
    cloud.scale = (1.3, 1.0, 0.8)
    ground_under(12, y)
    fog = houdini(cloud, TEST_HDA, test_hda("bridge::fog", "Bridge Fog"),
                  {"voxel_size": [0.03], "density": [8.0], "noise_frequency": [1.2]})
    label("11. Volume (VDB)\nfog with noise", 12, y, 0.22)
    # It recooks when the file opens. A saved volume keeps the absolute path of its temporary .vdb, and
    # an empty path leaves the old text in the datablock's buffer: only a new datablock is really clean.
    settings = fog.node_group.houdini_bridge
    streams.clear(settings)
    old = settings.volume_holder.data
    settings.volume_holder.data = bpy.data.volumes.new(old.name)
    bpy.data.volumes.remove(old)

    # 12. Fragments: a sphere cut into pieces, one shape per piece, inside material kept.
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.9, location=(16, y, 1.0))
    ball = bpy.context.active_object
    ball.name = "12 Shatter (fragments)"
    ground_under(16, y)
    houdini(ball, TEST_HDA, test_hda("bridge::shatter", "Bridge Shatter"), {"pieces": [40], "explode": [0.25]})
    label("12. Fragments\none shape per piece", 16, y, 0.22)

    # 13. Many instances: drawn as points in the viewport, instances at render time.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=20, y_subdivisions=20, size=3, location=(20, y, 0))
    crowd = bpy.context.active_object
    crowd.name = "13 Many instances (viewport points)"
    ground_under(20, y)
    heavy = houdini(crowd, TEST_HDA, test_hda("bridge::scatter", "Bridge Scatter"),
                    {"count": [100000], "size": [0.012], "spheres": False, "animate": True, "spin": [3.0]})
    heavy.node_group.houdini_bridge.instance_display = "POINTS"
    label("13. 100,000 instances\npoints in viewport, instances in render", 20, y, 0.22)
    # They recook when the file opens: saving them would only make the file heavy.
    streams.clear(heavy.node_group.houdini_bridge)

    camera = bpy.data.objects.new("Camera · streams", bpy.data.cameras.new("Camera · streams"))
    scene.collection.objects.link(camera)
    camera.location = (10, y - 21.0, 11.0)
    camera.rotation_euler = (math.radians(63), 0, 0)
    camera.data.lens = 30
    scene.camera = camera
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(30))
    scene.collection.objects.link(sun)
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.05, 0.06, 0.08, 1.0)


def forget_folders():
    """Blender points the file browser of the Shading workspace at the user's Documents folder.
    An empty path leaves the old text in the buffer, so it is overwritten first."""
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "FILE_BROWSER" and space.params:
                    space.params.directory = b"." * 1000
                    space.params.directory = b""


def main():
    # Without use_factory_startup this loads the user's startup.blend, whatever the command line says,
    # and the demo inherits its color settings and file browser folder.
    bpy.ops.wm.read_homefile(use_factory_startup=True, use_empty=True)
    api.start_server()
    print("building the demos", flush=True)

    # 1. A face group through a native Remesh: the group follows the new topology.
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, location=(0, 0, 0))
    sphere = bpy.context.active_object
    sphere.name = "1 Group through Remesh"
    face_group(sphere, "cap", lambda c: c[:, 2] > 0.45)
    remesh = houdini(sphere, "", native("remesh::2.0", "Remesh"))  # shown by the Groups panel preview
    sphere.modifiers.active = remesh
    label("1. Group through Remesh\nGroups panel: cap", 0, 0)

    # 2. A scalar attribute: curvature measured by Houdini, as a color ramp.
    bpy.ops.mesh.primitive_monkey_add(location=(4, 0, 0))
    monkey = bpy.context.active_object
    monkey.name = "2 Curvature"
    monkey.modifiers.new("Subdivision", "SUBSURF").levels = 2
    houdini(monkey, "", native("measure::2.0", "Measure"), {"measure": "curvature", "attribname": "curvature"},
            visualize="curvature")
    label("2. Curvature (Measure)\nVisualize: color ramp", 4, 0)

    # 3. A vector attribute: tangents from PolyFrame, as arrows.
    bpy.ops.mesh.primitive_torus_add(location=(8, 0, 0), major_segments=32, minor_segments=12)
    torus = bpy.context.active_object
    torus.name = "3 Tangents"
    houdini(torus, "", native("polyframe", "PolyFrame"), visualize="tangentu")
    label("3. Tangents (PolyFrame)\nVisualize: arrows", 8, 0)

    # 4. Groups made by an HDA: a point group and an edge group.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=10, size=3, location=(12, 0, 0))
    grid = bpy.context.active_object
    grid.name = "4 HDA groups"
    face_group(grid, "back", lambda c: c[:, 1] > 0.6)
    houdini(grid, TEST_HDA, {"type": "bridge::groups", "label": "Bridge Groups", "max_inputs": 1},
            {"group": "back", "distance": [0.6]})
    label("4. Groups made by an HDA\nVisualize: right_edges", 12, 0)

    # 5. The stack: the first HDA makes 'right', the second one works on it.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=10, size=3, location=(16, 0, 0))
    stack = bpy.context.active_object
    stack.name = "5 Group through the stack"
    houdini(stack, TEST_HDA, {"type": "bridge::groups", "label": "Bridge Groups", "max_inputs": 1}, {"distance": [0.0]})
    houdini(stack, TEST_HDA, {"type": "bridge::groups", "label": "Bridge Groups 2", "max_inputs": 1},
            {"group": "right", "distance": [0.8]})
    label("5. Group through the stack\n1st HDA makes 'right', 2nd uses it", 16, 0)

    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 48
    scene.frame_set(1)

    # 6. Time: an HDA reading $T, live; Measure's curvature, stacked, follows it.
    for other in bpy.context.selected_objects:
        other.select_set(False)
    scene.cursor.location = (20, 0, 0)
    terrain = api.place_hda(bpy.context, TEST_HDA,
                            {"type": "bridge::animated", "label": "Bridge Animated", "max_inputs": 0}, "NEW")
    terrain.name = "6 Time (live)"
    terrain.scale = (0.3, 0.3, 0.3)
    api.cook_now(next(m.node_group for m in terrain.modifiers if m.type == "NODES"), force=True)
    houdini(terrain, "", native("measure::2.0", "Measure"), {"measure": "curvature", "attribname": "curvature"},
            visualize="curvature")
    label("6. Time, live (play the timeline)\n$T in the HDA, curvature follows", 20, 0)

    # 7. Cache: a rock following a keyframed sphere, cooked once for frames 1–48.
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.8, location=(24, -1, 0))
    ball = bpy.context.active_object
    ball.name = "7 Animated input"
    ball.hide_render = True
    ball.keyframe_insert("location", frame=1)
    ball.location.y += 2.0
    ball.location.z += 1.0
    ball.keyframe_insert("location", frame=48)
    select_only(ball)
    rock = api.place_hda(bpy.context, TEST_HDA, {"type": "bridge::rock", "label": "Bridge Rock", "max_inputs": 1},
                         "NEW")
    rock.name = "7 Cached rock"
    rock_tree = next(m.node_group for m in rock.modifiers if m.type == "NODES")
    api.cook_now(rock_tree, force=True)
    ball.hide_set(True)
    print("  %-26s %s" % ("Bridge Rock (cache)", api.cache_now(rock_tree, 1, 48)), flush=True)
    label("7. Cache, frames 1–48\nplays and renders without Houdini", 24, 0)
    scene.frame_set(1)

    native_streams(scene)

    # Relative paths (//../tests/hda/…), so the file works wherever the repository is.
    for tree in bpy.data.node_groups:
        settings = getattr(tree, "houdini_bridge", None)
        if settings is not None and settings.enabled and settings.path:
            settings.path = bpy.path.relpath(settings.path, start=os.path.dirname(OUTPUT)).replace("\\", "/")
    bpy.context.view_layer.update()
    api.stop_server()
    forget_folders()
    bpy.ops.wm.save_as_mainfile(filepath=OUTPUT)
    print("saved", OUTPUT, flush=True)


status = 0
try:
    main()
except Exception:
    traceback.print_exc()
    status = 1
sys.stdout.flush()
sys.stderr.flush()
os._exit(status)
