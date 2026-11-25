"""The step "selection" of tests/test_ui.py: the viewport group selection in a real window, with
events simulated (blender --enable-event-simulate): clicks in the 3D view, Enter and Esc read by
the modal operator, real undo and redo after it, the ways it ends, Lock and a chained Houdini
modifier above, edges, and the extension disabled while it runs.

It runs alone, in a Blender started with --enable-event-simulate (tests/test_ui.py runs only
this step then): such a Blender reads no event of the system, so its windows are drawn only
when asked (redraw(), before each click, so that the 3D view's selection sees what is shown),
and the undo history starts with this step, so an undo that goes past it only meets the
bottom of the history. Each check says what must be true.
"""

import os
import tempfile

import bmesh
import bpy
import support
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Euler
from support import HDA, check, get_parm, set_parm

TEMPORARY = "Houdini Selection"  # the temporary object of a copy (features/selection/session.py)
MARKER = "houdini_bridge_selection"  # the record of a running selection, on the scene (restore.py)
_mouse = [0, 0]


def window():
    return bpy.context.window_manager.windows[0]


def area(kind):
    return next(a for a in window().screen.areas if a.type == kind)


def region(found, kind):
    return next(r for r in found.regions if r.type == kind)


def event(kind, value, **keys):
    window().event_simulate(type=kind, value=value, x=_mouse[0], y=_mouse[1], **keys)


def mouse_to(x, y):
    _mouse[:] = int(x), int(y)
    event("MOUSEMOVE", "NOTHING")


def redraw():
    """Draw every window now: with event simulation, nothing else draws them."""
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def click(x, y, **keys):
    redraw()
    mouse_to(x, y)
    yield 0.15
    redraw()  # what is under the mouse is drawn before the press: the selection reads it
    yield 0.1
    event("LEFTMOUSE", "PRESS", **keys)
    event("LEFTMOUSE", "RELEASE", **keys)
    yield 0.3


def key(kind, **keys):
    event(kind, "PRESS", **keys)
    event(kind, "RELEASE", **keys)


def view_override():
    view = area("VIEW_3D")
    return bpy.context.temp_override(window=window(), area=view, region=region(view, "WINDOW"))


def frame_on(obj, distance=7.5):
    r3d = area("VIEW_3D").spaces.active.region_3d
    r3d.view_perspective = "PERSP"
    r3d.view_location = obj.matrix_world.translation
    r3d.view_rotation = Euler((1.05, 0, 0.7)).to_quaternion()
    r3d.view_distance = distance


def face_point(obj, index):
    """Window coordinates of the centre of a face of `obj`."""
    view = area("VIEW_3D")
    win = region(view, "WINDOW")
    centre = obj.matrix_world @ obj.data.polygons[index].center
    point = location_3d_to_region_2d(win, view.spaces.active.region_3d, centre)
    return win.x + point.x, win.y + point.y


def facing_faces(obj, count, exclude=()):
    """`count` faces of `obj` that face the view, nearest the middle of the 3D view first: a
    click on each selects it, and pushes one Edit Mode step."""
    view = area("VIEW_3D")
    win = region(view, "WINDOW")
    r3d = view.spaces.active.region_3d
    eye = r3d.view_matrix.inverted().translation
    middle = (win.x + win.width / 2, win.y + win.height / 2)
    found = []
    for face in obj.data.polygons:
        centre = obj.matrix_world @ face.center
        normal = (obj.matrix_world.to_3x3() @ face.normal).normalized()
        if face.index not in exclude and normal.dot((eye - centre).normalized()) > 0.6:
            x, y = face_point(obj, face.index)
            found.append(((x - middle[0]) ** 2 + (y - middle[1]) ** 2, face.index))
    return [index for _, index in sorted(found)[:count]]


def invoke_arrow(tree, parm):
    with view_override():
        return bpy.ops.houdini_bridge.select_group("INVOKE_DEFAULT", tree=tree.name, parm=parm)


def undo(times, redo=False):
    """Undo (or redo) `times` steps in the 3D view; a step refused (the bottom of the history) stops."""
    done = 0
    for _ in range(times):
        with view_override():
            try:
                (bpy.ops.ed.redo if redo else bpy.ops.ed.undo)()
            except RuntimeError:
                break
        done += 1
        yield 0.4
    return done


def leave_edit_mode():
    with view_override():
        active = bpy.context.view_layer.objects.active
        if active is not None and active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")


