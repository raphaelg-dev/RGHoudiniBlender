"""UI test: the asynchronous path (timer + worker thread), with a verdict, and screenshots of the panels.

    blender --factory-startup --python tests/test_ui.py
    blender --factory-startup --enable-event-simulate --python tests/test_ui.py   # the group selection

Needs a window. Each step checks what must be true; the suite then stops the
server and quits Blender with exit code 0 when everything passed, 1 otherwise.
A wait that runs out is a failure, and a watchdog stops the whole suite when it
takes longer than BRIDGE_UI_WATCHDOG seconds (default 300). Screenshots go to
tests/screenshots/.
"""

import json
import os
import sys
import tempfile
import threading
import time

import bmesh
import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
import ui_selection  # noqa: E402
from support import HDA, ROOT, check, get_parm, positions, set_parm  # noqa: E402

SCREENSHOTS = os.path.join(ROOT, "tests", "screenshots")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "compat_0_17.blend")
FIXTURE_EXPECTED = os.path.join(ROOT, "tests", "fixtures", "compat_0_17.json")
FAVORITES = ["bridge::rock", "bridge::rings", "labs::cable_gen", "labs::lot_subdivision",
             "labs::tree_simple", "labs::building_generator", "labs::quickmaterial"]
WATCHDOG = float(os.environ.get("BRIDGE_UI_WATCHDOG", "300"))
COOKING = ("", "cooking…")
START = time.perf_counter()


def log(message):
    print("[test_ui] %.1f s  %s" % (time.perf_counter() - START, message), flush=True)


def view_3d():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                return window, area
    return None, None


def properties_area():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "PROPERTIES":
                return window, area
    return None, None


def screenshot(name, finder=view_3d):
    window, area = finder()
    if area is None:
        return
    os.makedirs(SCREENSHOTS, exist_ok=True)
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(SCREENSHOTS, name))
    log("screenshot " + name)


def frame_view():
    window, area = view_3d()
    space = area.spaces.active
    space.show_region_ui = True
    space.shading.type = "SOLID"
    space.shading.color_type = "VERTEX"
    # The sidebar tab is read-only from Python, except inside an override of its region.
    sidebar = next(r for r in area.regions if r.type == "UI")
    with bpy.context.temp_override(window=window, area=area, region=sidebar):
        try:
            sidebar.active_panel_category = "Houdini"
        except AttributeError:
            pass
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_all()


def show_modifier_tab():
    _, area = properties_area()
    if area is not None:
        area.spaces.active.context = "MODIFIER"


def houdini_modifier(obj):
    modifier = next(m for m in obj.modifiers if m.type == "NODES" and m.node_group)
    return modifier, modifier.node_group, modifier.node_group.houdini_bridge


def wait_for(condition, seconds, what):
    """Yield timer delays until `condition()` holds; a timeout is a failure. Returns whether it held."""
    start = time.perf_counter()
    while not condition():
        if time.perf_counter() - start > seconds:
            check(False, "%s within %d s" % (what, seconds))
            return False
        yield 0.1
    return True


def watch_idle(seconds):
    """Yield for `seconds` while sampling whether the engine is busy: (idle samples, samples, busy at the end)."""
    start, idle, samples = time.perf_counter(), 0, 0
    while time.perf_counter() - start < seconds:
        samples += 1
        idle += 0 if support.is_busy() else 1
        yield 0.1
    return idle, samples, support.is_busy()


def cooked(settings):
    return settings.status not in COOKING


