"""Capture the images of the documentation into docs/images/.

    blender --factory-startup --python docs/capture_images.py

Needs the installed extension, Houdini and ffmpeg on PATH. It opens a 1920x1080
window, whatever the size of the monitor, and ignores the mouse and keyboard while
it runs.
Set CAPTURE_ONLY to a comma-separated list of shot names to retake only those.
Blender quits when done. The demo file and the test HDAs are copied to a temporary
folder first: nothing in the repository is opened or saved, and the preferences
are left alone. The capture window runs without the OCIO variable, so the colors
are Blender's own whatever the machine sets.

Blender folds the Houdini panel of each new modifier, and Python cannot unfold it: the shots
click its node row, found by the color of its state dot, as a user does (open_node_panels).
The flags of the node row are clicked the same way. The tools of the other packages of the
machine are hidden with the preference Hide, so the shelf shows Houdini, Labs and the test HDAs.

live-edit.gif is cut from live_edit.mp4, a screen recording kept next to it.
Captions need the Segoe UI font, so a run outside Windows should set CAPTION_FONT.
"""

import importlib
import math
import os
import shutil
import subprocess
import tempfile
import time
import traceback

import addon_utils
import bmesh
import bpy
import numpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Euler, Matrix, Vector

MODULE = "bl_ext.user_default.rg_houdini_bridge"
# Where each module of the extension used here lives in its package, beyond its scripting interface
# (api.py, whose names do not move): a module that moves is fixed here.
EXTENSION_MODULES = {"assets": "blender.features.shelf.assets", "catalog": "blender.features.shelf.catalog",
                     "cook": "blender.core.cook", "paths": "blender.core.paths"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(ROOT, "docs", "images")
WORK = tempfile.mkdtemp(prefix="bridge_capture_")
DEMO = os.path.join(WORK, "demos", "visualization_demo.blend")
HDA = os.path.join(WORK, "tests", "hda", "bridge_test.hda")
ONLY = {name.strip() for name in os.environ.get("CAPTURE_ONLY", "").split(",") if name.strip()}
UI_SCALE = 1.25
STATUS_BAR = 30
PREFERENCES_TABS_TOP = 300
PREFERENCES_MODIFIERS_HEIGHT = 480
PREFERENCES_HOUDINI_HEIGHT = 430
STACK_CELL = 470
STACK_CELL_HEIGHT = 620
ASSET_SHELF_ROOTS = {"Houdini", "Labs"}
ASSET_SHELF_CATEGORY = 186  # Houdini > Geometry, in pixels below the top of the viewport
SIDEBAR_WIDTH = 510
RIGHT_COLUMN_EXTRA = 200  # pixels added to the right column: the node row of a long name fits
SIDEBAR_SHELF_HEIGHT = 450
SIDEBAR_TABS_HEIGHT = 450
TEMPLATE_LEFT_CROP = 200
DISPLAY_LEFT_CROP = 200
SELECTION_LEFT_CROP = 0  # the orange frame of the selection, whole
ADD_NODE_HEIGHT = 470  # from the top of the Properties editor down to below the search
MODIFIER_PANEL_LEFT_CROP = 200
RAMPS_LEFT_CROP = 200
MULTIPARM_SCROLL = 10  # wheel steps over the Properties editor: the end of the panel
MULTIPARM_TOP = 109  # then the crop starts at the count of bumps, below the curve
RAMPS_SCROLL = 5
GROUPS_STACK_LEFT_CROP = 200
VISUALIZE_LEFT_CROP = 200
INPUTS_LEFT_CROP = 200
CACHE_ABOVE_ROW = 40  # the crop of cache-section.png around the node row, at a UI scale of 1
CACHE_BELOW_ROW = 370
CAPTION_FONT = os.environ.get("CAPTION_FONT", "C:/Windows/Fonts/segoeui.ttf")
DAY = {"zenith": (0.13, 0.28, 0.62), "horizon": (0.66, 0.76, 0.90),
       "wall": (0.20, 0.35, 0.64), "ground": (0.50, 0.53, 0.57)}
DUSK = {"zenith": (0.12, 0.15, 0.36), "horizon": (0.90, 0.46, 0.30),
        "wall": (0.20, 0.20, 0.46), "ground": (0.50, 0.30, 0.30)}
SKY = {"zenith": (0.025, 0.085, 0.33), "horizon": (0.17, 0.36, 0.74), "ground": (0.17, 0.36, 0.74)}
FAVORITES = ("mountain", "polyextrude::2.0", "remesh::2.0", "labs::edge_damage::2.1",
             "labs::cable_gen", "labs::lot_subdivision", "labs::tree_simple")


def extension(name):
    """A module of the installed extension (enabled by enable_extension)."""
    return importlib.import_module(MODULE + "." + EXTENSION_MODULES[name])


def api():
    """The scripting interface of the installed extension (enabled by enable_extension)."""
    return importlib.import_module(MODULE + ".api")


def log(message):
    print("[capture] " + message, flush=True)


# --- window helpers -------------------------------------------------------------------


def window():
    return bpy.context.window_manager.windows[0]


def find_area(kind):
    return next((a for a in window().screen.areas if a.type == kind), None)


def find_region(area, kind):
    return next(r for r in area.regions if r.type == kind)


def view_space():
    return find_area("VIEW_3D").spaces.active


def redraw():
    for area in window().screen.areas:
        area.tag_redraw()


def idle():
    return not extension("cook").cooks.is_busy()


def settle(seconds=1.0, timeout=120.0):
    """Wait for Houdini to finish, then for the viewport to catch up."""
    start = time.perf_counter()
    while not idle() and time.perf_counter() - start < timeout:
        yield 0.1
    calm = time.perf_counter()
    while time.perf_counter() - calm < seconds:
        if not idle():
            calm = time.perf_counter()
        redraw()
        yield 0.1


def replace_file(source, target):
    """os.replace, tried again for a few seconds: a viewer or an indexer may hold the old file."""
    for _ in range(50):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(0.1)
    os.replace(source, target)


def crop_image(path, box):
    x, y, width, height = box
    temporary = path + ".crop.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vf", "crop=%d:%d:%d:%d" % (width, height, x, y),
                    temporary], check=True)
    replace_file(temporary, path)


def limit_size(path, limit=1_400_000):
    """A palette keeps a heavy PNG sharp at a third of its size."""
    if os.path.getsize(path) <= limit:
        return
    temporary = path + ".small.png"
    graph = "split[a][b];[a]palettegen=max_colors=256[p];[b][p]paletteuse=dither=sierra2_4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-filter_complex", graph, temporary], check=True)
    replace_file(temporary, path)


def shoot(name, box=None):
    """Screenshot of the whole window, cropped to box (x, y, width, height from the top left)."""
    path = os.path.join(IMAGES, name + ".png")
    redraw_now()
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.screenshot(filepath=path)
    if box:
        crop_image(path, [int(v) for v in box])
    limit_size(path)
    log("shot " + name)


def shoot_frame(name, *boxes):
    """Screenshot of the pieces of the window named by boxes, side by side."""
    path = os.path.join(IMAGES, name + ".png")
    redraw_now()
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.screenshot(filepath=path)
    if len(boxes) == 1:
        crop_image(path, [int(v) for v in boxes[0]])
    else:
        split = "split=%d%s" % (len(boxes), "".join("[s%d]" % i for i in range(len(boxes))))
        crops = ";".join("[s%d]crop=%d:%d:%d:%d[c%d]" % (i, b[2], b[3], b[0], b[1], i) for i, b in enumerate(boxes))
        stack = "".join("[c%d]" % i for i in range(len(boxes))) + "hstack=inputs=%d" % len(boxes)
        temporary = path + ".pieces.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-filter_complex",
                        "%s;%s;%s" % (split, crops, stack), temporary], check=True)
        replace_file(temporary, path)
    return path


def caption(path, text):
    """Burn a short caption into the lower left corner of an image."""
    temporary = path + ".caption.png"
    folder, font = os.path.split(CAPTION_FONT)
    graph = ("drawtext=fontfile=%s:text='%s':fontsize=32:fontcolor=white:x=22:y=h-66"
             ":box=1:boxcolor=0x14161ACC:boxborderw=12" % (font, text))
    # The font is named from its own folder: a drive colon would end the filter's option.
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vf", graph, temporary],
                   check=True, cwd=folder)
    replace_file(temporary, path)


def montage(name, paths, gap=8):
    """Images of one height side by side, on a light strip; the sources are removed."""
    target = os.path.join(IMAGES, name + ".png")
    inputs = [argument for path in paths for argument in ("-i", path)]
    padded = ";".join("[%d]pad=iw+%d:ih:0:0:color=0xE8EAEE[p%d]" % (i, gap if i < len(paths) - 1 else 0, i)
                      for i in range(len(paths)))
    stack = "".join("[p%d]" % i for i in range(len(paths))) + "hstack=inputs=%d" % len(paths)
    temporary = target + ".strip.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", padded + ";" + stack, temporary],
                   check=True)
    for path in paths:
        if os.path.abspath(path) != os.path.abspath(target):
            os.remove(path)
    replace_file(temporary, target)
    limit_size(target)
    log("montage " + name)


def region_box(area_kind, region_kind=None, height=None):
    """The on-screen box of an area or one of its regions, optionally only its top part."""
    area = find_area(area_kind)
    item = find_region(area, region_kind) if region_kind else area
    full = item.height if height is None else min(height, item.height)
    top = window().height - (item.y + item.height)
    return item.x, top, item.width, full


def workspace_box():
    """Viewport, sidebar and Properties, without the top bar, the timeline and the status bar."""
    view = find_area("VIEW_3D")
    return 0, window().height - (view.y + view.height), window().width, view.height


def no_status_bar_box():
    return 0, 0, window().width, window().height - STATUS_BAR


def centered_view_box(width, height=None):
    """A piece of the viewport, without the header, around its center."""
    x, top, full_width, full_height = region_box("VIEW_3D", "WINDOW")
    height = height or full_height
    return x + (full_width - width) // 2, top + (full_height - height) // 2, width, height


def sidebar_box():
    """The sidebar, without the column of its tabs."""
    x, top, width, height = region_box("VIEW_3D", "UI")
    return x, top, width - 30, height


def redraw_now():
    """Draw the window now: a Blender started with --enable-event-simulate draws only when asked,
    and what a click lands on must be drawn first."""
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def window_pixels():
    """The window as it is drawn now: RGB bytes, rows from the bottom up as in event coordinates."""
    path = os.path.join(WORK, "probe.png")
    redraw_now()
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.screenshot(filepath=path)
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255.0 + 0.5).astype(numpy.int16)


def to_box(x, y, width, height):
    """A box (x, y from the top left, width, height) from a rectangle in event coordinates."""
    return x, window().height - (y + height), width, height


# --- scene helpers --------------------------------------------------------------------


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)


def select_only(obj):
    for other in bpy.context.view_layer.objects:
        other.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def show_only(*names):
    """Hide everything but the named objects (a trailing * matches a prefix), lights and cameras aside."""
    for obj in bpy.context.view_layer.objects:
        shown = obj.type in {"LIGHT", "CAMERA"} or any(
            obj.name.startswith(n[:-1]) if n.endswith("*") else obj.name == n for n in names)
        obj.hide_set(not shown)
        obj.hide_render = not shown


def look(center, distance, azimuth=30.0, elevation=20.0, lens=70.0):
    """Frame the viewport on a point, from an orbit around it (angles in degrees)."""
    space = view_space()
    view = space.region_3d
    space.lens = lens
    view.view_perspective = "PERSP"
    view.view_location = Vector(center)
    view.view_rotation = Euler((math.radians(90.0 - elevation), 0.0, math.radians(azimuth)), "XYZ").to_quaternion()
    view.view_distance = distance