def make_active(obj):
    for other in bpy.context.view_layer.objects:
        if other.select_get():
            other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def state(name):
    """What a selection must leave as it found it, for the object `name`."""
    obj = bpy.data.objects[name]
    active = bpy.context.view_layer.objects.active
    return {
        "active": active.name if active else None,
        "mode": obj.mode,
        "modifiers": [(m.name, m.show_viewport, m.show_render) for m in obj.modifiers],
        "hidden": obj.hide_get(),
        "objects": sorted(o.name for o in bpy.data.objects),
        "meshes": sorted(m.name for m in bpy.data.meshes),
        "marker": MARKER in bpy.context.scene,
    }


def compare(before, name, what):
    after = state(name)
    check(after == before, "%s: everything is back %s" % (what, {k: (before[k], after[k]) for k in before
                                                                if before[k] != after[k]}))


def clean(name):
    """What an undo or a redo may bring back must hold nothing of a selection: True, or what is
    left of it. Edit Mode may come back: an undo into a click of the user is a step of the user's."""
    obj = bpy.data.objects.get(name)
    left = {"copy": TEMPORARY in bpy.data.objects, "marker": MARKER in bpy.context.scene,
            "gone": obj is None, "hidden": obj is not None and obj.hide_get(),
            "cut": obj is not None and not all(m.show_viewport for m in obj.modifiers)}
    left = sorted(key for key, value in left.items() if value)
    return True if not left else left


def selected(obj, domain):
    edit = bmesh.from_edit_mesh(obj.data)
    if domain == "FACE":
        return [i for i, face in enumerate(edit.faces) if face.select]
    edit.verts.index_update()
    return sorted(tuple(sorted(v.index for v in edge.verts)) for edge in edit.edges if edge.select)


