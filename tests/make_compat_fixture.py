"""Write tests/fixtures/compat_0_17.blend and compat_0_17.json, a file saved before 1.0.

It was run once, with a pre-release build installed, to keep a file in the oldest
format the extension reads. test_blender.py (test_compat) opens it with whatever
version is installed and checks it against the .json: the file only means something
if it stays exactly as that build wrote it, so the script refuses to overwrite it. A
later format would get a witness of its own, under a new name.

    $env:OCIO = $null   # PowerShell, or start from a shell without OCIO
    blender --background --factory-startup --python tests/make_compat_fixture.py

It starts from the factory scene, stores the HDA path relative to the file, and
holds no volume (a saved volume keeps the absolute path of its .vdb).
"""

import json
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
from support import HDA, ROOT, get_parm, hda, set_parm  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "compat_0_17.blend")
EXPECTED = os.path.join(ROOT, "tests", "fixtures", "compat_0_17.json")
FRAME = 1


def select_only(obj):
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def add_group(mesh, name, domain, members, color):
    attribute = mesh.attributes.new(name, "BOOLEAN", domain)
    attribute.data.foreach_set("value", members)
    group = mesh.houdini_bridge_groups.add()
    group.attribute = name
    group.domain = domain
    group.name = name
    group.color = color


def forget_folders():
    """Blender points the file browser of the Shading workspace at the user's Documents folder."""
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "FILE_BROWSER" and space.params:
                    space.params.directory = b"." * 1000
                    space.params.directory = b""