def tool(node_type, label):
    """The definition of a Houdini node or an HDA with one input."""
    return {"type": node_type, "label": label, "max_inputs": 1}


def add_houdini(obj, path, definition, parms=None, others=()):
    """Add a Houdini modifier, wait for its first cook, then set its parameters.
    The objects in others are selected too: they fill the next inputs."""
    select_only(obj)
    for other in others:
        other.select_set(True)
    modifier = api().place_hda(bpy.context, path, definition, "MODIFIER")
    tree = modifier.node_group
    yield from settle(0.3)
    start = time.perf_counter()
    while not tree.houdini_bridge.parms:
        if time.perf_counter() - start > 60.0:
            raise RuntimeError("%s did not cook: %s" % (definition["type"], tree.houdini_bridge.errors))
        yield 0.1
    for name, value in (parms or {}).items():
        api().set_parameter(modifier, name, value)
    yield from settle(0.3)
    return modifier


def wait_for_shading(seconds=12.0):
    """Material Preview compiles shaders and accumulates samples."""
    start = time.perf_counter()
    while time.perf_counter() - start < seconds:
        redraw()
        yield 0.1


# --- the session ----------------------------------------------------------------------


def prepare_work_folder():
    os.makedirs(os.path.dirname(DEMO))
    shutil.copy2(os.path.join(ROOT, "demos", "visualization_demo.blend"), DEMO)
    shutil.copytree(os.path.join(ROOT, "tests", "hda"), os.path.dirname(HDA))


def prepare_preferences():
    preferences = bpy.context.preferences
    preferences.use_preferences_save = False
    preferences.filepaths.use_scripts_auto_execute = True  # the demo file opens and cooks, as a trusted one
    preferences.view.ui_scale = UI_SCALE
    preferences.view.show_splash = False
    preferences.view.show_tooltips = False
    preferences.view.show_navigate_ui = False
    preferences.system.use_region_overlap = False


def enable_extension():
    addon_utils.enable(MODULE, default_set=True)
    # Favorites, catalog and icons go to the temporary folder, not the user's.
    user_dir = os.path.join(WORK, "user")
    os.makedirs(os.path.join(user_dir, "status_icons"))
    os.makedirs(os.path.join(user_dir, "icons"))
    extension("paths").user_dir = lambda: user_dir
    extension("catalog").load()
    # The asset shelf shows Houdini's and Labs' tools only: not the test HDAs, nor the
    # other packages installed on this machine.
    build_library = extension("assets").build_library
    extension("assets").build_library = lambda tools: build_library(
        [tool for tool in tools if tool["group"].split("/")[0] in ASSET_SHELF_ROOTS])


def prepare_window():
    """Tidy viewport, Houdini tab open in the sidebar."""
    yield 1.0
    area = find_area("VIEW_3D")
    space = area.spaces.active
    space.show_region_ui = True
    space.show_region_toolbar = False
    space.show_gizmo = False
    overlay = space.overlay
    overlay.show_floor = False
    overlay.show_axis_x = False
    overlay.show_axis_y = False
    overlay.show_text = False
    overlay.show_cursor = False
    overlay.show_object_origins = False
    overlay.show_relationship_lines = False
    overlay.show_extras = False
    find_area("PROPERTIES").spaces.active.context = "MODIFIER"
    yield 0.5
    yield from widen_right_column()
    yield from widen_sidebar()
    yield from open_sidebar_tab("Houdini")


