"""HDA tools as Blender assets: Asset Shelf, drag and drop, favorites pie menu.

After each shelf refresh, the catalog is written as an asset library: one small
Empty object per HDA, marked as an asset, with the HDA icon as its preview, its
group as catalog, and the tool reference in custom properties of its asset
data. Blender then gives the HDAs its own Asset Shelf: large thumbnails, search,
catalogs, a popup on a hotkey, and drag and drop into the viewport: dropping
an HDA on an object applies it to that object.
"""

import contextlib
import ctypes
import json
import os
import re
import sys
import uuid

import bpy
import numpy as np
from bpy.props import EnumProperty, StringProperty
from bpy_extras import view3d_utils

from ...core import feedback, modifiers, paths, preferences
from . import catalog

LIBRARY_NAME = "Houdini Bridge HDAs"
LIBRARY_FILE = "houdini_bridge_hdas.blend"
INDEX_FILE = "index.json"
SHELF_IDNAME = "HOUDINI_BRIDGE_AST_hdas"
PIE_IDNAME = "HOUDINI_BRIDGE_MT_favorites_pie"
CATALOG_NAMESPACE = uuid.UUID("6f1c2f9e-6a7b-4f0e-9b0c-3d2a1e5f7c11")
PIE_SLOTS = 7  # the eighth slot opens the whole shelf

_keymap_items = []
_index_cache = {}


def _catalog_depth():
    return preferences.get().catalog_depth


def _catalog_path(group):
    """Catalog of a tool group, cut to the preferred number of levels.

    Blender unfolds the whole catalog tree of the asset shelf popup and offers no
    way to fold it: a shallow tree keeps it readable."""
    # The catalog file uses ":" as separator, and "/" separates levels.
    parts = [part.strip().replace(":", " -").replace("\\", "-") for part in group.split("/")]
    parts = [part for part in parts if part][: _catalog_depth()]
    return "/".join(parts) or "Houdini Bridge"