def scenario():
    cook, modifiers, catalog = support.cook(), support.modifiers(), support.catalog()
    yield 1.0
    if bpy.app.use_event_simulate:
        # Started with --enable-event-simulate, Blender reads no event of the system, so its
        # windows are drawn only when asked: the steps that count draws run without it, and
        # this run is the step of the viewport group selection alone, which simulates clicks.
        print("event simulation: the viewport group selection alone")
        cook.ensure_server(blocking=True)
        yield from ui_selection.selection(support.definitions())
        return

    print("asynchronous cook of a new object")
    for other in list(bpy.data.objects):
        bpy.data.objects.remove(other)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.0, location=(0, 0, 1))
    sphere = bpy.context.active_object
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere
    cook.ensure_server(blocking=True)
    definitions = support.definitions()
    obj = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::rock"])
    modifier, tree, settings = houdini_modifier(obj)
    sphere.hide_set(True)
    frame_view()
    log("object created, asynchronous cook requested")
    if not (yield from wait_for(lambda: cooked(settings), 60, "a result")):
        return
    log("result: %s | %s | errors %r" % (settings.status, settings.timings, settings.errors))
    faces = len(settings.holder.data.polygons)
    check(not settings.errors and "faces" in settings.status, "the result arrives without error (%s)" % settings.status)
    check(faces >= 3 * len(sphere.data.polygons), "the cooked mesh is written: one subdivision (%d faces for %d)"
          % (faces, len(sphere.data.polygons)))
    frame_view()
    show_modifier_tab()
    yield 0.5

    print("quick changes merge into one cook")
    screenshot("1_rock.png")
    screenshot("1b_modifier_panel.png", properties_area)
    applied = []
    restore = support.record_applied_results(applied)
    for value in (0.4, 0.5, 0.6, 0.7, 0.8):
        set_parm(modifier, settings, "height", [value])
    set_parm(modifier, settings, "color", [0.2, 0.45, 0.8])
    set_parm(modifier, settings, "subdivisions", [2])
    yield 0.3
    done = yield from wait_for(lambda: not support.is_busy(), 60, "the changes cooked")
    restore()
    log("after changes: %s | %s | %d results applied" % (settings.status, settings.timings, len(applied)))
    if done:
        check(not settings.errors, "no error after the changes: %r" % settings.errors[-200:])
        check(1 <= len(applied) <= 2, "seven quick changes give one cook, not seven (%d)" % len(applied))
        check(len(settings.holder.data.polygons) == 4 * faces,
              "the last values are cooked: 2 subdivisions (%d faces, %d before)"
              % (len(settings.holder.data.polygons), faces))
        check(abs(get_parm(modifier, settings.parms["height"])[0] - 0.8) < 1e-6, "the last height is kept (0.8)")
    yield 0.5

    print("shelf: asynchronous catalog refresh")
    screenshot("2_rock_tuned.png")
    preferences = support.preferences()
    preferences.folders.clear()
    preferences.folders.add().path = os.path.dirname(HDA)
    preferences.include_packages = True
    catalog.refresh()
    yield 0.2
    refreshed = yield from wait_for(lambda: not support.catalog_state()["refreshing"], 90, "the catalog refresh")
    log("shelf: %d tools, error %r" % (len(catalog.state.tools), support.catalog_state()["error"]))
    tools = catalog.state.tools
    types = {tool["type"] for tool in tools}
    if refreshed:
        check(not support.catalog_state()["error"] and {"bridge::rock", "bridge::rings"} <= types,
              "the catalog holds the test HDAs (%d tools)" % len(tools))
    has_labs = any(name.startswith("labs::") for name in types)
    for prefix in FAVORITES:
        tool = next((t for t in tools if t["type"].startswith(prefix)), None)
        if tool is not None:
            catalog.toggle_favorite(catalog.tool_key(tool))
    favorites = {tool["type"] for tool in catalog.favorite_tools()}
    check({"bridge::rock", "bridge::rings"} <= favorites, "the test HDAs are favorites")
    if has_labs:
        labs = [prefix for prefix in FAVORITES if prefix.startswith("labs::")]
        missing = [prefix for prefix in labs if not any(name.startswith(prefix) for name in favorites)]
        check(not missing, "the SideFX Labs favorites are set (missing %s)" % missing)
    else:
        support.skip("SideFX Labs favorites: Labs is not in the catalog")
    bpy.context.view_layer.objects.active = None
    window_manager = bpy.context.window_manager
    window_manager.houdini_bridge_search = ""
    listed = [tool["type"] for tool in catalog.visible_tools(window_manager)]
    check(sorted(listed) == sorted(tool["type"] for tool in catalog.favorite_tools()),
          "without a search, the sidebar lists the favorites (%d)" % len(listed))
    yield 1.0

    screenshot("3_shelf_favorites.png")
    window_manager.houdini_bridge_search = "terrain"
    found = catalog.visible_tools(window_manager)
    log("search 'terrain': %d tools" % len(found))
    check(all("terrain" in " ".join([tool["label"], tool["type"], tool["group"], tool.get("summary", "")]).lower()
              for tool in found), "a search lists only matching tools (%d)" % len(found))
    yield 1.0

    print("modifier mode: the rock from the shelf on a monkey with a Subdivision above")
    screenshot("4_shelf_search.png")
    window_manager.houdini_bridge_search = ""
    preferences.default_target = "MODIFIER"
    for other in list(bpy.context.view_layer.objects):
        other.hide_set(True)
    bpy.ops.mesh.primitive_monkey_add(size=1.5, location=(0, 0, 1))
    monkey = bpy.context.active_object
    monkey.modifiers.new("Subdivision", "SUBSURF").levels = 1
    rock_key = next(catalog.tool_key(t) for t in catalog.state.tools if t["type"] == "bridge::rock")
    bpy.ops.houdini_bridge.tool(key=rock_key)
    frame_view()
    modifier, tree, settings = houdini_modifier(monkey)
    check(tree.houdini_bridge.enabled and settings.inputs[0].is_self, "the shelf adds a Houdini modifier to the monkey")
    yield 0.5
    if not (yield from wait_for(lambda: cooked(settings), 60, "the modifier's result")):
        return
    log("modifier: %s | %s | errors %r" % (settings.status, settings.timings, settings.errors))
    faces = len(settings.holder.data.polygons)
    check(not settings.errors and faces > 2 * 4 * len(monkey.data.polygons),
          "the Subdivision above reaches Houdini (%d faces for %d)" % (faces, len(monkey.data.polygons)))
    set_parm(modifier, settings, "height", [0.15])
    yield 0.2
    yield from wait_for(lambda: not support.is_busy(), 60, "the new height cooked")
    yield 1.0

    print("Edit Mode: the result follows an edit of the original mesh on its own")
    screenshot("5_modifier.png")
    bpy.ops.object.mode_set(mode="EDIT")
    reach = float(positions(settings.holder.data)[:, 0].max())
    results = []
    restore = support.record_applied_results(results)
    edit_mesh = bmesh.from_edit_mesh(monkey.data)
    for vertex in edit_mesh.verts:
        if vertex.co.x > 0.3:
            vertex.co.x += 0.6
    bmesh.update_edit_mesh(monkey.data)
    yield 0.3
    yield from wait_for(lambda: results and not support.is_busy(), 30, "a cook after the edit")
    restore()
    log("after the Edit Mode change: %s | %s" % (settings.status, settings.timings))
    farther = float(positions(settings.holder.data)[:, 0].max())
    check(results and not settings.errors, "the edit cooks again (%d results)" % len(results))
    check(farther > reach + 0.3, "the edit reaches the result (x %.2f → %.2f)" % (reach, farther))
    screenshot("6_modifier_edit_mode.png")
    idle, samples, busy = yield from watch_idle(3)
    log("idle after the edit: %d of %d checks idle" % (idle, samples))
    check(not busy and idle >= samples // 2, "idle after the edit, no loop (%d of %d checks idle)" % (idle, samples))
    bpy.ops.object.mode_set(mode="OBJECT")

    print("playback: an HDA reading $T cooks as the timeline plays")
    for other in list(bpy.context.view_layer.objects):
        other.hide_set(True)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.scene.frame_set(1)
    animated = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::animated"])
    modifier, tree, settings = houdini_modifier(animated)
    frame_view()
    if not (yield from wait_for(lambda: cooked(settings), 60, "the animated HDA's result")):
        return
    log("animated: %s | time dependent %s" % (settings.status, settings.time_dependent))
    check(not settings.errors and settings.time_dependent, "the animated HDA is time dependent")
    first = positions(settings.holder.data).copy()
    window, area = view_3d()
    region = next(r for r in area.regions if r.type == "WINDOW")
    frames = []
    restore = support.record_applied_results(frames)
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.animation_play()
    start = time.perf_counter()
    while time.perf_counter() - start < 3:
        yield 0.2
    restore()
    screenshot("7_animated_playback.png")
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.screen.animation_cancel(restore_frame=False)
    stopped = bpy.context.scene.frame_current
    log("playback: %d cooks in 3 s, stopped on frame %d | %s | errors %r"
        % (len(frames), stopped, settings.timings, settings.errors))
    check(len(frames) >= 10 and len(set(frames)) >= 10, "playback cooks frame after frame (%d cooks on %d frames)"
          % (len(frames), len(set(frames))))
    check(stopped > 10 and not settings.errors, "the timeline moved (frame %d), no error" % stopped)
    check(not np.allclose(first, positions(settings.holder.data)), "the result follows the frames")
    idle, samples, busy = yield from watch_idle(3)
    log("idle after playback: %d of %d checks idle" % (idle, samples))
    check(not busy, "idle after playback, no loop (%d of %d checks idle)" % (idle, samples))

    print("cache: 48 frames with the panel's button (background job)")
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 48
    scene.frame_set(1)
    timings = {"prepare": 0.0, "round_trip": 0.0, "sent": 0.0}
    restore = support.time_cache(timings)
    bpy.ops.houdini_bridge.cache(tree=tree.name, action="START")
    cache = support.cache()
    seen = {"progress": False}

    def cache_done():
        progress = cache.progress(tree.session_uid)
        if progress is not None and not seen["progress"] and progress[0] > 5:
            seen["progress"] = True
            screenshot("8_caching.png")
        return progress is None

    finished = yield from wait_for(cache_done, 120, "the cache")
    restore()
    log("cache: %s | use_cache %s | %d frames | frame back to %d"
        % (settings.status, settings.use_cache, cache.frame_count(settings), scene.frame_current))
    log("cache time: prepare (frame change + reading inputs) %.2f s, round trip until applied %.2f s"
        % (timings["prepare"], timings["round_trip"]))
    if finished:
        check(settings.use_cache and cache.frame_count(settings) == 48
              and settings.status.startswith("cached 48 frames"),
              "48 frames cached and in use (%s)" % settings.status)
        check(scene.frame_current == 1, "back to the frame the cache started from (%d)" % scene.frame_current)
        check(timings["round_trip"] > 0, "the frames went through the worker thread")
    scene.frame_set(30)
    yield 0.5
    screenshot("9_cached_frame_30.png")
    shown = support.evaluated(animated)[0]
    check(shown > 0, "frame 30 plays from the cache (%d faces)" % shown)
    idle, samples, busy = yield from watch_idle(2)
    log("idle in cache mode: %d of %d checks idle" % (idle, samples))
    check(not busy and not support.is_requested(tree), "no cook in cache mode (%d of %d checks idle)" % (idle, samples))

    print("ramps and multiparms: the ramp widgets in the sidebar, nested panels in the modifier")
    for other in list(bpy.context.view_layer.objects):
        other.hide_set(True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=40, size=3, location=(0, 0, 0))
    grid = bpy.context.active_object
    modifiers.place_hda(bpy.context, HDA, definitions["bridge::ramps"], "MODIFIER")
    modifier, tree, settings = houdini_modifier(grid)
    frame_view()
    if not (yield from wait_for(lambda: cooked(settings), 60, "the ramps result")):
        return
    check(not settings.errors and {"shape", "tint"} <= {p.name for p in settings.parms if p.kind == "ramp"},
          "the ramps are described")
    set_parm(modifier, settings, "bumps", [2])
    yield 1.0
    if not (yield from wait_for(lambda: not support.is_busy(), 60, "the multiparm rebuild")):
        return
    log("ramps: %s | %d parameters | errors %r" % (settings.status, len(settings.parms), settings.errors))
    names = {p.name for p in settings.parms}
    check(not settings.errors and {"radius1", "radius2"} <= names and "radius3" not in names,
          "a new multiparm count rebuilds the inputs (%d parameters)" % len(settings.parms))
    frame_view()
    show_modifier_tab()
    yield 1.0

    screenshot("10_ramps_sidebar.png")
    screenshot("10b_ramps_modifier_panel.png", properties_area)
    yield from panel_draws(definitions, catalog)
    yield from flags(definitions)
    yield from code_block()
    yield from undo_steps(definitions)

    print("preferences: one screenshot per tab")
    bpy.ops.houdini_bridge.open_preferences()
    yield 1.0
    window, area = next(((w, a) for w in bpy.context.window_manager.windows for a in w.screen.areas
                         if a.type == "PREFERENCES"), (None, None))
    check(area is not None, "the preferences window opens")
    if area is None:
        return
    shown = []
    for tab in ["MODIFIERS", "OUTPUT", "SHELF", "HOUDINI", "VIEWPORT", "HOTKEYS"]:
        preferences.tab = tab
        area.tag_redraw()
        yield 0.7
        path = os.path.join(SCREENSHOTS, "11_preferences_%s.png" % tab.lower())
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.screen.screenshot_area(filepath=path)
        log("screenshot preferences " + tab)
        if os.path.exists(path) and os.path.getmtime(path) >= START - 1:
            shown.append(tab)
    check(len(shown) == 6, "six preference tabs drawn and captured %s" % shown)

    yield from failures(definitions)
    support.skip("the viewport group selection: run this suite again with --enable-event-simulate")


# --- the modifier panel ---------------------------------------------------------------


TEST_TYPES = ("bridge::rock", "bridge::rings", "bridge::groups", "bridge::animated", "bridge::scatter",
              "bridge::grass", "bridge::fireflies", "bridge::fog", "bridge::shatter", "bridge::ramps")


def panel_draws(definitions, catalog):
    """The modifier panel draws without exception for every test HDA and Tree Branch Generator
    (an exception in a draw does not stop Blender: it is caught here and counted): its header
    in the Properties editor, its body in a test panel of the sidebar too, since Blender folds
    it at first. And a value set on the input the panel draws cooks asynchronously."""
    print("the modifier panel: every test HDA and Tree Branch Generator draw without exception")
    modifiers = support.modifiers()
    for other in list(bpy.context.view_layer.objects):
        other.hide_set(True)
    made, grids = [], []
    for index, node_type in enumerate(TEST_TYPES):
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=2, location=(index * 3, 8, 0))
        grids.append(bpy.context.active_object)
        placed = modifiers.place_hda(bpy.context, HDA, definitions[node_type], "MODIFIER")
        made.append(placed.id_data if isinstance(placed, bpy.types.Modifier) else placed)  # a generator: an object
    labs = next((tool for tool in catalog.state.tools if tool["type"].startswith("labs::tree_branch_generator")), None)
    if labs is not None:
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=2, location=(0, 14, 0))
        made.append(modifiers.place_hda(bpy.context, labs["file"], labs, "MODIFIER").id_data)
    else:
        support.skip("Tree Branch Generator: Labs is not in the catalog")
    trees = [houdini_modifier(obj)[1] for obj in made]
    yield from wait_for(lambda: not support.is_busy() and all(cooked(t.houdini_bridge) for t in trees), 120,
                        "the test HDAs cooked")
    watch = support.watch_panel_draws()
    remove_bodies = support.show_panel_bodies()
    try:
        for obj in made:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            for _ in range(3):
                for area in bpy.context.window.screen.areas:
                    area.tag_redraw()
                yield 0.1
            name = houdini_modifier(obj)[2].node_type.split("::")[1]
            screenshot("12_modifier_panel_%s.png" % name, properties_area)
            screenshot("12_modifier_body_%s.png" % name)
    finally:
        remove_bodies()
        calls, errors = watch()
    check(calls >= len(made) and not errors, "the panel draws %d Houdini modifiers %d times without exception %s"
          % (len(made), calls, errors[:2]))

    print("a value set on the input the panel draws cooks asynchronously")
    modifier, tree, settings = houdini_modifier(made[0])
    applied = []
    restore = support.record_applied_results(applied)
    support.modifier_input(modifier, settings.parms["height"].socket).value = 0.65
    yield 0.3
    done = yield from wait_for(lambda: applied and not support.is_busy(), 60, "the cook of the new value")
    restore()
    sent = support.peek_parms(tree) or {}
    check(done and abs(sent.get("height", [0])[0] - 0.65) < 1e-6,
          "the poll sees it and the timer cooks it (%d result)" % len(applied))
    for obj in set(made + grids):
        bpy.data.objects.remove(obj)