def widen_right_column():
    """More room for the Properties editor: a wider column, and a short Outliner."""
    properties = find_area("PROPERTIES")
    with bpy.context.temp_override(window=window(), area=properties):
        bpy.ops.screen.area_move(x=properties.x, y=properties.y + properties.height // 2, delta=-RIGHT_COLUMN_EXTRA)
    yield 0.3
    properties = find_area("PROPERTIES")
    outliner = find_area("OUTLINER")
    with bpy.context.temp_override(window=window(), area=properties):
        bpy.ops.screen.area_move(x=properties.x + 40, y=properties.y + properties.height + 1,
                                 delta=outliner.height - 130)
    yield 0.3


def drag_edge(edge, y, target):
    """Drag a vertical edge of the interface with simulated events."""
    for x in (edge + 12, edge + 3, edge + 1):
        mouse_to(x, y)
        yield 0.2
    event("LEFTMOUSE", "PRESS")
    yield 0.2
    for step in range(1, 7):
        mouse_to(edge + 1 + (target - edge - 1) * step // 6, y)
        yield 0.15
    yield 0.3
    mouse_to(target, y)
    yield 0.3
    event("LEFTMOUSE", "RELEASE")
    yield 0.4


def widen_sidebar():
    """Its width is read-only from Python, and names and status lines are cut off at
    the default one."""
    sidebar = find_region(find_area("VIEW_3D"), "UI")
    yield from drag_edge(sidebar.x, sidebar.y + sidebar.height // 2, sidebar.x - (SIDEBAR_WIDTH - sidebar.width))
    log("sidebar width %d" % find_region(find_area("VIEW_3D"), "UI").width)


def resize_right_column(delta):
    """Move the edge between the viewport and the Properties editor."""
    properties = find_area("PROPERTIES")
    yield from drag_edge(properties.x - 1, properties.y + properties.height // 2, properties.x - 1 + delta)


_mouse = [0, 0]


def event(kind, value, **modifiers):
    """A simulated event, at the last mouse position (its default is the window corner)."""
    window().event_simulate(type=kind, value=value, x=_mouse[0], y=_mouse[1], **modifiers)


def mouse_to(x, y):
    _mouse[:] = int(x), int(y)
    event("MOUSEMOVE", "NOTHING")


def click(x, y, **modifiers):
    redraw_now()
    mouse_to(x, y)
    yield 0.15
    redraw_now()  # what is under the mouse is drawn before the press
    event("LEFTMOUSE", "PRESS", **modifiers)
    event("LEFTMOUSE", "RELEASE", **modifiers)
    yield 0.25


def open_sidebar_tab(name):
    """The active tab is read-only from Python: click down the tab column until it is reached."""
    sidebar = find_region(find_area("VIEW_3D"), "UI")
    x = sidebar.x + sidebar.width - 10 * UI_SCALE
    top = sidebar.y + sidebar.height
    for offset in range(20, 500, 22):
        if sidebar.active_panel_category == name:
            return
        yield from click(x, top - offset)
    log("sidebar tab %s not reached, on %s" % (name, sidebar.active_panel_category))


def press(key, **modifiers):
    event(key, "PRESS", **modifiers)
    event(key, "RELEASE", **modifiers)


def type_text(text):
    """Letters typed on the keyboard, into the field or the search that has the focus."""
    for character in text:
        kind = character.upper() if character.isalpha() else "SPACE"
        event(kind, "PRESS", unicode=character)
        event(kind, "RELEASE")


def park_mouse():
    """The mouse in the corner of the status bar, so that no button is drawn hovered."""
    mouse_to(window().width - 4, 4)


def ui_scale():
    return bpy.context.preferences.system.ui_scale


# --- the Houdini sidebar and the modifier panel ---------------------------------------


# The state dots of core/icons.py COLORS, as bytes: up to date, bypassed, in error, cooking.
STATE_DOTS = ((77, 204, 102), (255, 209, 26), (242, 69, 56), (255, 115, 26))
NODE_ROW_ARROW = 32  # from the left of the Properties editor to the arrow of a node row, at a UI scale of 1
FLAG_STEP = 20  # from one icon of the node row to the next, at a UI scale of 1
FLAG_OFFSETS = {"display": 1, "template": 2, "lock": 3, "bypass": 4}  # icons left of the state dot


def show_tabs(*keys):
    """The tabs of the Houdini sidebar to show: the set a click, or a Shift+click, leaves."""
    bpy.context.window_manager.houdini_bridge_tabs = set(keys)
    redraw()


def node_rows():
    """The node rows of the Houdini modifiers the Properties editor shows, top down: the window
    position (event coordinates) of the dot of each one's state, the rightmost dot of its row."""
    region = find_region(find_area("PROPERTIES"), "WINDOW")
    pixels = window_pixels()[region.y:region.y + region.height, region.x:region.x + region.width]
    mask = numpy.zeros(pixels.shape[:2], dtype=bool)
    for color in STATE_DOTS:
        mask |= numpy.abs(pixels - numpy.array(color)).max(axis=2) <= 6
    found = numpy.unique(numpy.nonzero(mask)[0])
    rows = []
    for band in numpy.split(found, numpy.nonzero(numpy.diff(found) > 3)[0] + 1) if len(found) else []:
        ys, xs = numpy.nonzero(mask[band[0]:band[-1] + 1])
        dot = xs > xs.max() - 12 * ui_scale()
        if dot.sum() >= 6:
            rows.append((region.x + int(xs[dot].mean()), region.y + int(band[0] + ys[dot].mean())))
    return sorted(rows, key=lambda row: -row[1])


def open_node_panels(first=0, last=None):
    """Unfold the Houdini panels of the node rows the Properties editor shows (rows first to
    last, top down). Blender folds that panel on each new modifier, and Python cannot unfold it
    (the state is a bit of the modifier's ui_expand_flag): it is clicked, as a user does. The
    lowest goes first, so the rows above stay where they are."""
    rows = node_rows()[first:last]
    region = find_region(find_area("PROPERTIES"), "WINDOW")
    band = int(40 * ui_scale())
    for _, y in reversed(rows):
        def below(row=y):
            bottom = max(region.y, row - band)
            return window_pixels()[bottom:row - int(12 * ui_scale()), region.x:region.x + region.width]
        before = below()
        yield from click(region.x + NODE_ROW_ARROW * ui_scale(), y)
        park_mouse()
        yield 0.3
        changed = numpy.abs(below() - before).max() > 30
        log("node row at %d %s" % (y, "unfolded" if changed else "NOT unfolded"))
    yield 0.3


REGION_BACKGROUND = (48, 48, 48)  # the Properties editor of the default theme, behind its panels
SECTION_ARROW = 40  # from the left of the Properties editor to the arrow of Output and Cache, at a UI scale of 1
SECTION_CACHE = 14  # from the bottom of the last modifier up to its Cache row, at a UI scale of 1
SECTION_STEP = 20  # from the Cache row up to the Output row


def open_section(name):
    """Unfold the Output or the Cache section of the lowest Houdini modifier the Properties editor
    shows (its panel unfolded), with a click on its row, the last rows of the panel."""
    region = find_region(find_area("PROPERTIES"), "WINDOW")
    column = window_pixels()[region.y:region.y + region.height, region.x + int(12 * ui_scale())]
    empty = numpy.abs(column - numpy.array(REGION_BACKGROUND)).max(axis=1) <= 3
    bottom = 0
    while bottom < 6 and not empty[bottom]:  # the edge of the area
        bottom += 1
    while bottom < len(empty) and empty[bottom]:  # the room below the last modifier
        bottom += 1
    if bottom == len(empty):
        log("open_section: no panel found")
        return
    up = SECTION_CACHE + (SECTION_STEP if name == "Output" else 0)
    yield from click(region.x + SECTION_ARROW * ui_scale(), region.y + bottom + up * ui_scale())
    park_mouse()
    yield 0.4


def click_flag(row, flag):
    """Click a flag of a node row (a position node_rows gave), as a user does."""
    x, y = row
    yield from click(x - FLAG_OFFSETS[flag] * FLAG_STEP * ui_scale(), y)
    park_mouse()
    yield 0.3


def prepare_shelf():
    """The test HDAs, Houdini's nodes and Labs in the shelf, read once Houdini runs; the tools of the
    other packages of this machine hidden (the preference Hide), so the pictures show those alone."""
    catalog = extension("catalog")
    preferences = bpy.context.preferences.addons[MODULE].preferences
    preferences.folders.clear()
    preferences.folders.add().path = os.path.dirname(HDA)
    preferences.include_packages = True
    preferences.refresh_on_start = False
    preferences.folders_open = True  # Open Folders: Houdini's collapsible folders start open in the panel
    api().start_server()
    while catalog.state.refreshing:
        yield 0.2
    catalog.refresh()
    yield 0.5
    while catalog.state.refreshing:
        yield 0.2
    test_folder = os.path.normcase(os.path.dirname(HDA))
    others = sorted({tool["type"] for tool in catalog.state.tools
                     if tool["group"].split("/")[0] not in ASSET_SHELF_ROOTS
                     and not os.path.normcase(os.path.normpath(tool.get("file", ""))).startswith(test_folder)})
    preferences.hidden_tools = ", ".join(others)
    log("shelf: %d tools, %d of other packages hidden, test HDAs %s" % (
        len(catalog.state.tools), len(others), sorted(t["type"] for t in catalog.state.tools if t["group"] == "hda")))
    for prefix in FAVORITES:
        tool = next((t for t in catalog.state.tools if t["type"].startswith(prefix)), None)
        if tool is not None:
            catalog.toggle_favorite(catalog.tool_key(tool))
    yield 0.5


# --- look -----------------------------------------------------------------------------


def stock_color_management():
    scene = bpy.context.scene
    scene.eevee.taa_samples = 64
    scene.display_settings.display_device = "sRGB"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"


def add_light(name, kind, energy, rotation, color=(1.0, 1.0, 1.0)):
    light = bpy.data.lights.get(name) or bpy.data.lights.new(name, kind)
    light.energy = energy
    light.color = color
    obj = bpy.data.objects.get(name) or bpy.data.objects.new(name, light)
    if obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(obj)
    obj.rotation_euler = [math.radians(angle) for angle in rotation]
    return obj


def world_color(color, strength=1.0):
    scene = bpy.context.scene
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    for link in list(background.inputs["Color"].links):
        world.node_tree.links.remove(link)  # the sky of sky_world
    background.inputs["Color"].default_value = (*color, 1.0)
    background.inputs["Strength"].default_value = strength


def studio():
    """A dark backdrop with a warm key and a cool rim."""
    stock_color_management()
    world_color((0.017, 0.019, 0.024), 1.0)
    for obj in list(bpy.data.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj)
    add_light("Key", "SUN", 3.2, (55, 0, 35), (1.0, 0.93, 0.85)).data.angle = math.radians(12)
    add_light("Rim", "SUN", 1.6, (70, 0, 215), (0.65, 0.78, 1.0)).data.angle = math.radians(20)


def sky_world(palette=DAY, strength=1.0):
    """A soft vertical gradient: it lights the scene and shows above the backdrop."""
    scene = bpy.context.scene
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    tree = world.node_tree
    for node in [n for n in tree.nodes if n.bl_idname not in {"ShaderNodeBackground", "ShaderNodeOutputWorld"}]:
        tree.nodes.remove(node)
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    elements = ramp.color_ramp.elements
    elements.new(0.5)
    for element, (position, color) in zip(elements, ((0.0, palette["ground"]), (0.5, palette["horizon"]),
                                                     (1.0, palette["zenith"])), strict=False):
        element.position = position
        element.color = (*color, 1.0)
    scale = tree.nodes.new("ShaderNodeMapRange")
    scale.inputs["From Min"].default_value = -1.0
    tree.links.new(coordinates.outputs["Generated"], separate.inputs[0])
    tree.links.new(separate.outputs["Z"], scale.inputs["Value"])
    tree.links.new(scale.outputs[0], ramp.inputs["Fac"])
    background = tree.nodes["Background"]
    tree.links.new(ramp.outputs["Color"], background.inputs["Color"])
    background.inputs["Strength"].default_value = strength


def daylight(palette=DAY, key=3.4, key_rotation=(50, 0, 55), strength=0.65):
    """The look of every bright shot: a sky, and one soft warm sun."""
    stock_color_management()
    sky_world(palette, strength)
    for obj in list(bpy.data.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj)
    bpy.context.view_layer.update()
    add_light("Key", "SUN", key, key_rotation, (1.0, 0.94, 0.86)).data.angle = math.radians(14)


def backdrop_material(palette, height):
    """Ground color at the bottom, sky color up the wall, by height. Diffuse only: it takes shadows."""
    name = "doc_backdrop"
    material = bpy.data.materials.get(name)
    if material is not None:
        bpy.data.materials.remove(material)
    material = principled(name, palette["ground"], 1.0)
    tree = material.node_tree
    shader = tree.nodes["Principled BSDF"]
    shader.inputs["Specular IOR Level"].default_value = 0.0
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    scale = tree.nodes.new("ShaderNodeMath")
    scale.operation = "DIVIDE"
    scale.inputs[1].default_value = height
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*palette["ground"], 1.0)
    ramp.color_ramp.elements[1].color = (*palette["wall"], 1.0)
    tree.links.new(coordinates.outputs["Object"], separate.inputs[0])
    tree.links.new(separate.outputs["Z"], scale.inputs[0])
    tree.links.new(scale.outputs[0], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
    return material


def add_backdrop(center=(0.0, 0.0), z=0.0, radius=40.0, palette=DAY):
    """A seamless bowl, a floor that curves up into a wall: there is no horizon line from
    any side, and it works in the viewport as well as in Cycles."""
    rings = [(radius * 0.7 * i / 12, 0.0) for i in range(1, 13)]
    curve = radius * 0.3
    rings += [(radius * 0.7 + curve * math.sin(a * math.pi / 32), curve * (1.0 - math.cos(a * math.pi / 32)))
              for a in range(1, 17)]
    segments = 96
    angles = [2.0 * math.pi * s / segments for s in range(segments)]
    vertices = [(0.0, 0.0, 0.0)] + [(r * math.cos(a), r * math.sin(a), h) for r, h in rings for a in angles]
    faces = [(0, 1 + s, 1 + (s + 1) % segments) for s in range(segments)]
    for ring in range(len(rings) - 1):
        low, high = 1 + ring * segments, 1 + (ring + 1) * segments
        faces += [(low + s, high + s, high + (s + 1) % segments, low + (s + 1) % segments) for s in range(segments)]
    mesh = bpy.data.meshes.new("Backdrop")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    mesh.materials.append(backdrop_material(palette, curve * 0.7))
    obj = bpy.data.objects.new("Backdrop", mesh)
    obj.location = (center[0], center[1], z)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def bright_stage(radius=40.0, palette=DAY, z=-1.15):
    """An empty scene with the sky and the backdrop."""
    yield from stage()
    daylight(palette)
    add_backdrop(z=z, radius=radius, palette=palette)


def soft_viewport(top=(0.52, 0.58, 0.66), bottom=(0.74, 0.76, 0.78)):
    """A light gradient behind solid shading, from the theme: the preferences are not saved."""
    theme = bpy.context.preferences.themes[0].view_3d.space
    theme.gradients.background_type = "LINEAR"
    theme.gradients.gradient = bottom
    theme.gradients.high_gradient = top
    view_space().shading.background_type = "THEME"


def principled(name, color, roughness=0.6):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if material.node_tree is None:
        material.use_nodes = True
    shader = material.node_tree.nodes["Principled BSDF"]
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    return material


def rock_material():
    """Stone from noise: mottled color, and a fine bump the eye reads as grain."""
    name = "doc_rock"
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    material = principled(name, (0.3, 0.27, 0.24), 0.72)
    nodes, links = material.node_tree.nodes, material.node_tree.links
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 30.0
    noise.inputs["Detail"].default_value = 10.0
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[0].color = (0.2, 0.17, 0.15, 1.0)
    ramp.color_ramp.elements[1].position = 0.7
    ramp.color_ramp.elements[1].color = (0.42, 0.37, 0.32, 1.0)
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.5
    links.new(noise.outputs[0], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    links.new(noise.outputs[0], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], nodes["Principled BSDF"].inputs["Normal"])
    return material


def clay_material():
    material = principled("doc_clay", (0.42, 0.20, 0.11), 0.5)
    material.diffuse_color = (0.62, 0.40, 0.30, 1.0)  # solid shading
    return material


def color_attribute_material():
    name = "doc_cd"
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    material = principled(name, (0.8, 0.8, 0.8), 0.45)
    nodes = material.node_tree.nodes
    attribute = nodes.new("ShaderNodeAttribute")
    attribute.attribute_name = "Cd"
    material.node_tree.links.new(attribute.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    return material


def set_material(modifier, material):
    """Give the object carrying the modifier one material, as a user does: a result that
    names no material takes the object's own. (An HDA would set shop_materialpath.)"""
    slots = modifier.id_data.data.materials
    slots.clear()
    slots.append(material)


def active_only(obj):
    """Active, so the panels show it, but with no selection outline."""
    select_only(obj)
    obj.select_set(False)


def add_floor(size=400.0, z=-1.1):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, z))
    floor = bpy.context.active_object
    floor.name = "Floor"
    material = principled("doc_floor", (0.012, 0.013, 0.016), 1.0)
    material.node_tree.nodes["Principled BSDF"].inputs["Specular IOR Level"].default_value = 0.0
    floor.data.materials.append(material)
    return floor


def dark_backdrop(shade):
    shade.background_type = "VIEWPORT"
    shade.background_color = (0.009, 0.01, 0.012)


def shading(kind="MATERIAL"):
    shade = view_space().shading
    if kind == "MATCAP":
        shade.type = "SOLID"
        shade.light = "MATCAP"
        shade.studio_light = "basic_grey.exr"
        shade.color_type = "MATERIAL"
        dark_backdrop(shade)
    elif kind == "ATTRIBUTE":
        shade.type = "SOLID"
        shade.light = "STUDIO"
        shade.color_type = "VERTEX"
        dark_backdrop(shade)
    else:
        shade.type = kind
        shade.use_scene_lights = True
        shade.use_scene_world = True
        if kind == "RENDERED":
            shade.use_scene_lights_render = True
            shade.use_scene_world_render = True


def deselect():
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)


def viewport_override():
    area = find_area("VIEW_3D")
    return bpy.context.temp_override(window=window(), area=area, region=find_region(area, "WINDOW"))


def set_mode(mode):
    with viewport_override():
        bpy.ops.object.mode_set(mode=mode)


def set_value(modifier, name, value):
    api().set_parameter(modifier, name, value)


def scroll_properties(steps, down=True):
    """Turn the mouse wheel over the Properties editor, then leave it by an empty corner, so that
    no button stays drawn hovered."""
    region = find_region(find_area("PROPERTIES"), "WINDOW")
    mouse_to(region.x + region.width // 2, region.y + region.height // 2)
    for _ in range(steps):
        press("WHEELDOWNMOUSE" if down else "WHEELUPMOUSE")
        yield 0.05
    mouse_to(region.x + 2, region.y + 2)
    yield 0.2
    park_mouse()
    yield 0.2


def properties_shot(name, scroll=0, top=0):
    """The Properties editor at 100% UI scale: long lists fit, and stay sharp. `scroll` turns the
    mouse wheel down that many steps over it first, and back up after; `top` crops that many
    pixels off the top."""
    preferences = bpy.context.preferences
    preferences.view.ui_scale = 1.0
    yield from wait_for_shading(1.0)
    yield from scroll_properties(scroll)
    yield from wait_for_shading(0.5)
    x, y, width, height = region_box("PROPERTIES")
    shoot(name, (x, y + top, width, height - top))
    yield from scroll_properties(scroll, down=False)
    preferences.view.ui_scale = UI_SCALE
    yield from wait_for_shading(0.5)


def stage():
    """An empty lit scene."""
    clear_scene()
    studio()
    yield 1.3  # the engine's upkeep releases the modifiers just removed
    for tree in list(bpy.data.node_groups):
        if tree.users == 0:
            bpy.data.node_groups.remove(tree)


def make_rock(subdivisions=6):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=1.0, location=(0, 0, 0))
    rock = bpy.context.active_object
    rock.name = "Rock"
    with viewport_override():
        bpy.ops.object.shade_smooth()
    return rock


ROCK_PARMS = {"height": [0.7], "elementsize": [1.1]}


# --- demo file ------------------------------------------------------------------------

_demo = {"open": False}


def open_demo():
    """The temporary copy, once: each shot then shows part of it."""
    if _demo["open"]:
        return
    bpy.ops.wm.open_mainfile(filepath=DEMO, load_ui=False)
    _demo["open"] = True
    stock_color_management()
    yield from settle(1.5, 180.0)
    log("demo open: %d objects" % len(bpy.data.objects))


def show_demo(*names):
    show_only(*names)
    deselect()


# --- 1. hero --------------------------------------------------------------------------


def ensure_cycles():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.use_denoising = True
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "OPTIX"
        preferences.get_devices()
        for device in preferences.devices:
            device.use = device.type == "OPTIX"
        scene.cycles.device = "GPU"
    except (KeyError, TypeError):
        scene.cycles.device = "CPU"


def render_camera(location, target, lens=50.0):
    camera = bpy.data.objects.get("Doc Camera")
    if camera is None:
        camera = bpy.data.objects.new("Doc Camera", bpy.data.cameras.new("Doc Camera"))
        bpy.context.scene.collection.objects.link(camera)
    camera.location = location
    direction = Vector(target) - Vector(location)
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    camera.data.lens = lens
    bpy.context.scene.camera = camera
    return camera


def render(name, samples=128, size=(1280, 720), denoise=True):
    scene = bpy.context.scene
    ensure_cycles()
    scene.cycles.use_denoising = denoise
    scene.cycles.samples = samples
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    # Blender writes these into the PNG as text, the hostname and the .blend path among them.
    for flag in scene.render.bl_rna.properties.keys():
        if flag.startswith("use_stamp"):
            setattr(scene.render, flag, False)
    scene.render.filepath = os.path.join(IMAGES, name + ".png")
    bpy.ops.render.render(write_still=True)
    limit_size(scene.render.filepath)
    log("render " + name)


# The parameters of the terrain, shared by everything that sits on it.
HILLS = {"height": [1.1], "elementsize": [5.0], "offset": [1.0, 0.0, 3.0]}


def hill(name, resolution=200):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=resolution, y_subdivisions=resolution, size=16.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    return obj


def build_meadow():
    """Everything here is a Houdini modifier: terrain, grass, stones, fireflies, fragments."""
    yield from stage()
    for obj in list(bpy.data.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj)
    world_color((0.018, 0.028, 0.055), 1.0)
    add_light("Dusk", "SUN", 5.0, (78, 0, 250), (1.0, 0.56, 0.3)).data.angle = math.radians(4)
    add_light("Sky", "SUN", 0.5, (30, 0, 60), (0.5, 0.65, 1.0))

    mountain = tool("mountain::2.0", "Mountain")
    terrain = hill("Terrain")
    with viewport_override():
        bpy.ops.object.shade_smooth()
    modifier = yield from add_houdini(terrain, "", mountain, HILLS)
    set_material(modifier, principled("doc_soil", (0.05, 0.04, 0.025), 0.9))

    lawn = hill("Grass")
    yield from add_houdini(lawn, "", mountain, HILLS)
    yield from add_houdini(lawn, HDA, tool("bridge::grass", "Bridge Grass"), {
        "count": [200000], "height": [0.42], "bend": [0.45], "width": [0.016], "segments": [6],
        "root_color": [0.015, 0.05, 0.01], "tip_color": [0.3, 0.42, 0.07]})

    stones = hill("Stones", 60)
    yield from add_houdini(stones, "", mountain, HILLS)
    yield from add_houdini(stones, HDA, tool("bridge::scatter", "Bridge Scatter"), {
        "count": [260], "size": [0.2], "size_variation": [0.9], "spheres": False,
        "color_a": [0.2, 0.18, 0.16], "color_b": [0.34, 0.31, 0.28]})

    bpy.ops.mesh.primitive_circle_add(vertices=48, radius=6.0, fill_type="NGON", location=(0, 0, 0.1))
    swarm = bpy.context.active_object
    swarm.name = "Fireflies"
    yield from add_houdini(swarm, HDA, tool("bridge::fireflies", "Bridge Fireflies"), {
        "count": [2500], "height": [2.6], "size": [0.011], "color": [1.0, 0.62, 0.16]})

    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.25, location=(0.6, 1.0, 2.75))
    moon = bpy.context.active_object
    moon.name = "Shattered Sphere"
    yield from add_houdini(moon, HDA, tool("bridge::shatter", "Bridge Shatter"), {
        "pieces": [150], "explode": [0.32], "seed": [7]})
    yield from settle(1.0)
    return moon


def shot_hero_viewport():
    """The meadow by day, the whole workspace: the sidebar on its Shelf and Modifiers tabs, the
    Properties editor on the grass, the panel of Bridge Grass unfolded. Fireflies draw as
    large squares near this camera, and the stones as pale shapes, so they stay hidden."""
    yield from open_demo()
    yield from build_meadow()
    daylight(key=3.2, key_rotation=(40, 0, 25))
    show_only("Terrain", "Grass", "Shattered Sphere")
    active_only(bpy.data.objects["Grass"])
    show_tabs("SHELF", "MODIFIERS")
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    shading("RENDERED")
    look((0.4, 0.4, 1.8), 5.6, -9, 5, 38.0)
    yield from wait_for_shading(2.0)
    yield from open_node_panels(first=1)
    yield from wait_for_shading(15.0)
    shoot("hero-blender", workspace_box())
    show_tabs("SHELF")


# --- 2-4. shelf, pie, sidebar, header -------------------------------------------------


def chord(key, *held):
    """A key with modifiers held, the way the keyboard sends it."""
    names = {"ctrl": "LEFT_CTRL", "shift": "LEFT_SHIFT", "alt": "LEFT_ALT"}
    flags = {}
    for name in held:
        flags[name] = True
        event(names[name], "PRESS", **flags)
    press(key, **flags)
    for name in reversed(held):
        del flags[name]
        event(names[name], "RELEASE", **flags)


def shot_shelf():
    yield from bright_stage()
    rock = make_rock()
    modifier = yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"), ROCK_PARMS)
    set_material(modifier, rock_material())
    select_only(rock)
    deselect()
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    shading("RENDERED")
    region = find_region(find_area("VIEW_3D"), "WINDOW")
    look((0, 0, 0), 6.0)
    yield from wait_for_shading(5.0)
    mouse_to(region.x + region.width / 2, region.y + region.height / 2)
    yield 0.4
    chord("H", "shift", "alt")
    yield from wait_for_shading(2.0)
    shoot("favorites-pie", region_box("VIEW_3D", "WINDOW"))
    press("ESC")
    yield 0.6


def shot_asset_shelf():
    """The popup at 100% UI scale, so it leaves the object in view."""
    yield from bright_stage()
    rock = make_rock()
    modifier = yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"), ROCK_PARMS)
    set_material(modifier, rock_material())
    select_only(rock)
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    shading("RENDERED")
    view_space().show_region_ui = False
    look((3.1, 0, 0.2), 8.0)
    bpy.context.preferences.view.ui_scale = 1.0
    yield from wait_for_shading(5.0)
    region = find_region(find_area("VIEW_3D"), "WINDOW")
    log("window %d x %d, view %d x %d" % (window().width, window().height, region.width, region.height))
    mouse_to(region.x + region.width * 0.56, region.y + region.height * 0.62)
    yield 0.4
    chord("H", "ctrl", "shift")
    yield from wait_for_shading(3.0)
    area_top = region_box("VIEW_3D")[1]
    yield from click(667, window().height - (area_top + ASSET_SHELF_CATEGORY))
    yield from wait_for_shading(10.0)  # previews load lazily
    shoot("asset-shelf", (0, 0, 1810, 640))
    press("ESC")
    yield 0.6
    bpy.context.preferences.view.ui_scale = UI_SCALE
    view_space().show_region_ui = True
    yield 0.6


def shot_sidebar():
    """The Shelf tab: the favorites, then a search."""
    yield from stage()
    make_rock(5)
    bpy.context.view_layer.objects.active = None
    deselect()
    show_tabs("SHELF")
    yield from wait_for_shading(0.6)
    x, top, width, _ = region_box("VIEW_3D", "UI")
    shelf_box = x, top, width - 30, SIDEBAR_SHELF_HEIGHT
    shoot("sidebar-shelf", shelf_box)
    bpy.context.window_manager.houdini_bridge_search = "scatter"
    yield from wait_for_shading(0.8)
    shoot("sidebar-search", shelf_box)
    bpy.context.window_manager.houdini_bridge_search = ""
    yield from wait_for_shading(0.3)


def shot_sidebar_tabs():
    """The three tabs of the sidebar on one object, side by side: Shelf, Modifiers (one HDA
    bypassed), Groups (the mesh's groups, and those of the active modifier's result)."""
    yield from stage()
    head = suzanne_with_groups()
    head.modifiers.new("Subdivision", "SUBSURF").levels = 1
    yield from add_houdini(head, "", tool("mountain::2.0", "Mountain"), {"height": [0.2], "elementsize": [0.6]})
    extrude = yield from add_houdini(head, "", tool("polyextrude::2.0", "PolyExtrude"),
                                     {"splittype": "elements", "dist": [0.05]})
    yield from add_houdini(head, "", tool("groupcreate", "Group Create"),
                           {"groupname": "top", "groupbase": False, "groupbounding": True,
                            "size": [3.0, 0.6, 3.0], "t": [0.0, 0.7, 0.0]})
    bpy.ops.houdini_bridge.bypass(tree=extrude.node_group.name)
    yield from settle(0.5)
    active_only(head)
    x, top, width, _ = region_box("VIEW_3D", "UI")
    box = x, top, width - 30, SIDEBAR_TABS_HEIGHT
    pieces = []
    for key in ("SHELF", "MODIFIERS", "GROUPS"):
        show_tabs(key)
        yield from wait_for_shading(0.8)
        pieces.append(shoot_frame("sidebar-tab-%s" % key.lower(), box))
    montage("sidebar-tabs", pieces, gap=12)
    show_tabs("SHELF")


def shot_header():
    header = find_region(find_area("VIEW_3D"), "HEADER")
    top = window().height - (header.y + header.height)
    yield 0.5
    shoot("header-connected", (0, top - 2, 760, header.height + 4))
    button_x = 490
    yield from click(button_x, header.y + header.height / 2)
    yield 1.0
    shoot("header-popover", (300, top - 2, 640, 220))
    press("ESC")
    yield 0.5


# --- 5-8. modifiers -------------------------------------------------------------------


def shot_parameters():
    yield from stage()
    rock = make_rock()
    add_floor()
    modifier = yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"),
                                      {"height": [0.25], "elementsize": [1.1]})
    set_material(modifier, rock_material())
    deselect()
    shading("MATERIAL")
    look((0, 0, -0.1), 6.5, 25, 26)
    yield from wait_for_shading()
    box = region_box("VIEW_3D", "WINDOW")
    shoot("parameter-before", box)
    set_value(modifier, "height", [1.0])
    yield from settle(0.5)
    set_material(modifier, rock_material())
    yield from wait_for_shading(6.0)
    shoot("parameter-after", box)