def selection(definitions):
    """The step: see the module."""
    print("the viewport group selection in a window: clicks, Enter, Esc, undo, the ways it ends")
    if not bpy.app.use_event_simulate:
        check(False, "the selection step needs Blender started with --enable-event-simulate")
        return
    modifiers, session = support.modifiers(), support.selection()
    group_string = support.installed("group_string")
    yield from wait_idle()
    leave_edit_mode()
    for other in list(bpy.context.view_layer.objects):
        other.hide_set(True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1.5, location=(0, 0, 0))
    sphere = bpy.context.active_object
    sphere.name = name = "Selection Sphere"
    modifier = modifiers.place_hda(bpy.context, "", {"type": "groupcreate", "label": "Group Create",
                                                     "max_inputs": 1}, "MODIFIER")
    tree = modifier.node_group
    yield from wait_cooked(tree)
    set_parm(modifier, tree.houdini_bridge, "basegroup", "0-3")
    yield from wait_cooked(tree, again=True)
    frame_on(sphere)
    bpy.ops.ed.undo_push(message="Selection step start")
    yield 0.5

    print("in place: clicks, then Enter")
    view = region(area("VIEW_3D"), "WINDOW")
    # The first simulated click of a session is lost (it only activates the window): spent here.
    yield from click(view.x + 40, view.y + 40)
    make_active(sphere)
    before = state(name)
    check(invoke_arrow(tree, "basegroup") == {"RUNNING_MODAL"}, "the arrow enters the mode")
    yield 0.4
    check(sphere.mode == "EDIT" and not modifier.show_viewport and session.active() is not None,
          "Edit Mode on the sphere, the modifier cut from the viewport")
    faces = facing_faces(sphere, 6, exclude=(0, 1, 2, 3))  # 0-3: the field holds them already
    mouse_to(*face_point(sphere, faces[-1]))  # the view drawn in Edit Mode once before the first click
    redraw()
    yield 0.5
    for index in faces:  # Shift: each click adds its face to the preselected ones
        yield from click(*face_point(sphere, index), shift=True)
    picked = group_string.write_numbers(selected(sphere, "FACE"))
    key("RET")
    yield 0.6
    value = get_parm(modifier, tree.houdini_bridge.parms["basegroup"])
    check(session.active() is None and value == picked == group_string.write_numbers(sorted({0, 1, 2, 3, *faces})),
          "Enter, read by the modal operator, writes the faces clicked: %r (%r)" % (value, sorted(faces)))
    compare(before, name, "after Enter")

    print("undo and redo after Enter (each click pushed an Edit Mode step)")
    marks = []
    for _ in range(len(faces)):
        yield from undo(1)
        marks.append(clean(name))
    check(all(mark is True for mark in marks), "no undo brings back any of the selection %s" % marks)
    leave_edit_mode()
    marks = []
    for _ in range(len(faces) + 1):
        if not (yield from undo(1, redo=True)):
            break
        marks.append(clean(name))
    check(all(mark is True for mark in marks), "nor each redo %s" % marks)
    leave_edit_mode()
    sphere = bpy.data.objects[name]
    modifier, tree = sphere.modifiers[-1], sphere.modifiers[-1].node_group
    yield 0.3

    print("a modifier above: a copy is edited")
    make_active(sphere)
    bevel = sphere.modifiers.new("Bevel", "BEVEL")
    bevel.width = 0.08
    with bpy.context.temp_override(object=sphere):
        bpy.ops.object.modifier_move_to_index(modifier="Bevel", index=0)
    yield from wait_cooked(tree, again=True)
    before = state(name)
    check(invoke_arrow(tree, "basegroup") == {"RUNNING_MODAL"}, "the arrow enters the mode on a copy")
    yield 0.4
    copy = bpy.context.view_layer.objects.active
    check(copy is not None and copy.name.startswith(TEMPORARY) and copy.mode == "EDIT" and sphere.hide_get()
          and copy.hide_render, "a copy in Edit Mode, kept out of renders, the sphere hidden")
    for index, number in enumerate(facing_faces(copy, 3)):
        yield from click(*face_point(copy, number), shift=index > 0)
    picked = group_string.write_numbers(selected(copy, "FACE"))
    key("RET")
    yield 0.6
    value = get_parm(modifier, tree.houdini_bridge.parms["basegroup"])
    check(value == picked and value, "Enter writes the faces of the copy: %r (%r)" % (value, picked))
    compare(before, name, "after Enter on a copy")
    marks = []
    for _ in range(5):
        if not (yield from undo(1)):
            break
        marks.append(clean(name))
    check(marks and all(mark is True for mark in marks),
          "undo after a copy: no copy, no hidden object, no marker, no crash %s" % marks)
    leave_edit_mode()
    yield from undo(6, redo=True)
    check(clean(name) is True, "nor after the redos %s" % clean(name))
    leave_edit_mode()
    sphere = bpy.data.objects[name]
    for other in [m for m in sphere.modifiers if m.type == "BEVEL"]:
        sphere.modifiers.remove(other)
    modifier, tree = sphere.modifiers[-1], sphere.modifiers[-1].node_group
    make_active(sphere)
    yield from wait_cooked(tree, again=True)

    print("the ways it ends: Esc, Tab, a save, undos past its start")
    saved = os.path.join(tempfile.mkdtemp(), "selection.blend")
    endings = (("Esc", lambda: key("ESC")),
               ("Tab", lambda: key("TAB")),
               ("a save", lambda: bpy.ops.wm.save_as_mainfile(filepath=saved, copy=True)),
               ("two undos past its start", lambda: (key("Z", ctrl=True), key("Z", ctrl=True))))
    for what, action in endings:
        sphere = bpy.data.objects[name]
        modifier, tree = sphere.modifiers[-1], sphere.modifiers[-1].node_group
        before = state(name)
        value_before = get_parm(modifier, tree.houdini_bridge.parms["basegroup"])
        invoke_arrow(tree, "basegroup")
        yield 0.4
        yield from click(*face_point(bpy.context.view_layer.objects.active, 3))
        with view_override():
            action()
        yield 0.8
        check(session.active() is None, "%s ends the mode" % what)
        compare(before, name, what)
        sphere = bpy.data.objects[name]
        modifier, tree = sphere.modifiers[-1], sphere.modifiers[-1].node_group
        check(get_parm(modifier, tree.houdini_bridge.parms["basegroup"]) == value_before,
              "%s writes nothing" % what)
        leave_edit_mode()
        yield 0.3
    if os.path.exists(saved):
        os.remove(saved)

    print("one undo inside the mode undoes a click, and the mode goes on")
    sphere = bpy.data.objects[name]
    modifier, tree = sphere.modifiers[-1], sphere.modifiers[-1].node_group
    before = state(name)
    invoke_arrow(tree, "basegroup")
    yield 0.4
    active = bpy.context.view_layer.objects.active
    yield from click(*face_point(active, 3))
    yield from click(*face_point(active, 5), shift=True)
    key("Z", ctrl=True)
    yield 0.6
    mouse_to(_mouse[0] + 4, _mouse[1])
    yield 0.3
    active = bpy.context.view_layer.objects.active
    check(session.active() is not None and active.mode == "EDIT" and len(selected(active, "FACE")) == 1,
          "still selecting, one face selected (%s)" % selected(active, "FACE"))
    key("ESC")
    yield 0.6
    compare(before, name, "Esc after an undo inside")

    yield from edges(name, group_string)
    yield from lock_and_chain(definitions, name)
    yield from disabled_while_running(name)


def wait_idle(seconds=90):
    """Yield until the engine has nothing to cook (the file opened before cooks its modifiers)."""
    waited = 0.0
    while support.is_busy() and waited < seconds:
        yield 0.2
        waited += 0.2