def text_editors():
    """[(window, area)] of the Text Editors of every window."""
    return [(w, a) for w in bpy.context.window_manager.windows for a in w.screen.areas if a.type == "TEXT_EDITOR"]


def code_block():
    """An Attribute Wrangle's snippet: its code block drawn, Houdini interface on and off, without exception
    (screenshots); Edit opens it in a Text Editor of its own, once; what is typed cooks through the
    timer, an error shows; the window closed, the mirror goes; the modifier removed, its window too."""
    print("a code parameter: the code block and its Text Editor")
    editor, modifiers = support.code_editor(), support.modifiers()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=2, location=(0, -8, 0))
    grid = bpy.context.active_object
    modifier = modifiers.place_hda(bpy.context, "", {"type": "attribwrangle", "label": "Attribute Wrangle",
                                                     "max_inputs": 1}, "MODIFIER")
    tree, settings = modifier.node_group, modifier.node_group.houdini_bridge
    if not (yield from wait_for(lambda: cooked(settings) and not support.is_busy(), 60, "the wrangle cooked")):
        return
    set_parm(modifier, settings, "snippet", "// Lift the points\n@P.y += 0.5;\n" + "\n".join(
        "f@step%d = %d;" % (n, n) for n in range(8)))
    yield from wait_for(lambda: not support.is_busy() and (support.peek_parms(tree) or {}).get("snippet"), 60,
                        "the snippet cooked")
    check(settings.parms["snippet"].editor == "vex", "the snippet is VEX")
    show_modifier_tab()
    watch = support.watch_panel_draws()
    remove_bodies = support.show_panel_bodies()
    preferences = support.preferences()
    try:
        yield from redraw_all()
        screenshot("14_code_block.png")
        preferences.houdini_interface = False
        yield from redraw_all()
        screenshot("14_code_block_interface_off.png")
        preferences.houdini_interface = True
        yield from redraw_all()
    finally:
        calls, errors = watch()
    check(calls and not errors, "the code block draws, Houdini interface on and off, without exception %s" % errors[:1])

    print("Edit: a Text Editor in a window of its own, once")
    window, area = properties_area()
    windows = len(bpy.context.window_manager.windows)
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.houdini_bridge.edit_code(tree=tree.name, parm="snippet")
    yield 0.5
    found = [(w, a) for w, a in text_editors() if w != window]
    space = found[0][1].spaces.active if found else None
    check(len(bpy.context.window_manager.windows) == windows + 1 and space is not None and space.text is not None
          and space.show_line_numbers and space.show_syntax_highlight and space.text.name.endswith(".osl"),
          "a window of its own, line numbers and highlighting on, the text named for OSL")
    if space is None:
        remove_bodies()
        return
    with bpy.context.temp_override(window=found[0][0], area=found[0][1]):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(SCREENSHOTS, "14_code_editor.png"))
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.houdini_bridge.edit_code(tree=tree.name, parm="snippet")
    yield 0.3
    check(len(bpy.context.window_manager.windows) == windows + 1 and len(bpy.data.texts) == 1,
          "Edit again: no second window, no second text")

    print("typed: the timer writes it and the modifier cooks; an error shows")
    text = space.text
    text.from_string("@P.y += 1;")
    yield from wait_for(lambda: (support.peek_parms(tree) or {}).get("snippet") == "@P.y += 1;" and cooked(settings)
                        and not support.is_busy(), 30, "the typed code cooked")
    check(not settings.errors, "cooked without error")
    text.from_string("@P.y += ;")
    yield from wait_for(lambda: settings.errors and not support.is_busy(), 30, "the error of VEX")
    check("syntax" in settings.errors.lower(), "the error shows on the modifier %r" % settings.errors)
    yield from redraw_all()
    screenshot("14_code_error.png")
    text.from_string("@P.y += 1;")
    yield from wait_for(lambda: not settings.errors and not support.is_busy(), 30, "the error gone")

    print("the window closed: the mirror goes, then the modifier removed: its window too")
    with bpy.context.temp_override(window=found[0][0]):
        bpy.ops.wm.window_close()
    yield from wait_for(lambda: not editor.mirrors() and not bpy.data.texts, 5, "the mirror gone")
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.houdini_bridge.edit_code(tree=tree.name, parm="snippet")
    yield 0.3
    check(len(text_editors()) == 1 and len(bpy.data.texts) == 1, "opened again from the parameter")

    print("a file saved with the editor open: no text in it, and its window closed when it opens")
    tree_name, grid_name = tree.name, grid.name
    path = os.path.join(tempfile.mkdtemp(), "code_editor.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=True)
    yield 1.0
    check(not text_editors() and not bpy.data.texts and not editor.mirrors(),
          "opened again: no Text Editor window, no text, no mirror %s" % [w.screen.name for w, _ in text_editors()])
    grid = bpy.data.objects[grid_name]
    modifier = grid.modifiers[0]
    window, area = properties_area()
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.houdini_bridge.edit_code(tree=tree_name, parm="snippet")
    yield 0.3
    check(len(text_editors()) == 1, "Edit opens it again in that file")
    windows = len(bpy.context.window_manager.windows) - 1
    grid.modifiers.remove(modifier)
    yield from wait_for(lambda: not text_editors() and not bpy.data.texts, 5, "the editor closed with its modifier")
    check(len(bpy.context.window_manager.windows) == windows and not editor.mirrors(),
          "no window, no text, no mirror left")
    remove_bodies()
    bpy.data.objects.remove(grid)


def undo_once():
    """One real undo, as Ctrl+Z in the 3D view."""
    window, area = view_3d()
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.ed.undo()


def undo_steps(definitions):
    """Real undos (bpy.ops.ed.undo), through the timer: back to the step of a parameter change,
    and an undo while a cache is being made."""
    modifiers, cache = support.modifiers(), support.cache()
    print("undo: back to the step of a parameter change, pushed before its cook")
    for other in list(bpy.context.view_layer.objects):
        if other is not None:  # an object removed by the step before, until the view layer syncs
            other.hide_set(True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=2, location=(0, 0, 0))
    grid = bpy.context.active_object
    grid.name = name = "Undo Grid"
    modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    modifier, tree, settings = houdini_modifier(grid)
    frame_view()
    if not (yield from wait_for(lambda: cooked(settings) and not support.is_busy(), 60, "the rock on the grid")):
        return
    bpy.ops.ed.undo_push(message="Rock placed")
    set_parm(modifier, settings, "height", [1.2])
    bpy.ops.ed.undo_push(message="Height")  # where a change in the panel pushes its step: before the cook
    yield 0.3
    if not (yield from wait_for(lambda: not support.is_busy(), 60, "the new height cooked")):
        return
    expected = positions(settings.holder.data).copy()
    grid.select_set(False)
    bpy.ops.ed.undo_push(message="Deselect")
    undo_once()
    yield 0.5
    yield from wait_for(lambda: not support.is_busy(), 60, "what the undo asks for cooked")
    modifier, tree, settings = houdini_modifier(bpy.data.objects[name])
    shown = positions(settings.holder.data)
    height = get_parm(modifier, settings.parms["height"])[0]
    check(abs(height - 1.2) < 1e-6 and shown.shape == expected.shape and np.allclose(shown, expected, atol=1e-5),
          "after the undo, the result is the one of the height the panel shows (%.2f): %s"
          % (height, "same points" if shown.shape == expected.shape and np.allclose(shown, expected, atol=1e-5)
             else "other points"))

    print("undo while a cache is being made: the cache stops, and the modifier cooks again")
    for other in list(bpy.context.view_layer.objects):
        if other is not None:  # an object removed by the step before, until the view layer syncs
            other.hide_set(True)
    bpy.ops.object.select_all(action="DESELECT")
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 48
    scene.frame_set(1)
    animated = modifiers.create_hda_object(bpy.context, HDA, definitions["bridge::animated"])
    animated.name = name = "Undo Animated"
    modifier, tree, settings = houdini_modifier(animated)
    uid = tree.session_uid
    if not (yield from wait_for(lambda: cooked(settings) and not support.is_busy(), 60, "the animated HDA")):
        return
    bpy.ops.ed.undo_push(message="Animated placed")
    animated["undo_mark"] = 1
    bpy.ops.ed.undo_push(message="Marked")
    bpy.ops.houdini_bridge.cache(tree=tree.name, action="START")
    yield from wait_for(lambda: (cache.progress(uid) or (0, 0))[0] >= 5, 60, "five frames cached")
    undo_once()
    stopped = yield from wait_for(lambda: cache.progress(uid) is None, 20, "the cache stopped after the undo")
    tree = bpy.data.objects[name].modifiers[-1].node_group
    settings = tree.houdini_bridge
    check(stopped and not settings.use_cache and not settings.status.startswith("caching"),
          "the cache job is gone, the modifier is not left caching (%s)" % settings.status)
    applied = []
    restore = support.record_applied_results(applied)
    bpy.context.scene.frame_set(10)  # the undo replaced the scene
    yield 0.3
    yield from wait_for(lambda: applied and not support.is_busy(), 30, "a cook at frame 10")
    restore()
    check(applied and not settings.errors, "the modifier cooks again (%d results)" % len(applied))
    bpy.context.scene.frame_set(1)
    yield 0.3


def redraw_all(times=3):
    """Yield while every area is drawn again, `times` times."""
    for _ in range(times):
        for area in bpy.context.window.screen.areas:
            area.tag_redraw()
        yield 0.1


def flags(definitions):
    """The Template and Display flags of the node row, in their states (screenshots), and the
    Template flag's batch: made once per result, not at each frame."""
    print("the node flags: Template draws a ghost wireframe, Display shows one HDA's output")
    modifiers, template, display = support.modifiers(), support.template(), support.display()
    bpy.context.view_layer.update()  # the objects panel_draws removed leave the view layer
    for other in list(bpy.context.view_layer.objects):
        if other is not None:
            other.hide_set(True)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=16, y_subdivisions=16, size=3, location=(0, 0, 0))
    grid = bpy.context.active_object
    first = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    grid.modifiers.new("Subdivision", "SUBSURF").levels = 1
    second = modifiers.place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    trees = (first.node_group, second.node_group)
    yield from wait_for(lambda: not support.is_busy() and all(cooked(t.houdini_bridge) for t in trees), 60,
                        "the two rocks cooked")
    set_parm(second, trees[1].houdini_bridge, "effect", "inflate")
    set_parm(second, trees[1].houdini_bridge, "subdivisions", [0])
    yield 0.3
    yield from wait_for(lambda: not support.is_busy(), 60, "the second rock cooked again")
    frame_view()
    show_modifier_tab()
    yield from redraw_all()
    screenshot("13_flags_none.png", properties_area)

    def template_stats():
        return support.template_stats(template)

    builds = template_stats()["builds"]
    trees[0].houdini_bridge.template = True
    yield from redraw_all()
    made = template_stats()["builds"] - builds
    yield from redraw_all(5)
    check(made == 1 and template_stats()["builds"] - builds == 1,
          "Template: one batch made for the result, none at the next frames (%d, then %d)"
          % (made, template_stats()["builds"] - builds))
    screenshot("13_template_viewport.png")
    screenshot("13_flags_template.png", properties_area)
    applied = []
    restore = support.record_applied_results(applied)
    set_parm(first, trees[0].houdini_bridge, "height", [0.5])
    yield 0.3
    yield from wait_for(lambda: applied and not support.is_busy(), 60, "the first rock cooked again")
    restore()
    yield from redraw_all()
    check(template_stats()["builds"] - builds == 2, "a new result: its batch is made again, once (%d)"
          % (template_stats()["builds"] - builds))

    bpy.ops.houdini_bridge.display(tree=trees[0].name)
    yield from redraw_all()
    check(display.is_flagged(first) and not second.show_viewport, "Display: the modifiers below are cut")
    screenshot("13_display_viewport.png")
    screenshot("13_flags_display.png", properties_area)
    bpy.ops.houdini_bridge.bypass(tree=trees[0].name)
    yield from redraw_all()
    screenshot("13_flags_display_bypassed.png", properties_area)
    bpy.ops.houdini_bridge.bypass(tree=trees[0].name)
    bpy.ops.houdini_bridge.display(tree=trees[0].name)
    check(template_stats()["batches"] == 1, "a templated modifier keeps its batch (%d)" % template_stats()["batches"])
    trees[0].houdini_bridge.template = False
    yield from redraw_all()
    check(all(m.show_viewport and m.show_render for m in grid.modifiers), "lifted: the stack is whole again")
    check(template_stats()["batches"] == 0, "the Template flag lifted: its batch is freed at the next draw (%d left)"
          % template_stats()["batches"])
    yield from wait_for(lambda: not support.is_busy(), 60, "the stack settled")
    bpy.data.objects.remove(grid)