def shot_modifier_panel():
    """The whole Houdini modifier in its panel, as Houdini's parameter pane: a rock whose faces
    PolyExtrude pushes out as shards, its panel unfolded beside it (group field and arrow, tabs,
    boxes)."""
    yield from stage()
    add_floor()
    rock = make_rock(3)
    rock.name = "Crystal"
    # About one face in three, scattered, as a group string.
    shards = " ".join(str(face.index) for face in rock.data.polygons if (face.index * 7919) % 7 < 2)
    mountain = yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"),
                                      {"height": [0.3], "elementsize": [1.0]})
    extrude = yield from add_houdini(rock, "", tool("polyextrude::2.0", "PolyExtrude"), {
        "group": shards, "splittype": "elements", "dist": [0.22], "inset": [0.025]})
    active_only(rock)
    view_space().show_region_ui = False
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    shading("RENDERED")
    look((0, 0, 0.1), 6.2, 28, 27, 60.0)
    yield from wait_for_shading(2.0)
    yield from open_node_panels(first=1)
    yield from settle(0.5)
    for modifier in (mountain, extrude):
        set_material(modifier, clay_material())
    yield from wait_for_shading(8.0)
    shoot("modifier-panel", view_and_properties_box(MODIFIER_PANEL_LEFT_CROP))
    yield from properties_shot("modifier-parameters")
    view_space().show_region_ui = True


