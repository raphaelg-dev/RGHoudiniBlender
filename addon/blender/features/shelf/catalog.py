"""HDA shelf: cached catalog, favorites, the sidebar list, the Shift+A menu and the Houdini
sub-menu of Add Modifier (the favorites).

The catalog is built by hython (the "catalog" command) and cached in the extension's user
folder, so the shelf shows up without starting Houdini; only Refresh rebuilds it. It lists
the HDAs of the user's folders, those of the Houdini packages (SideFX Labs…) and Houdini's
own SOP nodes.
"""

import fnmatch
import json
import os
import re
import time
from dataclasses import dataclass, field

import bpy
import bpy.utils.previews
from bpy.props import EnumProperty, IntProperty, StringProperty

from ....shared import contract
from ...core import cook, events, feedback, hython, link, modifiers, paths, preferences, targets

LABS_PREFIX = "Labs "  # the start of the label of every SideFX Labs HDA


@dataclass
class Catalog:
    """The tools of the shelf, and the favorites among them."""

    tools: list = field(default_factory=list)  # [tool], as the server describes them
    date: float = 0.0  # time.time() of the read they come from
    favorites: set = field(default_factory=set)  # tool_key of each favorite
    refreshing: bool = False  # a read by Houdini is in flight
    error: str = ""  # why the last read failed, or ""
    read_this_session: bool = False  # Houdini read it once since Blender started


state = Catalog()  # read anywhere; its tools change only through _set_tools, which indexes them
_by_key = {}
_by_type = {}  # node type -> the tools of that type (one per file that defines it)
_previews = None

refreshed = events.Signal("refreshed", """refreshed()

Emitted each time Houdini has read the catalog again, once the new tools are kept
(catalog.json is written, the favorites moved to the versions kept). An exception of a
listener becomes the shelf's error ("asset library not written: ...").""")


def tool_key(tool):
    return "%s|%s" % (tool["file"], tool["type"])