# --- failures: hython killed, server stopped, extension disabled, file changed --------


def big_rock(definitions):
    """A rock modifier on a 250,000 face grid: a cook long enough to be caught in flight."""
    for other in list(bpy.context.view_layer.objects):
        other.hide_set(True)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=501, y_subdivisions=501, size=4, location=(0, 0, 0))
    grid = bpy.context.active_object
    modifier = support.modifiers().place_hda(bpy.context, HDA, definitions["bridge::rock"], "MODIFIER")
    return grid, modifier, modifier.node_group


def full_result(grid, settings):
    """The rock's whole result is written: one subdivision of every face of the grid."""
    holder = settings.holder
    return (not settings.errors and "faces" in settings.status and holder is not None
            and len(holder.data.polygons) == 4 * len(grid.data.polygons))


def cook_in_flight(tree):
    """Ask for a cook of every input and wait until it is on its way to Houdini."""
    bpy.ops.houdini_bridge.recook(tree=tree.name)
    return (yield from wait_for(lambda: support.is_in_flight(tree), 30, "a cook in flight"))


def wait_until_gone(seconds):
    """Yield until no houdini-bridge thread and no hython process of this Blender is left, or
    `seconds` pass. Returns {"threads": [...], "hython": [...]} as last seen, and "seen": the
    hython processes seen meanwhile."""
    start = time.perf_counter()
    seen = set()
    while True:
        left = {"threads": support.bridge_threads(), "hython": support.hython_processes()}
        seen.update(left["hython"])
        if not left["threads"] and not left["hython"] or time.perf_counter() - start > seconds:
            left["seen"] = sorted(seen)
            return left
        yield 0.5