def shot_multiparm():
    """Bridge Ramps on a plate: two bumps (a multiparm), the shape of a bump as a float ramp and
    the color as a color ramp, all in the panel; then a nested multiparm, rings in a bump. At a UI
    scale of 1, so that both ramps fit."""
    yield from stage()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=120, y_subdivisions=120, size=4, location=(0, 0, 0))
    plate = bpy.context.active_object
    plate.name = "Plate"
    modifier = yield from add_houdini(plate, HDA, tool("bridge::ramps", "Bridge Ramps"), {"bumps": [2]})
    yield from settle(0.5)
    set_value(modifier, "center1", [0.0, 0.0, 0.4])
    set_value(modifier, "center2", [0.9, 0.0, 0.0])
    set_value(modifier, "bump_height2", [0.9])
    yield from settle(0.5)
    active_only(plate)
    view_space().show_region_ui = False
    shading("MATERIAL")
    look((0, 0, 0.2), 8.0, 25, 28)
    yield from wait_for_shading(2.0)
    yield from open_node_panels()
    bpy.context.preferences.view.ui_scale = 1.0
    set_material(modifier, color_attribute_material())
    yield from wait_for_shading(1.0)
    yield from scroll_properties(RAMPS_SCROLL)
    yield from wait_for_shading()
    shoot("ramps-window", view_and_properties_box(RAMPS_LEFT_CROP))
    yield from scroll_properties(RAMPS_SCROLL, down=False)
    bpy.context.preferences.view.ui_scale = UI_SCALE
    set_value(modifier, "rings1", [2])
    yield from settle(0.8)
    yield from properties_shot("modifier-multiparm", scroll=MULTIPARM_SCROLL, top=MULTIPARM_TOP)
    view_space().show_region_ui = True


def stack_stage(cells, head, text, flat=False):
    """One cell of the strip: the object as this modifier leaves it."""
    for modifier in head.modifiers:
        if modifier.type == "NODES":
            set_material(modifier, clay_material())
            if flat:
                modifier.node_group.houdini_bridge.holder.data.shade_flat()
    yield from wait_for_shading(7.0)
    path = shoot_frame("stack-%d" % len(cells), centered_view_box(STACK_CELL, STACK_CELL_HEIGHT))
    caption(path, text)
    cells.append(path)


def shot_stack():
    """Blender and Houdini modifiers taking turns, each one with a visible effect."""
    yield from bright_stage(z=-1.25)
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    head = bpy.context.active_object
    head.name = "Suzanne"
    with viewport_override():
        bpy.ops.object.shade_smooth()
    head.data.materials.append(clay_material())
    view_space().show_region_ui = False
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    shading("RENDERED")
    look((0.1, 0, -0.05), 8.6, 22, 12, 60.0)
    deselect()
    cells = []
    head.modifiers.new("Subdivision", "SUBSURF").levels = 3
    yield from stack_stage(cells, head, "1  Subdivision · Blender")
    yield from add_houdini(head, "", tool("mountain::2.0", "Mountain"), {"height": [0.38], "elementsize": [0.42]})
    deselect()
    yield from stack_stage(cells, head, "2  Mountain · Houdini")
    with viewport_override():
        bpy.ops.object.shade_flat()
    head.modifiers.new("Decimate", "DECIMATE").ratio = 0.012
    yield from settle(0.5)
    yield from stack_stage(cells, head, "3  Decimate · Blender", flat=True)
    extrude = yield from add_houdini(head, "", tool("polyextrude::2.0", "PolyExtrude"), {
        "splittype": "elements", "dist": [0.07], "inset": [0.035]})
    log("polyextrude: %r %s" % (extrude.node_group.houdini_bridge.errors, extrude.node_group.houdini_bridge.status))
    deselect()
    yield from stack_stage(cells, head, "4  PolyExtrude · Houdini", flat=True)
    montage("stack-window", cells)
    view_space().show_region_ui = True


def shot_cooking():
    yield from stage()
    rock = make_rock(7)
    modifier = yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"), {"height": [0.5]})
    set_material(modifier, rock_material())
    add_floor()
    deselect()
    shading("MATERIAL")
    look((0, 0, -0.1), 7.0, 25, 26)
    settings = modifier.node_group.houdini_bridge
    # A heavy Remesh below the rock gives a cook long enough to be seen.
    remesh = yield from add_houdini(rock, "", tool("remesh::2.0", "Remesh"), {"targetsize": [0.03]})
    set_material(remesh, rock_material())
    deselect()
    yield from wait_for_shading(6.0)
    set_value(remesh, "targetsize", [0.012])
    start = time.perf_counter()
    while time.perf_counter() - start < 30.0:
        cooking, _ = extension("cook").activity()
        if cooking and max(seconds for _, seconds, _ in cooking) > 0.7:
            break
        yield 0.02
    redraw()
    yield 0.08
    shoot("cooking-feedback", region_box("VIEW_3D", "WINDOW"))
    yield from settle(0.5, 60.0)
    log("cooking: %s %s" % (settings.status, settings.timings))


# --- 9-10. groups and visualizers -----------------------------------------------------


def add_face_group(obj, name, color, keep):
    mesh = obj.data
    attribute = mesh.attributes.new(name, "BOOLEAN", "FACE")
    attribute.data.foreach_set("value", [bool(keep(p.center)) for p in mesh.polygons])
    group = mesh.houdini_bridge_groups.add()
    group.attribute = name
    group.domain = "FACE"
    group.name = name
    group.color = color
    group.show = True


def shot_groups():
    yield from stage()
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    head = bpy.context.active_object
    head.name = "Suzanne"
    head.modifiers.new("Subdivision", "SUBSURF").levels = 1
    with viewport_override():
        bpy.ops.object.shade_smooth()
    add_face_group(head, "ears", (1.0, 0.45, 0.1), lambda c: abs(c.x) > 0.95)
    add_face_group(head, "brow", (0.15, 0.75, 1.0), lambda c: c.z > 0.42 and abs(c.x) < 0.75)
    add_face_group(head, "jaw", (0.35, 0.9, 0.45), lambda c: c.z < -0.42)
    show_tabs("GROUPS")
    set_mode("EDIT")
    with viewport_override():
        bpy.ops.mesh.select_all(action="DESELECT")
    shading("MATCAP")
    soft_viewport()
    look((0, 0, 0), 5.0, 20, 6)
    yield from wait_for_shading(2.0)
    view = region_box("VIEW_3D")
    shoot("groups-panel", (view[0], view[1], view[2] - 30, view[3]))
    set_mode("OBJECT")
    show_tabs("SHELF")


def shot_groups_live():
    """Groups made by Houdini nodes, shown while they are tuned: one of each kind."""
    yield from stage()
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    head = bpy.context.active_object
    head.name = "Suzanne"
    head.modifiers.new("Subdivision", "SUBSURF").levels = 2
    with viewport_override():
        bpy.ops.object.shade_smooth()
    group = tool("groupcreate", "Group Create")
    yield from add_houdini(head, "", group, {"groupname": "top", "groupbase": False, "groupbounding": True,
                                             "size": [3.0, 0.5, 3.0], "t": [0.0, 0.75, 0.0]})
    yield from add_houdini(head, "", group, {"groupname": "creases", "grouptype": "edge", "groupbase": False,
                                             "groupedges": True, "dominedgeangle": True, "minedgeangle": [32.0]})
    sweep = yield from add_houdini(head, "", group, {"groupname": "band", "grouptype": "point", "groupbase": False,
                                                     "groupbounding": True, "size": [3.0, 0.16, 3.0],
                                                     "t": [0.0, -0.7, 0.0]})
    log("groups: %s" % [(g.name, g.domain, g.count) for g in sweep.node_group.houdini_bridge.result_groups])
    active_only(head)
    show_tabs("GROUPS")
    shading("MATCAP")
    soft_viewport()
    look((0, 0, 0), 5.4, 18, 8)
    yield from wait_for_shading(2.0)
    view = region_box("VIEW_3D")
    box = view[0], view[1], view[2] - 30, view[3]
    shoot("groups-live", box)
    frames_dir = os.path.join(WORK, "gif_groups")
    os.makedirs(frames_dir)
    count = 30
    for index in range(count):
        height = -0.8 * math.cos(index / count * 2.0 * math.pi)
        set_value(sweep, "t", [0.0, height, 0.0])
        yield from settle(0.05)
        yield from wait_for_shading(0.3)
        frame_shot(frames_dir, index, box)
    make_gif("groups-live", frames_dir, 10)
    show_tabs("SHELF")


def suzanne_with_groups():
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    head = bpy.context.active_object
    head.name = "Suzanne"
    with viewport_override():
        bpy.ops.object.shade_smooth()
    add_face_group(head, "ears", (1.0, 0.45, 0.1), lambda c: abs(c.x) > 0.95)
    add_face_group(head, "brow", (0.15, 0.75, 1.0), lambda c: c.z > 0.42 and abs(c.x) < 0.75)
    add_face_group(head, "jaw", (0.35, 0.9, 0.45), lambda c: c.z < -0.42)
    return head


def shot_groups_remesh():
    """The same groups on the same head, before and after Remesh changes every face."""
    yield from stage()
    head = suzanne_with_groups()
    active_only(head)
    show_tabs("GROUPS")
    shading("MATCAP")
    soft_viewport()
    overlay = view_space().overlay
    overlay.show_wireframes = True
    overlay.wireframe_threshold = 1.0
    look((0, 0, 0), 4.8, 20, 6)
    yield from wait_for_shading(2.0)
    before = shoot_frame("groups-before", centered_view_box(600))
    caption(before, "Before")
    remesh = yield from add_houdini(head, "", tool("remesh::2.0", "Remesh"), {"targetsize": [0.075]})
    head.modifiers.active = remesh
    active_only(head)
    yield from settle(1.0)
    yield from wait_for_shading(2.0)
    after = shoot_frame("groups-after", centered_view_box(600), sidebar_box())
    caption(after, "After Remesh")
    montage("groups-through-remesh", [before, after])
    overlay.show_wireframes = False
    show_tabs("SHELF")