def _read_json(name, default):
    try:
        with open(paths.user_file(name), encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return default


def _write_json(name, value):
    path = paths.user_file(name)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def load():
    catalog = _read_json("catalog.json", {})
    _set_tools(catalog.get("tools", []), catalog.get("date", 0.0))
    state.favorites = set(_read_json("favorites.json", []))


def _set_tools(tools, date):
    state.tools = tools
    state.date = date
    _by_key.clear()
    _by_type.clear()
    for tool in tools:
        _by_key[tool_key(tool)] = tool
        _by_type.setdefault(tool["type"], []).append(tool)


def get_tool(key):
    return _by_key.get(key)


def _same_file(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def tool_of(settings):
    """The shelf tool of a Houdini modifier's node type (the one from its HDA file when
    several files define that type), or None."""
    candidates = _by_type.get(settings.node_type, [])
    path = bpy.path.abspath(settings.path) if settings.path else ""
    for tool in candidates:
        if path and tool["file"] and _same_file(tool["file"], path):
            return tool
    return candidates[0] if candidates else None


def is_favorite(key):
    return key in state.favorites


def toggle_favorite(key):
    favorites = state.favorites
    if key in favorites:
        favorites.discard(key)
    else:
        favorites.add(key)
    _write_json("favorites.json", sorted(favorites))


def favorite_tools():
    return [tool for tool in state.tools if tool_key(tool) in state.favorites]


def _hidden_patterns():
    text = preferences.get().hidden_tools
    return [pattern.strip().lower() for pattern in text.split(",") if pattern.strip()]


def is_hidden(tool, patterns=None):
    """Whether the preference "Hide" leaves the tool out (name or type, * as a wildcard)."""
    patterns = _hidden_patterns() if patterns is None else patterns
    names = (tool["label"].lower(), display_name(tool).lower(), tool["type"].lower())
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns for name in names)


def listed_tools():
    """The tools the shelf, the asset shelf and the search show."""
    patterns = _hidden_patterns()
    if not patterns:
        return state.tools
    return [tool for tool in state.tools if not is_hidden(tool, patterns)]


def missing_file(tool):
    """Whether the tool's HDA file is gone (a native Houdini node has no file)."""
    return bool(tool["file"]) and not os.path.exists(tool["file"])


def _catalog_request():
    prefs = preferences.get()
    folders = [bpy.path.abspath(f.path) for f in prefs.folders if f.path and f.enabled]
    return contract.request(
        contract.CATALOG,
        folders=[f for f in folders if os.path.isdir(f)],
        packages=prefs.include_packages,
        native=prefs.include_native,
        all_versions=prefs.all_versions,
        icon_dir=paths.icons_dir(),
    )


def _on_catalog(answer, error):
    state.refreshing = False
    if error:
        state.error = error.splitlines()[-1][:200]
        return
    state.error = ""
    _set_tools(answer.get("tools", []), time.time())
    _write_json("catalog.json", {"date": state.date, "tools": state.tools})
    _follow_favorites()
    try:
        refreshed.emit()
    except Exception as exception:  # the sidebar shelf still works without the asset library
        feedback.log.exception("the asset library could not be written")
        state.error = "asset library not written: %s" % exception


def base_type(node_type):
    """Node type without its version: labs::edge_damage::1.0 → labs::edge_damage."""
    return re.sub(r"::[\d.]+$", "", node_type)


def _type_version(node_type):
    """labs::edge_damage::2.1 → (2, 1); no version → ()."""
    found = re.search(r"::([\d.]+)$", node_type)
    return tuple(int(part) for part in found.group(1).split(".") if part) if found else ()


def newer_version(settings):
    """The newest shelf tool that is a newer version of this HDA, or None."""
    base = base_type(settings.node_type)
    current = _type_version(settings.node_type)
    newer = [tool for tool in state.tools if tool["type"] != settings.node_type
             and base_type(tool["type"]) == base and _type_version(tool["type"]) > current]
    return max(newer, key=lambda tool: _type_version(tool["type"])) if newer else None


def _follow_favorites():
    """The shelf keeps one version per tool: move a favorite pointing to another version to
    the version kept."""
    by_base = {(tool["file"], base_type(tool["type"])): tool_key(tool) for tool in state.tools}
    by_base_any_file = {base_type(tool["type"]): tool_key(tool) for tool in state.tools}
    followed = set()
    for key in state.favorites:
        if key in _by_key:
            followed.add(key)
            continue
        path, _, node_type = key.rpartition("|")
        base = base_type(node_type)
        new_key = by_base.get((path, base)) or by_base_any_file.get(base)
        followed.add(new_key or key)
    if followed != state.favorites:
        state.favorites = followed
        _write_json("favorites.json", sorted(followed))


def refresh():
    """Rebuild the catalog in the background (starts hython if needed)."""
    if state.refreshing:
        return
    state.refreshing = True
    state.error = ""
    cook.send_command("__catalog__", _catalog_request(), _on_catalog)


def _on_houdini_start():
    """Rebuild the shelf with the first Houdini session only (Houdini may stop when idle and
    start again), unless the preferences turn it off."""
    if state.read_this_session:
        return
    state.read_this_session = True
    if preferences.get().refresh_on_start:
        refresh()


def refresh_now():
    """Synchronous refresh (tests)."""
    link.server.ensure_running(blocking=True)
    try:
        answer, _ = link.server.request_sync(_catalog_request())
    except hython.ServerError as error:
        _on_catalog({}, str(error))
        return
    _on_catalog(answer, "")


def icon(tool):
    """Blender preview id of a tool icon (0 when missing)."""
    path = tool.get("icon")
    if not path or _previews is None:
        return 0
    preview = _previews.get(path)
    if preview is None:
        if not os.path.exists(path):
            return 0
        preview = _previews.load(path, path, "IMAGE")
    return preview.icon_id


def display_name(tool):
    """Name without the "Labs" prefix carried by every SideFX Labs HDA."""
    label = tool["label"]
    prefs = preferences.get()
    if prefs.strip_labs_prefix and label.startswith(LABS_PREFIX) and tool["type"].startswith("labs::"):
        label = label[len(LABS_PREFIX):]
    if prefs.all_versions and tool["type"].count("::") >= 2:
        label += " %s" % tool["type"].rsplit("::", 1)[1]  # several versions are listed: tell them apart
    return label


def visible_tools(window_manager):
    """The favorites; while searching, every listed tool that matches."""
    words = window_manager.houdini_bridge_search.lower().split()
    if not words:
        return favorite_tools()
    with_help = preferences.get().search_help
    result = []
    for tool in listed_tools():
        parts = [tool["label"], tool["type"], tool["group"]] + ([tool.get("summary", "")] if with_help else [])
        text = " ".join(parts).lower()
        if all(word in text for word in words):
            result.append(tool)
    return result


class HOUDINI_BRIDGE_OT_tool(bpy.types.Operator):
    """Apply this HDA to the active object, or create a new object driven by it"""

    bl_idname = "houdini_bridge.tool"
    bl_label = "Houdini Tool"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    target: EnumProperty(
        items=[("PREFERENCE", "As set in Apply to", "Use the Apply to choice"),
               ("NEW", "New Object", "Always create a new object"),
               ("MODIFIER", "Modifier", "A modifier on the object of the context, a generator too (Add Modifier)")],
        options={"HIDDEN", "SKIP_SAVE"},
    )

    @classmethod
    def description(cls, context, properties):
        tool = get_tool(properties.key)
        if tool is None:
            return "Tool not found: refresh the shelf"
        lines = [tool["label"]]
        if tool.get("summary"):
            lines.append(tool["summary"])
        if tool["max_inputs"] == 0:
            kind = "Generator: new object at the 3D cursor"
        else:
            kind = "Up to %d input(s)" % tool["max_inputs"]
        lines.append("%s · %s" % (kind, tool["type"]))
        lines.append(tool["file"] or "Houdini node")
        return "\n".join(lines)

    def execute(self, context):
        error = use_tool(context, self.key, self.target)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        return {"FINISHED"}


def takes_modifier(obj):
    """Whether the object can take a Houdini modifier (Add Modifier, Add Houdini Node)."""
    return obj is not None and obj.type in targets.INPUT_TYPES and not obj.houdini_bridge_result


def use_tool(context, key, target):
    """Use the tool `key`: "MODIFIER" on the object of the context, "NEW" on a new object,
    "PREFERENCE" as *Apply to* says. Returns the error to report, or ""."""
    tool = get_tool(key)
    if tool is None:
        return "Tool not found: refresh the shelf"
    if missing_file(tool):
        return "HDA file not found: %s" % tool["file"]
    if target == "MODIFIER":
        if not takes_modifier(context.object):
            return "A Houdini modifier can only be added to a mesh or a Volume object"
        modifiers.create_hda_modifier(context, context.object, tool["file"], tool)
        return ""
    modifiers.place_hda(context, tool["file"], tool, "NEW" if target == "NEW" else None)
    return ""


class HOUDINI_BRIDGE_OT_toggle_favorite(bpy.types.Operator):
    """Add this tool to the favorites (pie menu, Shift+A), or remove it"""

    bl_idname = "houdini_bridge.toggle_favorite"
    bl_label = "Favorite"

    key: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        toggle_favorite(self.key)
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_refresh_shelf(bpy.types.Operator):
    """Read the HDA folders, the Houdini packages and Houdini's nodes again (starts Houdini if needed)"""

    bl_idname = "houdini_bridge.refresh_shelf"
    bl_label = "Refresh Shelf"

    def execute(self, context):
        link.server.startup_error = ""
        refresh()
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_folder(bpy.types.Operator):
    """Add or remove an HDA folder of the shelf"""

    bl_idname = "houdini_bridge.folder"
    bl_label = "HDA Folder"

    action: EnumProperty(items=[("ADD", "Add", ""), ("REMOVE", "Remove", "")])
    index: IntProperty(options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        if self.action == "ADD":
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)

    def execute(self, context):
        prefs = preferences.get(None)  # nowhere to keep the folder while the add-on is not registered
        if prefs is None:
            return {"CANCELLED"}
        if self.action == "ADD":
            prefs.folders.add().path = self.directory
        elif 0 <= self.index < len(prefs.folders):
            prefs.folders.remove(self.index)
        context.preferences.is_dirty = True
        refresh()
        return {"FINISHED"}


def draw_tab(layout, context):
    """The Shelf tab of the sidebar: search, favorites, and where a new tool goes."""
    window_manager = context.window_manager
    prefs = preferences.get(None)  # the target switch draws only the real preferences

    row = layout.row(align=True)
    row.prop(window_manager, "houdini_bridge_search", text="", icon="VIEWZOOM")
    row.operator("houdini_bridge.add_hda", text="", icon="FILE_FOLDER")
    row.operator("houdini_bridge.refresh_shelf", text="", icon="FILE_REFRESH")
    row.operator("houdini_bridge.open_preferences", text="", icon="PREFERENCES")
    if prefs is not None:
        row = layout.row(align=True)
        row.prop(prefs, "default_target", expand=True)

    if state.refreshing:
        layout.label(text="Houdini is reading the tools…", icon="TIME")
    elif state.error:
        layout.label(text=state.error, icon="ERROR")
    if not state.tools:
        if not state.refreshing:
            layout.operator("houdini_bridge.refresh_shelf", text="Read the Houdini Tools", icon="FILE_REFRESH")
        return

    searching = bool(window_manager.houdini_bridge_search.strip())
    tool_list = visible_tools(window_manager)
    if not tool_list:
        if searching:
            layout.label(text="No matching tool", icon="INFO")
        else:
            layout.label(text="Search a tool, star it to keep it here", icon="INFO")
            layout.label(text="All tools: Ctrl+Shift+H", icon="ASSET_MANAGER")
        return

    groups = {}
    grouped = prefs is None or prefs.group_by_category
    for tool in tool_list:
        groups.setdefault(tool["group"] if grouped else "", []).append(tool)
    for group, members in groups.items():
        if group:
            header = layout.row()
            header.enabled = False
            header.label(text=group)
        column = layout.column(align=True)
        for tool in members:
            key = tool_key(tool)
            row = column.row(align=True)
            row.operator("houdini_bridge.tool", text=display_name(tool), icon_value=icon(tool)).key = key
            row.operator("houdini_bridge.toggle_favorite", text="",
                         icon="SOLO_ON" if is_favorite(key) else "SOLO_OFF", emboss=False).key = key


class HOUDINI_BRIDGE_MT_add(bpy.types.Menu):
    """Shelf favorites in Shift+A"""

    bl_idname = "HOUDINI_BRIDGE_MT_add"
    bl_label = "Houdini HDA"

    def draw(self, context):
        layout = self.layout
        favorites = favorite_tools()
        for tool in favorites:
            operator = layout.operator("houdini_bridge.tool", text=display_name(tool), icon_value=icon(tool))
            operator.key = tool_key(tool)
            operator.target = "NEW"
        if not favorites:
            layout.label(text="No favorites in the shelf", icon="INFO")
        layout.separator()
        layout.operator("houdini_bridge.add_hda", text="Open HDA File…", icon="FILE_FOLDER")


def _draw_add_menu(self, context):
    self.layout.separator()
    self.layout.menu("HOUDINI_BRIDGE_MT_add", icon="NODETREE")


class HOUDINI_BRIDGE_MT_modifier_add(bpy.types.Menu):
    """The shelf favorites, as modifiers of the object (Add Modifier menu)"""

    bl_idname = "HOUDINI_BRIDGE_MT_modifier_add"
    bl_label = "Houdini"

    def draw(self, context):
        layout = self.layout
        favorites = favorite_tools()
        for tool in favorites:
            operator = layout.operator("houdini_bridge.tool", text=display_name(tool), icon_value=icon(tool))
            operator.key = tool_key(tool)
            operator.target = "MODIFIER"
        if not favorites:
            row = layout.row()
            row.enabled = False
            row.label(text="No favorites: star tools in the shelf", icon="INFO")


def _draw_modifier_add_menu(self, context):
    """At the end of Blender's Add Modifier menu, for objects that can take a Houdini modifier."""
    if takes_modifier(context.object):
        self.layout.menu("HOUDINI_BRIDGE_MT_modifier_add", icon="NODETREE")


CLASSES = (
    HOUDINI_BRIDGE_OT_tool,
    HOUDINI_BRIDGE_OT_toggle_favorite,
    HOUDINI_BRIDGE_OT_refresh_shelf,
    HOUDINI_BRIDGE_OT_folder,
    HOUDINI_BRIDGE_MT_add,
    HOUDINI_BRIDGE_MT_modifier_add,
)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    window_manager = bpy.types.WindowManager
    window_manager.houdini_bridge_search = StringProperty(
        name="Search", description="Search every tool by name, type, category or help (empty: your favorites)",
        options={"TEXTEDIT_UPDATE"},
    )
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_add.append(_draw_add_menu)
    bpy.types.OBJECT_MT_modifier_add.append(_draw_modifier_add_menu)
    events.server_started.connect(_on_houdini_start)
    load()


def unregister():
    global _previews
    bpy.types.VIEW3D_MT_add.remove(_draw_add_menu)
    bpy.types.OBJECT_MT_modifier_add.remove(_draw_modifier_add_menu)
    events.server_started.disconnect(_on_houdini_start)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.houdini_bridge_search
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