class Recorder:
    """Writes to a stream and keeps a copy of what was written (tracebacks printed by handlers)."""

    def __init__(self, stream):
        self.stream = stream
        self.parts = []

    def write(self, text):
        self.parts.append(text)
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()


def value_in_flight(modifier, tree):
    """A value typed while a cook is in flight is sent once that cook is back, and the result
    shown is the one of that value."""
    print("a value changed during a cook in flight")
    settings = tree.houdini_bridge
    if not (yield from cook_in_flight(tree)):
        return
    height = get_parm(modifier, settings.parms["height"])[0] + 0.7
    set_parm(modifier, settings, "height", [height])
    yield from wait_for(lambda: not support.is_in_flight(tree), 60, "the cook in flight back")
    yield 0.5  # a few ticks of the poll
    yield from wait_for(lambda: not support.is_busy(), 60, "what the new value asks for cooked")
    shown = positions(settings.holder.data).copy()
    bpy.ops.houdini_bridge.recook(tree=tree.name)
    yield 0.3
    yield from wait_for(lambda: not support.is_busy(), 60, "a recook of the new value")
    again = positions(settings.holder.data)
    check(abs(support.peek_parms(tree)["height"][0] - height) < 1e-6 and shown.shape == again.shape
          and np.allclose(shown, again, atol=1e-5),
          "the result shown is the one of the value typed during the cook (%s)"
          % ("same points as a recook" if shown.shape == again.shape and np.allclose(shown, again, atol=1e-5)
             else "other points than a recook"))