def shot_groups_stack():
    """Bridge Groups makes the group 'right'; the next modifier inflates only that half."""
    yield from stage()
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    head = bpy.context.active_object
    head.name = "Suzanne"
    head.modifiers.new("Subdivision", "SUBSURF").levels = 2
    with viewport_override():
        bpy.ops.object.shade_smooth()
    groups = tool("bridge::groups", "Bridge Groups")
    yield from add_houdini(head, HDA, groups, {"distance": [0.0]})
    yield from add_houdini(head, HDA, groups, {"group": "right", "distance": [0.13]})
    head.modifiers["Subdivision"].show_expanded = False
    active_only(head)
    view_space().show_region_ui = False
    shading("MATCAP")
    soft_viewport()
    look((0, 0, 0), 5.2, 20, 8)
    yield from settle(1.0)
    yield from wait_for_shading(1.0)
    yield from open_node_panels(first=1)
    yield from wait_for_shading(1.0)
    shoot("groups-through-stack", view_and_properties_box(GROUPS_STACK_LEFT_CROP))
    view_space().show_region_ui = True


def shot_visualize():
    yield from open_demo()
    shading("MATCAP")
    soft_viewport()
    monkey = bpy.data.objects["2 Curvature"]
    show_demo("2 Curvature")
    look(monkey.location, 4.6, 20, 10)
    yield from wait_for_shading(2.0)
    shoot("visualize-curvature", region_box("VIEW_3D", "WINDOW"))


FLOW = "v@flow = curlnoise(@P * 1.3) * 0.45;"


def shot_visualize_flow():
    """A vector whose length varies, as arrows, and the Visualize menu that picks it."""
    yield from stage()
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    head = bpy.context.active_object
    head.name = "Suzanne"
    head.modifiers.new("Subdivision", "SUBSURF").levels = 1
    with viewport_override():
        bpy.ops.object.shade_smooth()
    modifier = yield from add_houdini(head, "", tool("attribwrangle", "Attribute Wrangle"),
                                      {"class": "point", "snippet": FLOW})
    settings = modifier.node_group.houdini_bridge
    log("flow: %r %s %s" % (settings.errors, settings.status, settings.outputs_json[:200]))
    settings.visualize = "flow"
    head.modifiers["Subdivision"].show_expanded = False
    active_only(head)
    view_space().show_region_ui = False
    shading("MATCAP")
    soft_viewport()
    look((0, 0, 0), 4.2, 25, 12)
    yield from settle(1.0)
    yield from wait_for_shading(1.0)
    yield from open_node_panels()
    yield from scroll_properties(40)  # down to the end of the panel, where Output is
    yield from open_section("Output")
    yield from scroll_properties(40)
    yield from wait_for_shading(1.0)
    shoot("visualize-tangents", view_and_properties_box(VISUALIZE_LEFT_CROP))
    yield from scroll_properties(40, down=False)
    view_space().show_region_ui = True


# --- 11. attributes both ways ---------------------------------------------------------

PAINT_VEX = "@P += @N * f@Weight * 0.55;"
PAINT_STEPS = 30
PAINT_LEFT_CROP = 400
PAINT_PANEL_EXTRA = 100


def paint_path(center, radius=0.95, steps=PAINT_STEPS):
    """Points on the side of a sphere facing the camera, in one wave across it."""
    points = []
    for step in range(steps + 1):
        angle = -0.95 + 1.9 * step / steps
        height = 0.55 * math.sin(angle * 3.2)
        flat = math.sqrt(max(0.0, 1.0 - height * height))
        points.append(Vector(center) + radius * Vector((flat * math.sin(angle), -flat * math.cos(angle), height)))
    return points


def paint_along(path, frames_dir, box, result_modifier):
    """Real strokes with the Weight Paint brush, one short segment per frame, each
    followed by the cook it starts."""
    area = find_area("VIEW_3D")
    region = find_region(area, "WINDOW")
    view = area.spaces.active.region_3d
    screen = [location_3d_to_region_2d(region, view, point) for point in path]
    for index in range(len(screen) - 1):
        start, end = screen[index], screen[index + 1]
        mouse_to(region.x + start.x, region.y + start.y)
        yield 0.05
        event("LEFTMOUSE", "PRESS")
        yield 0.05
        for part in (0.34, 0.67, 1.0):
            point = start.lerp(end, part)
            mouse_to(region.x + point.x, region.y + point.y)
            yield 0.04
        event("LEFTMOUSE", "RELEASE")
        yield 0.1
        yield from settle(0.25)
        set_material(result_modifier, clay_material())
        yield from wait_for_shading(0.25)
        frame_shot(frames_dir, index, box)


def shot_attributes():
    """A brush paints a vertex group on the left object; the wrangle on the right object
    reads that object as its input, weights included, and displaces by them."""
    yield from stage()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=6, radius=1.0, location=(-1.5, 0, 0))
    painted = bpy.context.active_object
    painted.name = "Painted"
    with viewport_override():
        bpy.ops.object.shade_smooth()
    group = painted.vertex_groups.new(name="Weight")
    group.add(range(len(painted.data.vertices)), 0.0, "REPLACE")
    select_only(painted)
    result = api().place_hda(bpy.context, "", tool("attribwrangle", "Attribute Wrangle"), "OBJECT")
    result.name = "Wrangle Result"
    modifier = next(m for m in result.modifiers if m.type == "NODES")
    tree = modifier.node_group
    yield from settle(0.3)
    while not tree.houdini_bridge.parms:
        yield 0.1
    set_value(modifier, "class", "point")
    set_value(modifier, "snippet", PAINT_VEX)
    yield from settle(0.5)
    log("wrangle: %r %s" % (tree.houdini_bridge.errors, tree.houdini_bridge.status))
    set_material(modifier, clay_material())
    # An input is read where it stands, so the result sits on the painted sphere. This
    # Blender modifier sets it beside it.
    offset = result.modifiers.new("Beside the input", "DISPLACE")
    offset.direction = "X"
    offset.mid_level = 0.0
    offset.strength = 3.0
    offset.show_expanded = False

    properties = find_area("PROPERTIES").spaces.active
    properties.pin_id = result
    properties.use_pin_id = True
    view_space().show_region_ui = False
    yield from resize_right_column(-PAINT_PANEL_EXTRA)
    yield from open_node_panels()
    shade = view_space().shading
    shade.type = "SOLID"
    shade.light = "STUDIO"
    shade.color_type = "MATERIAL"
    soft_viewport()
    select_only(painted)
    set_mode("WEIGHT_PAINT")
    overlay = view_space().overlay
    overlay.show_wpaint_contours = False
    overlay.weight_paint_mode_opacity = 1.0
    paint = bpy.context.tool_settings.weight_paint.unified_paint_settings
    paint.use_unified_size = True
    paint.size = 55
    paint.use_unified_strength = True
    paint.strength = 1.0
    bpy.context.tool_settings.vertex_group_weight = 1.0
    bpy.context.tool_settings.vertex_group_user = "NONE"
    look((-1.25, 0, 0.0), 9.0, 0, 8, 70.0)
    yield from wait_for_shading(3.0)

    frames_dir = os.path.join(WORK, "gif_attributes")
    os.makedirs(frames_dir)
    view = region_box("VIEW_3D", "WINDOW")
    panel = region_box("PROPERTIES")
    box = view[0] + PAINT_LEFT_CROP, view[1], panel[0] + panel[2] - view[0] - PAINT_LEFT_CROP, view[3]
    yield from paint_along(paint_path((-1.5, 0, 0)), frames_dir, box, modifier)
    last = os.path.join(frames_dir, "%04d.png" % (PAINT_STEPS - 1))
    for index in range(PAINT_STEPS, PAINT_STEPS + 8):
        shutil.copy2(last, os.path.join(frames_dir, "%04d.png" % index))
    make_gif("attributes-painting", frames_dir, 8, 900, "none")
    shutil.copy2(last, os.path.join(IMAGES, "attributes-both-ways.png"))
    set_mode("OBJECT")
    properties.use_pin_id = False
    properties.pin_id = None
    yield from resize_right_column(PAINT_PANEL_EXTRA)
    view_space().show_region_ui = True


# --- 12. native streams ---------------------------------------------------------------


def stream_lighting():
    daylight()


def last_modifier(name):
    return next(m for m in reversed(bpy.data.objects[name].modifiers) if m.type == "NODES")


def stream_backdrop(palette=DAY, z=-0.08):
    """One backdrop under the whole second row of the demo."""
    old = bpy.data.objects.get("Backdrop")
    if old is not None:
        bpy.data.objects.remove(old)
    add_backdrop(center=(10.0, -7.0), z=z, radius=90.0, palette=palette)


def shot_streams():
    yield from open_demo()
    stream_lighting()
    stream_backdrop()

    set_value(last_modifier("8 Scatter (instances)"), "size", [0.075])
    show_demo("8 Scatter*", "Backdrop")
    render_camera((1.7, -9.8, 1.05), (0.0, -6.9, -0.05), 40.0)
    yield from settle(0.5)
    render("stream-instances", 256)

    show_demo("9 Grass*", "Backdrop")
    render_camera((4.3, -9.1, 0.5), (3.9, -6.9, 0.3), 36.0)
    yield from settle(0.5)
    render("stream-grass", 256)

    shatter = last_modifier("12 Shatter (fragments)")
    set_value(shatter, "pieces", [110])
    set_value(shatter, "explode", [0.22])
    show_demo("12 Shatter*", "Backdrop")
    render_camera((13.4, -14.6, 2.5), (16.0, -7.0, 1.0), 62.0)
    yield from settle(1.0)
    render("stream-fragments", 256)

    # Thousands of shapes in the distance, a few readable ones in front, in focus.
    many = last_modifier("13 Many instances (viewport points)")
    set_value(many, "size", [0.016])
    show_demo("13 Many*", "Backdrop")
    camera = render_camera((21.1, -8.72, 0.17), (20.4, -7.5, 0.0), 45.0)
    camera.data.dof.use_dof = True
    camera.data.dof.focus_distance = 0.45
    camera.data.dof.aperture_fstop = 8.0
    yield from settle(1.0)
    render("stream-many-instances", 192)
    camera.data.dof.use_dof = False

    # Fireflies glow: they get the dusk.
    daylight(DUSK, key=0.7, key_rotation=(70, 0, 200), strength=1.0)
    stream_backdrop(DUSK)
    bpy.data.materials["bridge_fireflies"].node_tree.nodes["Principled BSDF"].inputs[
        "Emission Strength"].default_value = 3.0
    swarm = last_modifier("10 Fireflies (points)")
    set_value(swarm, "size", [0.006])
    set_value(swarm, "color", [1.0, 0.5, 0.1])
    show_demo("10 Fireflies*", "Backdrop")
    render_camera((9.1, -10.7, 0.9), (8.0, -7.0, 0.8), 36.0)
    yield from settle(1.0)
    render("stream-points", 256)
    bpy.data.objects.remove(bpy.data.objects["Backdrop"])
    stream_lighting()