def _write_catalogs(groups, folder):
    ids = {}
    lines = [
        "# Written by RG Houdini Bridge: one catalog per HDA group.",
        "VERSION 1",
        "",
    ]
    for group in sorted({_catalog_path(g) for g in groups}):
        parts = group.split("/")
        for depth in range(1, len(parts) + 1):
            path = "/".join(parts[:depth])
            if path in ids:
                continue
            ids[path] = str(uuid.uuid5(CATALOG_NAMESPACE, path))
            lines.append("%s:%s:%s" % (ids[path], path, path.replace("/", "-")))
    with open(os.path.join(folder, "blender_assets.cats.txt"), "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    return ids


def _set_preview(data, datablock, png_path):
    if not png_path or not os.path.exists(png_path):
        return
    image = data.images.load(png_path, check_existing=False)
    width, height = image.size
    pixels = np.empty(width * height * 4, np.float32)
    image.pixels.foreach_get(pixels)
    preview = datablock.preview_ensure()
    preview.image_size = (width, height)
    preview.image_pixels_float.foreach_set(pixels)


@contextlib.contextmanager
def temp_data():
    """bpy.data.temp_data(), with the data it gives safe to hold during the block and after.

    Up to Blender 5.2, the `__enter__` of temp_data() gives the data without the reference it
    owes the caller (fixed in Blender 5.3, commit 9b217bcbd6): the context frees the data when
    it goes while the name bound by `as` still points at it, and that name then refers to
    freed memory, which damages Python's memory and crashes Blender later, most often in the
    garbage collector. The missing reference is added only when it is missing, so
    a Blender with the fix is left alone."""
    context = bpy.data.temp_data()
    data = context.__enter__()
    alone = object()
    held = [alone]  # held twice, as the data should be: by the context and by `data`
    if sys.getrefcount(data) < sys.getrefcount(alone):
        ctypes.pythonapi.Py_IncRef(ctypes.py_object(data))
    del held, alone
    try:
        yield data
    finally:
        context.__exit__(None, None, None)


def build_library(tools):
    """Write the catalog as an asset library and register it in the preferences.

    The assets are made in temporary data, apart from the open file: their names
    cannot clash with the user's objects, and nothing is left behind."""
    folder = paths.library_dir()
    os.makedirs(folder, exist_ok=True)
    catalog_ids = _write_catalogs([tool["group"] for tool in tools], folder)
    index = {}
    with temp_data() as data:
        objects = []
        names = [catalog.display_name(tool) for tool in tools]
        clashing = {name for name in names if names.count(name) > 1}
        for tool, name in zip(tools, names, strict=False):
            if name in clashing:
                # Two different tools with one label: the group tells them apart.
                name = "%s (%s)" % (name, tool["group"].split("/")[-1])
            obj = data.objects.new(name, None)
            obj.empty_display_size = 0.01
            obj.asset_mark()
            asset = obj.asset_data
            asset.description = tool.get("summary", "")
            asset.catalog_id = catalog_ids[_catalog_path(tool["group"])]
            asset.author = "Houdini HDA"
            asset.tags.new("Houdini")
            asset["houdini_bridge_file"] = tool["file"]
            asset["houdini_bridge_type"] = tool["type"]
            asset["houdini_bridge_label"] = tool["label"]
            asset["houdini_bridge_max_inputs"] = int(tool.get("max_inputs", 0))
            _set_preview(data, obj, tool.get("icon"))
            objects.append(obj)
            # Duplicate labels get a ".001" suffix: the index maps the final name back.
            index[obj.name] = catalog.tool_key(tool)
        data.libraries.write(os.path.join(folder, LIBRARY_FILE), set(objects), fake_user=True)
    with open(os.path.join(folder, INDEX_FILE), "w", encoding="utf-8") as file:
        json.dump(index, file, ensure_ascii=False)
    _index_cache.clear()
    register_library(folder)
    return len(objects)


def register_library(folder):
    """Add the folder to Blender's asset libraries, once."""
    libraries = bpy.context.preferences.filepaths.asset_libraries
    wanted = os.path.normcase(os.path.normpath(folder))
    for library in libraries:
        if os.path.normcase(os.path.normpath(bpy.path.abspath(library.path))) == wanted:
            return library
    library = libraries.new(name=LIBRARY_NAME, directory=folder)
    bpy.context.preferences.is_dirty = True
    return library


def library_ready():
    return os.path.exists(os.path.join(paths.library_dir(), LIBRARY_FILE))


def tool_from_asset(asset):
    """The shelf tool an asset stands for, or None for any other asset."""
    try:
        metadata = asset.metadata
        return {
            "file": metadata["houdini_bridge_file"],
            "type": metadata["houdini_bridge_type"],
            "label": metadata["houdini_bridge_label"],
            "max_inputs": int(metadata["houdini_bridge_max_inputs"]),
        }
    except (KeyError, TypeError, AttributeError):
        return None


def _index():
    if not _index_cache:
        try:
            with open(os.path.join(paths.library_dir(), INDEX_FILE), encoding="utf-8") as file:
                _index_cache.update(json.load(file))
        except (OSError, ValueError):
            pass
    return _index_cache


def _tool_for_operator(operator, context):
    """The tool behind an asset shelf click or drop.

    Blender gives the asset to the operator through its asset_library_type,
    asset_library_identifier and relative_asset_identifier properties; the last
    part of the relative identifier is the asset name."""
    asset = getattr(context, "asset", None)
    if asset is not None:
        tool = tool_from_asset(asset)
        if tool is not None:
            return tool
    name = re.split(r"[\\/]", operator.relative_asset_identifier)[-1]
    return catalog.get_tool(_index().get(name, ""))


LIBRARY_TYPES = [
    ("ALL", "All", ""),
    ("LOCAL", "Local", ""),
    ("ESSENTIALS", "Essentials", ""),
    ("CUSTOM", "Custom", ""),
]


class _AssetOperator:
    """Properties through which an Asset Shelf passes the clicked or dragged asset."""

    asset_library_type: EnumProperty(items=LIBRARY_TYPES, options={"HIDDEN", "SKIP_SAVE"})
    asset_library_identifier: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    relative_asset_identifier: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def tool(self, context):
        tool = _tool_for_operator(self, context)
        if tool is None:
            self.report({"ERROR"}, "Unknown HDA asset: refresh the shelf")
        elif catalog.missing_file(tool):
            self.report({"ERROR"}, "HDA file not found: %s" % tool["file"])
            tool = None
        return tool


def _drop_target(context, event):
    """(object under the mouse or None, 3D location under the mouse)."""
    region, view = context.region, context.region_data
    if region is None or view is None or region.type != "WINDOW":
        return None, context.scene.cursor.location.copy()
    coordinates = (event.mouse_region_x, event.mouse_region_y)
    origin = view3d_utils.region_2d_to_origin_3d(region, view, coordinates)
    direction = view3d_utils.region_2d_to_vector_3d(region, view, coordinates)
    hit, location, _, _, obj, _ = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin, direction)
    if hit:
        obj = obj.original if obj is not None else None
        if obj is not None and obj.type == "MESH" and not obj.houdini_bridge_result:
            return obj, location
        return None, location
    location = view3d_utils.region_2d_to_location_3d(region, view, coordinates, context.scene.cursor.location)
    return None, location