def build(definitions):
    cook, modifiers, cache = support.cook(), support.modifiers(), support.cache()
    scene = bpy.context.scene
    scene.frame_set(FRAME)

    # Two chained Houdini modifiers on an existing object, with groups in the Groups panel.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=6, y_subdivisions=6, size=2, location=(0, 0, 0))
    rock_object = bpy.context.active_object
    rock_object.name = "Rock and Groups"
    mesh = rock_object.data
    add_group(mesh, "top_row", "FACE", [index < 6 for index in range(len(mesh.polygons))], (0.9, 0.2, 0.1))
    add_group(mesh, "corner", "POINT", [index == 0 for index in range(len(mesh.vertices))], (0.1, 0.3, 0.9))
    mesh.houdini_bridge_groups[1].show = False
    rock = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    rock.name = "Rock"
    rock_tree = rock.node_group
    rock_settings = rock_tree.houdini_bridge
    cook.cook_now(rock_tree, force=True)
    set_parm(rock, rock_settings, "noise_size", [0.9])     # float
    set_parm(rock, rock_settings, "subdivisions", [2])     # int
    set_parm(rock, rock_settings, "effect", "inflate")     # menu
    set_parm(rock, rock_settings, "color", [0.2, 0.45, 0.8])  # color
    set_parm(rock, rock_settings, "smooth_normals", False)  # bool
    height = support.modifier_input(rock, rock_settings.parms["height"].socket)
    height.value = 0.2
    height.keyframe_insert("value", frame=1)
    height.value = 1.2
    height.keyframe_insert("value", frame=10)
    scene.frame_set(FRAME)
    rock_settings.output_mode = "MESH"
    cook.cook_now(rock_tree, force=True)

    groups_modifier = modifiers.place_hda(bpy.context, HDA, definitions["bridge::groups"], "MODIFIER")
    groups_modifier.name = "Groups"
    groups_tree = groups_modifier.node_group
    cook.cook_now(groups_tree, force=True)
    set_parm(groups_modifier, groups_tree.houdini_bridge, "group", "top_row")  # string
    set_parm(groups_modifier, groups_tree.houdini_bridge, "distance", [0.25])
    cook.cook_now(groups_tree)
    groups_tree.houdini_bridge.visualize = "height"

    # Ramps, a multiparm whose count changed, a vector.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=10, size=2, location=(4, 0, 0))
    ramps_object = bpy.context.active_object
    ramps_object.name = "Ramps"
    ramps = modifiers.place_hda(bpy.context, HDA, definitions["bridge::ramps"], "MODIFIER")
    ramps.name = "Ramps"
    ramps_tree = ramps.node_group
    ramps_settings = ramps_tree.houdini_bridge
    cook.cook_now(ramps_tree, force=True)
    parameters = support.parameters()
    shape = parameters.ramp_node(ramps_tree, ramps_settings.parms["shape"])
    shape.mapping.curves[0].points[1].location = (0.5, 0.25)
    shape.mapping.update()
    tint = parameters.ramp_node(ramps_tree, ramps_settings.parms["tint"])
    tint.color_ramp.elements[0].color = (0.0, 1.0, 0.0, 1.0)
    tint.color_ramp.interpolation = "CONSTANT"
    set_parm(ramps, ramps_settings, "bumps", [2])
    cook.cook_now(ramps_tree)
    set_parm(ramps, ramps_settings, "center1", [0.3, 0.0, 0.2])  # vector
    set_parm(ramps, ramps_settings, "radius1", [0.7])
    set_parm(ramps, ramps_settings, "height", [1.5])
    cook.cook_now(ramps_tree)

    # A native stream of instances.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4, y_subdivisions=4, size=3, location=(8, 0, 0))
    scatter_object = bpy.context.active_object
    scatter_object.name = "Scatter"
    scatter = modifiers.place_hda(bpy.context, HDA, definitions["bridge::scatter"], "MODIFIER")
    scatter.name = "Scatter"
    scatter_tree = scatter.node_group
    scatter_settings = scatter_tree.houdini_bridge
    cook.cook_now(scatter_tree, force=True)
    set_parm(scatter, scatter_settings, "count", [40])
    set_parm(scatter, scatter_settings, "color_a", [0.1, 0.9, 0.2])
    scatter_settings.instance_display = "POINTS"
    cook.cook_now(scatter_tree)

    # A cache of a few frames (a generator reading the time).
    for other in bpy.context.selected_objects:
        other.select_set(False)
    scene.cursor.location = (12, 0, 0)
    animated_object = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::animated"])
    animated_object.name = "Cached Waves"
    animated, animated_tree = hda(animated_object)
    animated.name = "Waves"
    cook.cook_now(animated_tree, force=True)
    set_parm(animated, animated_tree.houdini_bridge, "speed", [2.0])
    cook.cook_now(animated_tree)
    print("   cache:", cache.cache_now(animated_tree, 1, 4), flush=True)
    scene.frame_set(FRAME)

    # Relative paths (//../hda/…), so the file works wherever the repository is.
    for tree in bpy.data.node_groups:
        settings = getattr(tree, "houdini_bridge", None)
        if settings is not None and settings.enabled and settings.path:
            settings.path = bpy.path.relpath(settings.path, start=os.path.dirname(FIXTURE)).replace("\\", "/")
    bpy.context.view_layer.update()

    frames = {}
    for frame in (1, 10):
        scene.frame_set(frame)
        frames[str(frame)] = get_parm(rock, rock_settings.parms["height"])
    scene.frame_set(FRAME)
    errors = {tree.name: tree.houdini_bridge.errors for tree in (rock_tree, groups_tree, ramps_tree, scatter_tree,
                                                                  animated_tree) if tree.houdini_bridge.errors}
    if errors:
        raise RuntimeError("cook errors: %s" % errors)
    return {
        "version": support.installed_version(),
        "frame": FRAME,
        "objects": support.describe_houdini_scene(),
        "animated": {"object": rock_object.name, "modifier": rock.name, "parm": "height", "frames": frames},
        "cached": {"object": animated_object.name, "frames": [1, 4],
                   "points": len(animated_tree.houdini_bridge.holder.data.vertices)},
    }


def main():
    if os.path.exists(FIXTURE) or os.path.exists(EXPECTED):
        raise SystemExit("the fixture exists: it is a witness of a file saved before 1.0, never written again")
    # Without OCIO, Blender sets it to its own config; any other config would be stored in the file.
    ocio = os.path.normcase(os.environ.get("OCIO", ""))
    if ocio and not ocio.startswith(os.path.normcase(os.path.dirname(bpy.app.binary_path))):
        raise SystemExit("unset OCIO first: the file would store that config's color settings")
    # Without use_factory_startup this loads the user's startup.blend, whatever the command line says.
    bpy.ops.wm.read_homefile(use_factory_startup=True, use_empty=True)
    support.enable()
    support.redirect_user_dir()
    support.cook().ensure_server(blocking=True)
    expected = build(support.definitions())
    support.cook().stop_server()
    forget_folders()
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=FIXTURE, compress=True, relative_remap=True)
    with open(EXPECTED, "w", encoding="utf-8", newline="\n") as file:
        json.dump(expected, file, indent=1, sort_keys=True)
        file.write("\n")
    print("saved", FIXTURE, "and", EXPECTED, flush=True)


status = 0
try:
    main()
except SystemExit as stop:
    print(stop, flush=True)
    status = 1
except Exception:
    traceback.print_exc()
    status = 1
sys.stdout.flush()
sys.stderr.flush()
os._exit(status)