def puff_mesh():
    """A cumulus shape: a few overlapping spheres, flat underneath."""
    mesh = bmesh.new()
    for center, radius in (((0.0, 0.0, 0.1), 1.05), ((-1.15, 0.1, -0.1), 0.72), ((1.2, -0.1, -0.15), 0.66),
                           ((0.45, 0.3, 0.75), 0.62), ((-0.5, -0.2, 0.55), 0.58)):
        matrix = Matrix.Translation(center) @ Matrix.Scale(radius, 4)
        bmesh.ops.create_icosphere(mesh, subdivisions=4, radius=1.0, matrix=matrix)
    for vertex in mesh.verts:
        vertex.co.z = max(vertex.co.z, -0.42)
    data = bpy.data.meshes.new("Cloud")
    mesh.to_mesh(data)
    mesh.free()
    obj = bpy.data.objects.new("Cloud", data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def shot_volume():
    """Houdini's own Cloud and Cloud Noise, chained: the second one gets the VDB untouched.
    A sun from behind and to the side lights the edges, a deep sky gives the contrast."""
    yield from stage()
    daylight(key=11.0, key_rotation=(49.5, 0, 247))
    sky_world(SKY, 0.55)
    puff = puff_mesh()
    yield from add_houdini(puff, "", tool("cloud", "Cloud"), {"samplediv": [260], "densitymultiplier": [9.0]})
    yield from settle(1.0, 120.0)
    yield from add_houdini(puff, "", tool("cloudnoise", "Cloud Noise"), {})
    yield from settle(1.0, 120.0)
    render_camera((0.0, -8.6, -0.5), (0.0, 0.0, 0.2), 50.0)
    bpy.context.scene.cycles.volume_bounces = 16
    render("stream-volume", 512)


# --- 13. timeline and cache -----------------------------------------------------------


def shot_cache():
    yield from open_demo()
    stream_lighting()
    scene = bpy.context.scene
    rock = bpy.data.objects["7 Cached rock"]
    show_demo("7 Cached rock")
    active_only(rock)
    show_tabs("MODIFIERS")
    scene.frame_set(30)
    shading("MATERIAL")
    look((24.0, 0.4, 0.7), 6.0, 25, 18)
    yield from wait_for_shading(2.0)
    yield from open_node_panels()
    yield from wait_for_shading(4.0)
    shoot("timeline-cache", no_status_bar_box())
    row = node_rows()[0]
    shoot("cache-section", properties_box(top=row[1] + int(CACHE_ABOVE_ROW * ui_scale()),
                                          bottom=row[1] - int(CACHE_BELOW_ROW * ui_scale())))
    scene.frame_set(1)
    show_tabs("SHELF")


# --- 14. preferences ------------------------------------------------------------------


def shot_preferences():
    bpy.ops.houdini_bridge.open_preferences()
    yield 1.0
    windows = bpy.context.window_manager.windows
    with bpy.context.temp_override(window=windows[len(windows) - 1]):
        bpy.ops.wm.window_close()
    yield 0.5
    # The large area of the main window takes the editor for a moment: the tab fits.
    area = find_area("VIEW_3D")
    area.type = "PREFERENCES"
    yield 1.0
    preferences = bpy.context.preferences.addons[MODULE].preferences
    preferences.folders_open = False  # the default, as the guide says
    for tab, name, height in (("MODIFIERS", "preferences-modifiers", PREFERENCES_MODIFIERS_HEIGHT),
                              ("HOUDINI", "preferences-houdini", PREFERENCES_HOUDINI_HEIGHT)):
        preferences.tab = tab
        yield from wait_for_shading(1.0)
        region = find_region(area, "WINDOW")
        top = window().height - (region.y + region.height)
        # From the tab row down: the extension's File row above it shows a local path.
        shoot(name, (region.x, top + PREFERENCES_TABS_TOP, region.width, height))
    area.type = "VIEW_3D"
    yield 0.5


def shot_apply():
    """A live stack with its Apply buttons, then the frozen result of Apply All."""
    yield from bright_stage()
    rock = make_rock()
    rock.data.materials.append(clay_material())
    yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"), ROCK_PARMS)
    yield from add_houdini(rock, "", tool("remesh::2.0", "Remesh"), {"targetsize": [0.09]})
    for modifier in rock.modifiers:
        set_material(modifier, clay_material())
    active_only(rock)
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    shading("RENDERED")
    look((0, 0, -0.1), 5.4, 25, 24)
    yield from settle(1.0)
    yield from wait_for_shading(1.0)
    yield from open_node_panels(first=1)
    yield from wait_for_shading(3.0)
    x, top, width, _ = region_box("PROPERTIES")
    buttons = shoot_frame("apply-buttons", (x, top, width, 620))
    bpy.ops.houdini_bridge.apply_all(object=rock.name)
    yield from settle(0.5)
    deselect()
    yield from wait_for_shading(6.0)
    frozen = shoot_frame("apply-frozen", centered_view_box(860, 620))
    caption(frozen, "After Apply All, no Houdini needed")
    log("after apply: %s, %d modifiers" % (sorted(o.name for o in bpy.context.view_layer.objects), len(rock.modifiers)))
    montage("apply-freeze", [buttons, frozen])
    show_tabs("SHELF")


def shot_multiple_inputs():
    """Boolean takes the rock on Input 1 and the cutter, a second object, on Input 2."""
    yield from stage()
    for obj in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(obj)  # the overlay of their direction would show
    rock = make_rock()
    yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"), ROCK_PARMS)
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.72, depth=4.2, location=(0.2, 0, 0.1),
                                        rotation=(math.radians(82), 0, math.radians(18)))
    cutter = bpy.context.active_object
    cutter.name = "Cutter"
    cutter.display_type = "WIRE"
    boolean = {"type": "boolean::2.0", "label": "Boolean", "max_inputs": 2}
    modifier = yield from add_houdini(rock, "", boolean, {"booleanop": "subtract"}, others=[cutter])
    settings = modifier.node_group.houdini_bridge
    log("boolean: %r %s %s" % (settings.errors, settings.status, [i.label for i in settings.inputs]))
    active_only(rock)
    view_space().show_region_ui = False
    shading("MATCAP")
    soft_viewport()
    view_space().overlay.show_extras = True  # the cutter's wire
    look((0, 0, 0), 7.2, 58, 16)
    yield from settle(1.0)
    yield from wait_for_shading(1.0)
    yield from open_node_panels(first=1)
    yield from wait_for_shading(1.0)
    shoot("multiple-inputs", view_and_properties_box(INPUTS_LEFT_CROP))
    view_space().overlay.show_extras = False
    view_space().show_region_ui = True


DEMO_TITLES = {1: "Remesh groups", 2: "Curvature", 3: "Tangents", 4: "HDA groups", 5: "Stack groups",
               6: "Time", 7: "Cache", 8: "Instances", 9: "Curves", 10: "Points", 11: "Volume",
               12: "Fragments", 13: "Many instances"}


def shot_demo_overview():
    """The opened demo file from above: all 13 scenes, by day. For this picture the labels
    are short, larger and tilted to the camera, or they would run into each other."""
    _demo["open"] = False  # a fresh copy: the other shots hide objects and change values
    yield from open_demo()
    daylight()
    add_backdrop(center=(12.0, -3.5), z=-0.02, radius=90.0)
    ink = principled("doc_label", (0.015, 0.018, 0.025), 0.8)
    for obj in bpy.data.objects:
        if obj.type == "FONT":
            number = int(obj.data.body.split(".")[0])
            obj.data.body = "%d. %s" % (number, DEMO_TITLES[number])
            obj.data.size = 0.46
            obj.rotation_euler.x = math.radians(45)
            obj.location.z += 0.2
            obj.data.materials.clear()
            obj.data.materials.append(ink)
    deselect()
    render_camera((10.7, -37.0, 21.0), (10.7, -3.4, 0.0), 49.0)
    yield from settle(1.0, 180.0)
    for obj in bpy.data.objects:
        if obj.name[0] in "1234567" and obj.type == "MESH" and not obj.name.startswith(("10", "11", "12", "13")):
            if not obj.data.materials:
                obj.data.materials.append(clay_material())
            for modifier in obj.modifiers:
                if modifier.type == "NODES":
                    set_material(modifier, clay_material())
    fog = bpy.data.materials.get("Houdini Volume")
    if fog is not None:
        volume = next(n for n in fog.node_tree.nodes if n.bl_idname == "ShaderNodeVolumePrincipled")
        volume.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bpy.context.scene.cycles.volume_bounces = 8
    render("demo-overview", 192, (2400, 900))


def shot_live_edit():
    """The owner's screen recording, cut once the cube has been edited; nothing else changes."""
    graph = ("fps=12,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
             "[b][p]paletteuse=dither=none:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", "11", "-i", os.path.join(IMAGES, "live_edit.mp4"),
                    "-filter_complex", graph, "-loop", "0", os.path.join(IMAGES, "live-edit.gif")], check=True)
    yield 0.1


# --- GIFs -----------------------------------------------------------------------------


def make_gif(name, frames_dir, rate, width=960, dither="bayer:bayer_scale=4"):
    pattern = os.path.join(frames_dir, "%04d.png")
    target = os.path.join(IMAGES, name + ".gif")
    graph = "fps=%d,scale=%d:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];" \
            "[b][p]paletteuse=dither=%s:diff_mode=rectangle" % (rate, width, dither)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(rate), "-i", pattern,
                    "-filter_complex", graph, "-loop", "0", target], check=True)
    log("gif %s: %.1f MB" % (name, os.path.getsize(target) / 1e6))


def frame_shot(frames_dir, index, box):
    path = os.path.join(frames_dir, "%04d.png" % index)
    redraw_now()
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.screenshot(filepath=path)
    crop_image(path, [int(v) for v in box])


def shot_gif_parameter():
    yield from stage()
    rock = make_rock()
    add_floor()
    modifier = yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"),
                                      {"height": [0.1], "elementsize": [1.1]})
    set_material(modifier, rock_material())
    active_only(rock)
    view_space().show_region_ui = False
    shading("MATERIAL")
    look((0, 0, -0.1), 7.0, 25, 26)
    yield from wait_for_shading(1.0)
    yield from open_node_panels()
    set_material(modifier, rock_material())
    yield from wait_for_shading()
    frames_dir = os.path.join(WORK, "gif_parameter")
    os.makedirs(frames_dir)
    view = region_box("VIEW_3D", "WINDOW")
    properties = region_box("PROPERTIES")
    left = view[0] + view[2] // 4
    box = left, view[1], properties[0] + properties[2] - left, 800
    count = 30
    for index in range(count):
        height = 0.1 + 0.9 * (0.5 - 0.5 * math.cos(index / count * 2.0 * math.pi))
        set_value(modifier, "height", [height])
        yield from settle(0.05)
        set_material(modifier, rock_material())
        yield from wait_for_shading(0.6)
        frame_shot(frames_dir, index, box)
    make_gif("live-parameter", frames_dir, 10, 840)
    view_space().show_region_ui = True


PULSE = """// Reads the time: Houdini cooks this on every frame of the timeline.
vector drift = @P * 1.8 + set(0.0, @Time * 0.9, 0.0);
float swell = fit(noise(drift), 0.2, 0.8, 0.0, 1.0);
@P += @N * swell * 0.55;
v@Cd = lerp({0.05, 0.16, 0.5}, {1.0, 0.42, 0.08}, swell);
"""