def failures(definitions):
    grid, modifier, tree = big_rock(definitions)
    settings = tree.houdini_bridge
    frame_view()
    if not (yield from wait_for(lambda: full_result(grid, settings) and not support.is_busy(), 90,
                                "the result on the big grid")):
        return
    yield from value_in_flight(modifier, tree)

    print("hython killed during a cook in flight")
    if not (yield from cook_in_flight(tree)):
        return
    yield 0.3
    support.kill_server()
    start = last = time.perf_counter()
    longest = 0.0
    while not (settings.status == "failed" and not support.is_busy()) and time.perf_counter() - start < 30:
        yield 0.05
        now = time.perf_counter()
        longest, last = max(longest, now - last), now
    log("after the kill: %s | %r" % (settings.status, settings.errors[-160:]))
    check(settings.status == "failed" and settings.errors,
          "the modifier shows the lost connection (%s: %s)" % (settings.status, settings.errors[-120:]))
    idle, samples, busy = yield from watch_idle(2)
    check(not busy and idle == samples, "the engine is idle again, no loop (%d of %d checks idle)" % (idle, samples))
    check(longest < 1.0, "Blender does not freeze (longest pause %.2f s)" % longest)
    check(not support.server_running(), "the killed hython is not taken for a running server")
    bpy.ops.houdini_bridge.server(action="START")
    yield 0.3
    restarted = yield from wait_for(lambda: support.server_running() and full_result(grid, settings)
                                    and not support.is_busy(), 90, "the restart and the cook after it")
    log("after the restart: %s | %s" % (settings.status, settings.timings))
    check(restarted, "the Start button starts hython again and the next cook writes the whole result (%d faces)"
          % len(settings.holder.data.polygons))

    print("the server stopped while cooks wait")
    if not (yield from cook_in_flight(tree)):
        return
    set_parm(modifier, settings, "height", [0.9])
    bpy.ops.houdini_bridge.recook(tree=tree.name)  # waits: a cook of this modifier is in flight
    check(support.is_in_flight(tree) and support.is_requested(tree), "a cook in flight and a change waiting behind it")
    bpy.ops.houdini_bridge.server(action="STOP")
    seen = set()

    def settled():
        seen.add(settings.status)
        return support.server_running() and full_result(grid, settings) and not support.is_busy()

    finished = yield from wait_for(settled, 90, "the waiting cook after the stop")
    log("after the stop: statuses %s, %s" % (sorted(seen), settings.status))
    check("failed" in seen, "the cook in flight ends with an error, not silently (statuses %s)" % sorted(seen))
    check(finished and abs(support.get_parm(modifier, settings.parms["height"])[0] - 0.9) < 1e-6,
          "the waiting change starts the server again and cooks (%s)" % settings.status)

    print("the extension disabled during a cook in flight, then enabled again")
    settings.template = True
    old_template = support.template()
    draws = support.template_stats(old_template)["draws"]
    yield from redraw_all()
    check(support.template_stats(old_template)["draws"] > draws,
          "enabled: the Template flag's drawing runs at each frame")
    if not (yield from cook_in_flight(tree)):
        return
    support.disable()
    draws = support.template_stats(old_template)["draws"]
    yield from redraw_all()
    draws_after = support.template_stats(old_template)["draws"] - draws
    check(draws_after == 0, "disabled: the Template flag draws nothing any more, no draw handler is left "
          "(%d draws after)" % draws_after)
    left = yield from wait_until_gone(15)
    log("disabled during a cook: %s" % left)
    check(not left["threads"], "disabled during a cook: no houdini-bridge thread is left %s" % left["threads"])
    check(not left["hython"], "disabled during a cook: no hython is left %s" % left["hython"])
    check(modifier.show_group_selector and modifier.show_manage_panel,
          "disabled: Blender draws the modifier's inputs again")
    support.enable()
    support.redirect_user_dir()

    def interface_back():
        sockets = [i for i in tree.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"
                   and i.socket_type != "NodeSocketGeometry"]
        return not modifier.show_group_selector and all(i.hide_in_modifier for i in sockets)

    yield from wait_for(interface_back, 5, "the Houdini interface back on the modifier")
    check(interface_back(),
          "enabled again: the modifier has the Houdini interface again, from the timer after the registration")
    settings = tree.houdini_bridge
    bpy.ops.houdini_bridge.recook(tree=tree.name)
    yield 0.3
    cooked_again = yield from wait_for(lambda: full_result(grid, settings) and not support.is_busy(), 90,
                                       "a cook once enabled again")
    check(cooked_again, "enabled again: an asynchronous cook arrives (%s)" % settings.status)

    print("the extension disabled while hython starts")
    bpy.ops.houdini_bridge.server(action="STOP")
    check(not support.server_running(), "the server is stopped")
    bpy.ops.houdini_bridge.server(action="START")
    check(support.server_starting(), "hython starts in the background")
    support.disable()
    left = yield from wait_until_gone(45)
    log("disabled during the start: %s" % left)
    check(left["seen"], "the start under way did launch a hython %s" % left["seen"])
    check(not left["hython"], "disabled during the start: no hython is left once it would have started %s"
          % left["hython"])
    check(not left["threads"], "disabled during the start: no houdini-bridge thread is left %s" % left["threads"])
    if left["hython"]:
        support.kill_processes(left["hython"])
    support.enable()
    support.redirect_user_dir()

    print("another file opened during a cook in flight")
    settings = tree.houdini_bridge
    bpy.ops.houdini_bridge.recook(tree=tree.name)
    yield 0.3
    if not (yield from wait_for(lambda: full_result(grid, settings) and not support.is_busy(), 90,
                                "the cook before the file change")):
        return
    if not (yield from cook_in_flight(tree)):
        return
    old_key = tree.session_uid
    applied = []
    restore = support.record_applied_targets(applied)
    recorder = Recorder(sys.stderr)
    sys.stderr = recorder
    try:
        bpy.ops.wm.open_mainfile(filepath=FIXTURE, load_ui=False)
        with open(FIXTURE_EXPECTED, encoding="utf-8") as file:
            expected = json.load(file)
        live = [bpy.data.objects[name].modifiers[wanted["hda"]["name"]].node_group
                for name, entry in sorted(expected["objects"].items()) for wanted in entry["modifiers"]
                if not wanted["settings"]["use_cache"]]

        def all_cooked():
            return (not support.is_busy() and all(tree.session_uid in applied for tree in live)
                    and all(cooked(tree.houdini_bridge) for tree in live))

        done = yield from wait_for(all_cooked, 120, "the modifiers of the new file")
        yield 1.0  # the result of the old file would come back meanwhile
    finally:
        sys.stderr = recorder.stream
        restore()
    errors = {tree.name: tree.houdini_bridge.errors[-120:] for tree in live if tree.houdini_bridge.errors}
    log("after the file change: %d results applied, errors %s" % (len(applied), errors))
    check(old_key not in applied, "the result of the old file is written nowhere")
    check(done and not errors, "the %d live modifiers of the new file cook %s" % (len(live), errors))
    text = "".join(recorder.parts)
    check("Traceback" not in text, "no exception while the file changes %r" % text[-300:])