class HOUDINI_BRIDGE_OT_asset_apply(_AssetOperator, bpy.types.Operator):
    """Apply this HDA (to the active object, or as a new object, as set in the shelf)"""

    bl_idname = "houdini_bridge.asset_apply"
    bl_label = "Apply HDA"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        tool = self.tool(context)
        if tool is None:
            return {"CANCELLED"}
        modifiers.place_hda(context, tool["file"], tool)
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_asset_drop(_AssetOperator, bpy.types.Operator):
    """Drop an HDA: on an object, it becomes a modifier of that object; elsewhere, a new object"""

    bl_idname = "houdini_bridge.asset_drop"
    bl_label = "Drop HDA"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def invoke(self, context, event):
        tool = self.tool(context)
        if tool is None:
            return {"CANCELLED"}
        target, location = _drop_target(context, event)
        if target is not None and tool["max_inputs"] > 0:
            for other in context.selected_objects:
                other.select_set(False)
            target.select_set(True)
            context.view_layer.objects.active = target
            modifiers.create_hda_modifier(context, target, tool["file"], tool, sources=[])
            self.report({"INFO"}, "%s applied to %s" % (catalog.display_name(tool), target.name))
        else:
            obj = modifiers.create_hda_object(context, tool["file"], tool)
            if tool["max_inputs"] == 0:
                obj.location = location
        return {"FINISHED"}

    def execute(self, context):
        """Without a mouse position (called from a script): same as a click."""
        return HOUDINI_BRIDGE_OT_asset_apply.execute(self, context)


class HOUDINI_BRIDGE_AST_hdas(bpy.types.AssetShelf):
    bl_space_type = "VIEW_3D"
    bl_idname = SHELF_IDNAME
    bl_activate_operator = "HOUDINI_BRIDGE_OT_asset_apply"
    bl_drag_operator = "HOUDINI_BRIDGE_OT_asset_drop"
    bl_default_preview_size = 72

    @classmethod
    def poll(cls, context):
        return context.mode in {"OBJECT", "EDIT_MESH", "SCULPT"}

    @classmethod
    def asset_poll(cls, asset):
        return asset.id_type == "OBJECT" and tool_from_asset(asset) is not None

    @classmethod
    def draw_context_menu(cls, context, asset, layout):
        """Right click on a thumbnail: add the tool to the favorites, or remove it."""
        tool = tool_from_asset(asset)
        if tool is None:
            return
        key = catalog.tool_key(tool)
        favorite = catalog.is_favorite(key)
        layout.operator(
            "houdini_bridge.toggle_favorite",
            text="Remove from Favorites" if favorite else "Add to Favorites",
            icon="SOLO_OFF" if favorite else "SOLO_ON",
        ).key = key