def wait_cooked(tree, again=False, seconds=60):
    """Yield until the engine is idle and the modifier has a status (`again`: a cook was asked)."""
    waited = 0.0
    if again:
        yield 0.3
    while (support.is_busy() or not tree.houdini_bridge.status) and waited < seconds:
        yield 0.2
        waited += 0.2
    check(waited < seconds, "%s cooked" % tree.houdini_bridge.label)


def edges(name, group_string):
    print("edges: a field taking edges, selected in edge mode, confirmed by Enter")
    sphere = bpy.data.objects[name]
    modifier, tree = sphere.modifiers[-1], sphere.modifiers[-1].node_group
    set_parm(modifier, tree.houdini_bridge, "grouptype", "edge")
    set_parm(modifier, tree.houdini_bridge, "basegroup", "")
    yield from wait_cooked(tree, again=True)
    invoke_arrow(tree, "basegroup")
    yield 0.4
    check(tuple(bpy.context.scene.tool_settings.mesh_select_mode) == (False, True, False),
          "grouptype edge: edge mode")
    edit = bmesh.from_edit_mesh(sphere.data)
    edit.edges.ensure_lookup_table()
    for edge in edit.edges[:3]:
        edge.select_set(True)
    bmesh.update_edit_mesh(sphere.data, loop_triangles=False, destructive=False)
    wanted = group_string.write_edges(selected(sphere, "EDGE"))
    mouse_to(*face_point(sphere, 0))
    key("RET")
    yield 0.6
    value = get_parm(modifier, tree.houdini_bridge.parms["basegroup"])
    check(value == wanted and value.startswith("p"), "Enter writes the edges as point paths: %r" % value)
    yield from wait_cooked(tree, again=True)
    check(not tree.houdini_bridge.errors, "Houdini reads them %r" % tree.houdini_bridge.errors[-120:])
    set_parm(modifier, tree.houdini_bridge, "grouptype", "primitive")


def lock_and_chain(definitions, name):
    print("a locked modifier below is not cooked when the selection ends; a chained scatter above refuses it")
    modifiers, cook = support.modifiers(), support.cook()
    sphere = bpy.data.objects[name]
    make_active(sphere)
    group = sphere.modifiers[-1]
    extrude = modifiers.place_hda(bpy.context, "", {"type": "polyextrude::2.0", "label": "Poly Extrude",
                                                    "max_inputs": 1}, "MODIFIER")
    yield from wait_cooked(extrude.node_group)
    extrude.node_group.houdini_bridge.live = False
    invoke_arrow(group.node_group, "basegroup")
    yield 0.4
    yield from click(*face_point(sphere, 20))
    key("RET")
    yield 0.6
    check(not support.is_requested(extrude.node_group) and not cook.cooks.is_paused(extrude.node_group),
          "the locked Poly Extrude below resumes and is not asked to cook")
    sphere.modifiers.remove(extrude)
    support.maintain()
    scatter = modifiers.place_hda(bpy.context, HDA, definitions["bridge::scatter"], "MODIFIER")
    with bpy.context.temp_override(object=sphere):
        bpy.ops.object.modifier_move_to_index(modifier=scatter.name, index=0)
    yield from wait_cooked(scatter.node_group)
    yield from wait_cooked(group.node_group, again=True)
    before = state(name)
    check(invoke_arrow(group.node_group, "basegroup") == {"CANCELLED"} and support.selection().active() is None,
          "a scatter of instances chained above: the arrow refuses to start")
    compare(before, name, "the refused arrow")
    sphere.modifiers.remove(scatter)
    support.maintain()
    yield from wait_cooked(group.node_group, again=True)


def disabled_while_running(name):
    print("the extension disabled during a selection: everything is restored")
    sphere = bpy.data.objects[name]
    tree = sphere.modifiers[-1].node_group
    make_active(sphere)
    before = state(name)
    invoke_arrow(tree, "basegroup")
    yield 0.4
    support.disable()
    yield 0.5
    waited = 0.0
    while support.hython_processes() and waited < 15:  # the hython the extension ran stops with it
        yield 0.5
        waited += 0.5
    check(not support.hython_processes(), "disabled: no hython is left %s" % support.hython_processes())
    after = state(name)
    check(after["mode"] == "OBJECT" and after["hidden"] == before["hidden"] and after["objects"] == before["objects"]
          and [m[1:] for m in after["modifiers"]] == [m[1:] for m in before["modifiers"]] and not after["marker"],
          "disabled: the mode is left, the stack and the objects are back %s" % after)
    support.enable()
    support.redirect_user_dir()
    yield 1.0