def finish(reason=""):
    """Stop the server and leave Blender with the verdict as exit code."""
    if reason:
        check(False, reason)
    try:
        support.cook().stop_server()
    except Exception as error:
        check(False, "the server stops: %s" % error)
    check(not support.server_running(), "the server is stopped at the end")
    left = support.hython_processes()
    check(not left, "no hython process is left at the end %s" % left)
    support.kill_processes(left)
    log("END: %d failures" % len(support.failures))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if support.failures else 0)


flow = scenario()


def step():
    try:
        return next(flow)
    except StopIteration:
        finish()
    except Exception:
        import traceback

        traceback.print_exc()
        finish("the scenario raised an exception")
    return None


def watchdog():
    """Runs on its own thread: stops a blocked suite, whatever the main thread does."""
    print("[test_ui] FAIL  the suite took more than %d s: stopped by the watchdog" % WATCHDOG, flush=True)
    try:
        support.kill_server()
    finally:
        sys.stdout.flush()
        os._exit(1)


def start():
    support.enable()
    support.redirect_user_dir()  # test favorites and catalog go to a temporary folder, not the user's
    timer = threading.Timer(WATCHDOG, watchdog)
    timer.daemon = True
    timer.start()
    bpy.app.timers.register(step, first_interval=1.0, persistent=True)  # survives the file change


start()