class HOUDINI_BRIDGE_MT_favorites_pie(bpy.types.Menu):
    bl_idname = PIE_IDNAME
    bl_label = "Houdini Favorites"

    def draw(self, context):
        pie = self.layout.menu_pie()
        favorites = catalog.favorite_tools()
        for tool in favorites[:PIE_SLOTS]:
            operator = pie.operator("houdini_bridge.tool", text=catalog.display_name(tool),
                                    icon_value=catalog.icon(tool))
            operator.key = catalog.tool_key(tool)
        if not favorites:
            pie.label(text="No favorites yet: star tools in the shelf", icon="INFO")
        more = pie.operator("wm.call_asset_shelf_popover", text="All Houdini Tools…", icon="ASSET_MANAGER")
        more.name = SHELF_IDNAME


class HOUDINI_BRIDGE_OT_build_library(bpy.types.Operator):
    """Write the HDA catalog as a Blender asset library (done after every shelf refresh)"""

    bl_idname = "houdini_bridge.build_library"
    bl_label = "Build Asset Library"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        count = build_library(catalog.listed_tools())
        self.report({"INFO"}, "%d HDAs written to the asset library" % count)
        return {"FINISHED"}


CLASSES = (
    HOUDINI_BRIDGE_OT_asset_apply,
    HOUDINI_BRIDGE_OT_asset_drop,
    HOUDINI_BRIDGE_AST_hdas,
    HOUDINI_BRIDGE_MT_favorites_pie,
    HOUDINI_BRIDGE_OT_build_library,
)


def _on_refreshed():
    """Rebuild the asset library from the refreshed catalog."""
    build_library(catalog.listed_tools())


def _build_if_missing():
    """Build the asset library at startup when a cached catalog has none (after an upgrade)."""
    try:
        if catalog.state.tools and not library_ready():
            build_library(catalog.listed_tools())
    except Exception as error:  # never block Blender's startup
        feedback.log.warning("asset library not built: %s", error)
    return None


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    key_config = bpy.context.window_manager.keyconfigs.addon
    if key_config is not None:
        keymap = key_config.keymaps.new(name="3D View", space_type="VIEW_3D")
        popover = keymap.keymap_items.new("wm.call_asset_shelf_popover", "H", "PRESS", ctrl=True, shift=True)
        popover.properties.name = SHELF_IDNAME
        pie = keymap.keymap_items.new("wm.call_menu_pie", "H", "PRESS", shift=True, alt=True)
        pie.properties.name = PIE_IDNAME
        _keymap_items.extend([(keymap, popover), (keymap, pie)])
    bpy.app.timers.register(_build_if_missing, first_interval=1.0)
    catalog.refreshed.connect(_on_refreshed)


HOTKEY_LABELS = ("Houdini asset shelf (thumbnails, drag and drop)", "Favorites pie menu")


def hotkeys():
    """(label, keymap, item) of the add-on hotkeys, as the user configured them.

    The items registered in the add-on keyconfig are matched with their copies in
    the user keyconfig, where Blender stores the user's edits."""
    found = []
    user_config = bpy.context.window_manager.keyconfigs.user
    for label, (keymap, item) in zip(HOTKEY_LABELS, _keymap_items, strict=False):  # no item without an add-on keyconfig
        user_keymap = user_config.keymaps.get(keymap.name)
        user_item = None
        if user_keymap is not None:
            for candidate in user_keymap.keymap_items:
                if (candidate.idname == item.idname
                        and getattr(candidate.properties, "name", "") == item.properties.name):
                    user_item = candidate
                    break
        found.append((label, user_keymap or keymap, user_item or item))
    return found


def unregister():
    if bpy.app.timers.is_registered(_build_if_missing):  # not run yet (background mode runs no timer)
        bpy.app.timers.unregister(_build_if_missing)
    catalog.refreshed.disconnect(_on_refreshed)
    for keymap, item in _keymap_items:
        try:
            keymap.keymap_items.remove(item)
        except (ReferenceError, RuntimeError):
            pass
    _keymap_items.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