def shot_gif_playback():
    yield from stage()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=160, ring_count=80, radius=1.0, location=(0, 0, 0))
    blob = bpy.context.active_object
    blob.name = "Blob"
    with viewport_override():
        bpy.ops.object.shade_smooth()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 48
    scene.frame_set(1)
    yield from add_houdini(blob, "", tool("attribwrangle", "Attribute Wrangle"),
                           {"class": "point", "snippet": PULSE})
    active_only(blob)
    view_space().show_region_ui = False
    shading("ATTRIBUTE")
    look((0, 0, 0), 7.0, 25, 12, 70.0)
    yield from wait_for_shading(2.0)
    frames_dir = os.path.join(WORK, "gif_playback")
    os.makedirs(frames_dir)
    view = find_area("VIEW_3D")
    timeline = find_area("TIMELINE") or find_area("DOPESHEET_EDITOR")
    top = window().height - (view.y + view.height) + view.height // 6
    box = view.x, top, view.width, view.height * 5 // 6 + timeline.height
    for index in range(48):
        scene.frame_set(index + 1)
        yield from settle(0.05)
        yield from wait_for_shading(0.2)
        frame_shot(frames_dir, index, box)
    scene.frame_set(1)
    make_gif("timeline-playback", frames_dir, 12, 860)
    view_space().show_region_ui = True


# --- the node row, its flags, the group selection, Add Houdini Node -------------------


def properties_box(bottom=None, top=None):
    """The Properties editor, from `top` down to `bottom` (event coordinates, the whole region
    when None), without its column of tabs."""
    region = find_region(find_area("PROPERTIES"), "WINDOW")
    high = region.y + region.height if top is None else top
    low = region.y if bottom is None else max(region.y, bottom)
    return to_box(region.x, low, region.width, high - low)


def view_and_properties_box(left_crop=0):
    """The 3D view (its header included, the sidebar hidden) and the Properties editor beside it."""
    view = region_box("VIEW_3D")
    panel = region_box("PROPERTIES")
    return view[0] + left_crop, view[1], panel[0] + panel[2] - view[0] - left_crop, view[3]


def rock_stack():
    """A rock made of four Houdini steps, for the node rows: Mountain, Remesh, PolyExtrude, PolyReduce."""
    rock = make_rock(5)
    yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"), ROCK_PARMS)
    yield from add_houdini(rock, "", tool("remesh::2.0", "Remesh"), {"targetsize": [0.1]})
    yield from add_houdini(rock, "", tool("polyextrude::2.0", "PolyExtrude"),
                           {"splittype": "elements", "dist": [0.04], "inset": [0.02]})
    yield from add_houdini(rock, "", tool("polyreduce::2.0", "PolyReduce"), {"percentage": [40.0]})
    return rock


def shot_node_row():
    """Four Houdini modifiers, folded, each with one flag set by a click: Lock, Template,
    Display, Bypass, from the top."""
    yield from stage()
    rock = yield from rock_stack()
    active_only(rock)
    yield from wait_for_shading(1.0)
    rows = node_rows()
    log("node rows %s" % rows)
    for index, flag in ((3, "bypass"), (2, "display"), (1, "template"), (0, "lock")):
        yield from click_flag(rows[index], flag)
        yield from settle(0.5)
    yield from wait_for_shading(1.0)
    rows = node_rows()
    shoot("node-row", properties_box(bottom=rows[-1][1] - int(18 * ui_scale())))


def shot_template():
    """A rock smoothed by Blender's Subdivision after PolyReduce, with PolyReduce templated:
    its low result drawn as a pink cage around the smooth one."""
    yield from stage()
    rock = make_rock(5)
    yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"), {"height": [0.55], "elementsize": [0.9]})
    yield from add_houdini(rock, "", tool("polyreduce::2.0", "PolyReduce"), {"percentage": [3.0]})
    rock.modifiers.new("Subdivision", "SUBSURF").levels = 2
    active_only(rock)
    view_space().show_region_ui = False
    shading("MATCAP")
    soft_viewport()
    look((0, 0, 0), 5.8, 30, 18)
    yield from wait_for_shading(1.0)
    rows = node_rows()
    yield from click_flag(rows[1], "template")
    yield from wait_for_shading(1.5)
    shoot("template-flag", view_and_properties_box(TEMPLATE_LEFT_CROP))
    view_space().show_region_ui = True


def display_frame(frames_dir, index, box, copies):
    for copy in range(copies):
        frame_shot(frames_dir, index + copy, box)
    return index + copies


def shot_display():
    """A stack of Blender's Subdivision and three Houdini modifiers; the Display flag set on each
    in turn by a click on its eye, the viewport showing that HDA's output."""
    yield from stage()
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    head = bpy.context.active_object
    head.name = "Suzanne"
    with viewport_override():
        bpy.ops.object.shade_smooth()
    head.modifiers.new("Subdivision", "SUBSURF").levels = 2
    yield from add_houdini(head, "", tool("mountain::2.0", "Mountain"), {"height": [0.3], "elementsize": [0.42]})
    yield from add_houdini(head, "", tool("polyreduce::2.0", "PolyReduce"), {"percentage": [2.0]})
    yield from add_houdini(head, "", tool("polyextrude::2.0", "PolyExtrude"), {
        "splittype": "elements", "dist": [0.07], "inset": [0.035]})
    active_only(head)
    view_space().show_region_ui = False
    shading("MATCAP")
    soft_viewport()
    look((0.05, 0, 0), 5.6, 22, 10)
    yield from wait_for_shading(1.0)
    yield from open_node_panels(first=2)
    rows = node_rows()
    box = view_and_properties_box(DISPLAY_LEFT_CROP)
    frames_dir = os.path.join(WORK, "gif_display")
    os.makedirs(frames_dir)
    index = 0
    for step, row in enumerate(rows):
        yield from click_flag(row, "display")
        yield from settle(0.8)
        yield from wait_for_shading(1.0)
        if step == 0:
            shoot("display-flag", box)
        index = display_frame(frames_dir, index, box, 6)
    make_gif("display-flag", frames_dir, 4, 960)
    yield from click_flag(rows[-1], "display")  # lifted
    yield from settle(0.5)
    view_space().show_region_ui = True


def facing_faces(obj, count, near):
    """`count` faces of `obj` that face the view, nearest the window point `near` first."""
    area = find_area("VIEW_3D")
    region = find_region(area, "WINDOW")
    view = area.spaces.active.region_3d
    eye = view.view_matrix.inverted().translation
    found = []
    for face in obj.data.polygons:
        center = obj.matrix_world @ face.center
        normal = (obj.matrix_world.to_3x3() @ face.normal).normalized()
        if normal.dot((eye - center).normalized()) > 0.75:
            point = location_3d_to_region_2d(region, view, center)
            found.append((((region.x + point.x - near[0]) ** 2 + (region.y + point.y - near[1]) ** 2),
                          (region.x + point.x, region.y + point.y)))
    return [point for _, point in sorted(found)[:count]]


def shot_group_selection():
    """The arrow of PolyExtrude's Group field: the sphere in Edit Mode in an orange frame, faces
    clicked one by one, then Enter writes them into the field and the extrusion follows."""
    yield from stage()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=1.0, location=(0, 0, 0))
    ball = bpy.context.active_object
    ball.name = "Sphere"
    extrude = yield from add_houdini(ball, "", tool("polyextrude::2.0", "PolyExtrude"), {
        "group": "130", "splittype": "elements", "dist": [0.28], "inset": [0.04]})
    tree = extrude.node_group
    active_only(ball)
    view_space().show_region_ui = False
    shading("MATCAP")
    soft_viewport()
    look((0, 0, 0), 5.4, 15, 12)
    yield from wait_for_shading(1.0)
    yield from open_node_panels()
    box = view_and_properties_box(SELECTION_LEFT_CROP)
    frames_dir = os.path.join(WORK, "gif_selection")
    os.makedirs(frames_dir)
    index = display_frame(frames_dir, 0, box, 4)
    with viewport_override():
        bpy.ops.houdini_bridge.select_group("INVOKE_DEFAULT", tree=tree.name, parm="group")
    yield 0.5
    with viewport_override():
        bpy.ops.mesh.select_mode(type="FACE")
    yield from wait_for_shading(0.8)
    index = display_frame(frames_dir, index, box, 3)
    region = find_region(find_area("VIEW_3D"), "WINDOW")
    center = region.x + region.width * 0.5, region.y + region.height * 0.5
    points = facing_faces(ball, 9, center)
    for point in points:
        yield from click(*point, shift=True)
        park_mouse()
        yield from wait_for_shading(0.4)
        index = display_frame(frames_dir, index, box, 1)
    shoot("group-selection", box)
    index = display_frame(frames_dir, index, box, 2)
    press("RET")
    yield from settle(0.8)
    yield from wait_for_shading(1.0)
    log("after the selection: %r %s" % (tree.houdini_bridge.errors, tree.houdini_bridge.status))
    display_frame(frames_dir, index, box, 8)
    make_gif("group-selection", frames_dir, 3, 960)
    view_space().show_region_ui = True


def shot_add_node():
    """Add Houdini Node under Add Modifier, then the search it opens, a few letters typed: side by side."""
    yield from stage()
    rock = make_rock(5)
    yield from add_houdini(rock, "", tool("mountain::2.0", "Mountain"), ROCK_PARMS)
    active_only(rock)
    yield from wait_for_shading(1.0)
    region = find_region(find_area("PROPERTIES"), "WINDOW")
    depth = int(160 * ui_scale())
    pixels = window_pixels()[region.y + region.height - depth:region.y + region.height,
                             region.x:region.x + region.width]
    orange = (pixels[..., 0] > 200) & (pixels[..., 1] > 60) & (pixels[..., 1] < 150) & (pixels[..., 2] < 80)
    ys, xs = numpy.nonzero(orange)
    if not len(xs):
        log("Add Houdini Node: its logo is not drawn")
        return
    area = region_box("PROPERTIES")
    box = area[0], area[1], area[2], ADD_NODE_HEIGHT
    button = shoot_frame("add-node-button", box)
    yield from click(region.x + region.width / 2, region.y + region.height - depth + ys.mean())
    yield 0.8
    type_text("poly")
    yield 1.0
    search = shoot_frame("add-node-search", box)
    press("ESC")
    yield 0.5
    montage("add-houdini-node", [button, search], gap=12)


SHOTS = [shot_live_edit, shot_shelf, shot_asset_shelf, shot_sidebar, shot_sidebar_tabs, shot_header, shot_add_node,
         shot_parameters, shot_modifier_panel, shot_multiparm, shot_node_row, shot_template, shot_display,
         shot_group_selection, shot_stack, shot_cooking, shot_groups, shot_groups_live, shot_groups_remesh,
         shot_groups_stack, shot_attributes, shot_gif_parameter, shot_visualize_flow, shot_apply, shot_multiple_inputs,
         shot_visualize, shot_cache, shot_streams, shot_preferences, shot_gif_playback, shot_volume, shot_demo_overview,
         shot_hero_viewport]


def session():
    os.makedirs(IMAGES, exist_ok=True)
    prepare_work_folder()
    yield from prepare_window()
    yield from prepare_shelf()
    for shot in SHOTS:
        if not ONLY or shot.__name__[len("shot_"):] in ONLY:
            log("--- " + shot.__name__)
            yield from shot()
    log("done")


def run(steps):
    def tick():
        try:
            return next(steps)
        except StopIteration:
            pass
        except Exception:
            traceback.print_exc()
        api().stop_server()
        shutil.rmtree(WORK, ignore_errors=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=1.0, persistent=True)


if bpy.app.use_event_simulate:
    prepare_preferences()
    enable_extension()
    run(session())
else:
    # Popups and sidebar tabs need simulated events, which only a launch flag allows.
    environment = {name: value for name, value in os.environ.items() if name != "OCIO"}
    subprocess.Popen([bpy.app.binary_path, "--factory-startup", "--enable-event-simulate",
                      "--window-geometry", "0", "0", "1920", "1080", "--python", __file__], env=environment)
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.1)
