"""Add-on preferences: everything set once per machine; the sidebar keeps the daily work.

Six tabs, each made of collapsible sections:
- Modifiers: what a new Houdini modifier does, how its parameters show;
- Output: instances, curves and points, and where volume files go;
- Shelf: where the tools come from and how they are listed;
- Houdini: which installed version runs, and how its session lives;
- Viewport: what is drawn over the 3D view;
- Hotkeys: the asset shelf and the favorites pie.

The defaults, and get(), are in core/preferences.py.
"""

import os
import time

import bpy
import rna_keymap_ui
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ...shared import contract
from ..core import cook, feedback, hython, link, parameters, paths, streams, targets
from ..core.preferences import DEFAULTS, get
from ..features.shelf import assets, catalog


def _rebuild_library(self, context):
    """New category depth or hidden tools: rewrite the asset library from the cached catalog."""
    if catalog.state.tools:
        assets.build_library(catalog.listed_tools())


def _refresh_limits(self, context):
    parameters.refresh_limits()


def _refresh_visibility(self, context):
    """Hide Like Houdini or Houdini Interface changed: apply it to every Houdini modifier (who
    draws its inputs, and which ones Blender hides), and draw the panels again."""
    parameters.apply_houdini_interface_to_all()
    feedback.redraw(("VIEW_3D", "PROPERTIES"))  # the modifier panel asks parameters.is_hidden too


def _refresh_instance_display(self, context):
    for target in targets.all_targets():
        streams.apply_instance_display(target.settings)


def _recook_instances(self, context):
    """The sphere detail changed: modifiers with instances get their shapes again."""
    for target in targets.all_targets():
        if target.settings.live and streams.summary(target.settings).get(contract.INSTANCES):
            cook.cooks.request(target.tree, force=True)


def _recook_streams(self, context):
    for target in targets.all_targets():
        found = streams.summary(target.settings)
        if target.settings.live and (found.get(contract.CURVES) or found.get(contract.POINTS)):
            cook.cooks.request(target.tree, force=True)


def _redraw(self, context):
    feedback.redraw(("VIEW_3D", "PROPERTIES", "PREFERENCES"))


TARGET_ITEMS = [
    ("MODIFIER", "Active Object", "Add the HDA as a non-destructive modifier on the active object", "MODIFIER", 0),
    ("NEW", "New Object", "Create a new object driven by the HDA", "OBJECT_DATA", 1),
]


class HoudiniBridgeFolder(bpy.types.PropertyGroup):
    path: StringProperty(name="Folder", subtype="DIR_PATH")
    enabled: BoolProperty(name="Enabled", default=True)


class HoudiniBridgePreferences(bpy.types.AddonPreferences):
    bl_idname = paths.PACKAGE

    tab: EnumProperty(items=[
        ("MODIFIERS", "Modifiers", "What a new Houdini modifier does, how its parameters show", "MODIFIER", 0),
        ("OUTPUT", "Output", "Instances, curves, points, and where volume files go", "OUTLINER_OB_POINTCLOUD", 1),
        ("SHELF", "Shelf", "Where the tools come from and how they are listed", "ASSET_MANAGER", 2),
        ("HOUDINI", "Houdini", "Which Houdini runs, and how its session lives", "NODETREE", 3),
        ("VIEWPORT", "Viewport", "What is drawn over the 3D view", "VIEW3D", 4),
        ("HOTKEYS", "Hotkeys", "Asset shelf and favorites pie", "EVENT_H", 5),
    ])

    # Modifiers tab
    default_target: EnumProperty(
        name="Apply to", items=TARGET_ITEMS, default=DEFAULTS["default_target"],
        description="Where a tool goes when clicked: a modifier on the active object, or a new object",
    )
    generator_at_cursor: BoolProperty(
        name="Generators at the 3D Cursor",
        description="A tool without input (a generator) makes its object at the 3D cursor; off: at the world origin",
        default=DEFAULTS["generator_at_cursor"],
    )
    live_default: BoolProperty(
        name="Live Updates",
        description="New Houdini modifiers recook as soon as a parameter or an input changes",
        default=DEFAULTS["live_default"],
    )
    default_output: EnumProperty(
        name="Output",
        description="How new Houdini modifiers bring their result back (each modifier can change it)",
        items=[
            ("NATIVE", "Native", "Instances, open curves, points and volumes come back as Blender instances, "
             "curves, point clouds and volumes, next to the mesh"),
            ("MESH", "Mesh", "Everything is realized as one mesh: an editable result"),
        ],
        default=DEFAULTS["default_output"],
    )
    edit_mode_cook: EnumProperty(
        name="In Edit Mode",
        items=[
            ("LIVE", "Cook Live", "Recook while the mesh is being edited"),
            ("ON_EXIT", "Cook on Exit", "Recook once, when leaving Edit Mode (for heavy HDAs)"),
        ],
        default=DEFAULTS["edit_mode_cook"],
    )
    parameter_limits: EnumProperty(
        name="Limits",
        description="Which Houdini bounds a parameter value cannot cross in Blender. In Houdini, dragging stops "
        "at the ends of the slider range; a Blender field has only hard limits",
        items=[
            ("STRICT", "Strict Bounds",
             "Only the bounds locked in Houdini (typing can leave a slider range there too)"),
            ("MINIMUM", "Slider Minimum", "Strict bounds, and the minimum of the slider range: no accidental negative "
             "Step Size. The maximum stays open (more iterations than the slider offers)"),
            ("RANGE", "Slider Range", "Strict bounds, and both ends of the slider range"),
        ],
        default=DEFAULTS["parameter_limits"],
        update=_refresh_limits,
    )
    houdini_interface: BoolProperty(
        name="Houdini Interface",
        description="The extension draws the parameters in the modifier, in Houdini's order and folders (tabs, "
        "ramps, buttons, group fields with their picker and arrow). Off: Blender draws them as Geometry Nodes "
        "inputs, and the modifier only adds what an input cannot show; a file saved off opens with its "
        "parameters in a Blender without the extension",
        default=DEFAULTS["houdini_interface"],
        update=_refresh_visibility,
    )
    follow_hide_when: BoolProperty(
        name="Hide Like Houdini",
        description="A parameter Houdini hides (hide-when conditions, other parameters) is hidden in the modifier "
        "too, from the next cook; off: every parameter shows",
        default=DEFAULTS["follow_hide_when"],
        update=_refresh_visibility,
    )
    folders_open: BoolProperty(
        name="Open Folders",
        description="Houdini folders start open in the modifier panel (closed: only the parameters outside "
        "any folder show at first)",
        default=DEFAULTS["folders_open"],
    )
    bring_back_extras: BoolProperty(
        name="Attributes and Groups",
        description="The Houdini result keeps its numeric attributes and its groups (as boolean attributes): "
        "spreadsheet, shaders, Geometry Nodes, Visualize. Off for lighter cooks of heavy meshes",
        default=DEFAULTS["bring_back_extras"],
    )

    # Output tab
    points_above: IntProperty(
        name="Points Beyond",
        description="With Instances in Viewport on Auto, more instances than this are drawn as one point each "
        "in the viewport (a million instances take Blender over 100 ms to draw, their points 1 ms). "
        "The render always gets the instances",
        default=DEFAULTS["points_above"], min=1000, soft_max=2_000_000, step=10_000,
        update=_refresh_instance_display,
    )
    sphere_frequency: IntProperty(
        name="Sphere Detail",
        description="Houdini quadric spheres (one primitive, no polygons) become instances of one polygon sphere: "
        "its frequency, as in Houdini's Sphere node (4: 320 faces)",
        default=DEFAULTS["sphere_frequency"], min=contract.SPHERE_FREQUENCY_RANGE[0],
        max=contract.SPHERE_FREQUENCY_RANGE[1],
        update=_recook_instances,
    )
    curve_radius: FloatProperty(
        name="Curve Radius",
        description="Radius of the curves that come without a width attribute",
        default=DEFAULTS["curve_radius"], min=0.0, soft_max=0.1, precision=4, unit="LENGTH",
        update=_recook_streams,
    )
    point_radius: FloatProperty(
        name="Point Radius",
        description="Radius of the points that come without a pscale attribute",
        default=DEFAULTS["point_radius"], min=0.0, soft_max=0.5, precision=4, unit="LENGTH",
        update=_recook_streams,
    )
    live_volume_folder: StringProperty(
        name="Live Volume Files",
        description="Where Houdini writes the .vdb files of live results, replaced at each cook and removed when "
        "Houdini stops; empty: the system's temporary folder",
        default=DEFAULTS["live_volume_folder"], subtype="DIR_PATH",
    )
    applied_volumes: EnumProperty(
        name="Applied Volumes",
        description="Where Apply keeps the .vdb file of a volume (the live one is temporary)",
        items=[
            ("BESIDE", "Next to the .blend", "In a folder next to the saved .blend file, with a relative path: "
             "move the folder with the file. In a file not saved yet, the volume waits in the extension's "
             "folder and moves next to the .blend when the file is first saved"),
            ("FOLDER", "In a Folder", "In the folder set below, whatever the .blend"),
        ],
        default=DEFAULTS["applied_volumes"],
    )
    applied_folder_name: StringProperty(
        name="Folder Name",
        description="Name of the folder made next to the .blend file",
        default=DEFAULTS["applied_folder_name"],
    )
    applied_folder: StringProperty(
        name="Folder",
        description="Where applied volumes are written",
        default=DEFAULTS["applied_folder"], subtype="DIR_PATH",
    )
    relative_volume_paths: BoolProperty(
        name="Relative Paths",
        description="Store the path of an applied volume relative to the .blend file (when it is saved)",
        default=DEFAULTS["relative_volume_paths"],
    )

    # Shelf tab
    folders: CollectionProperty(type=HoudiniBridgeFolder)
    include_packages: BoolProperty(
        name="Houdini Packages",
        description="SOP HDAs loaded by your Houdini packages (SideFX Labs…)",
        default=DEFAULTS["include_packages"],
    )
    include_native: BoolProperty(
        name="Houdini Nodes",
        description="Houdini's own SOP nodes (PolyReduce, Boolean, Remesh…)",
        default=DEFAULTS["include_native"],
    )
    all_versions: BoolProperty(
        name="Every Version",
        description="List every version of a tool (SideFX Labs ships several); off: the latest only, "
        "like Houdini's Tab menu. Takes effect at the next refresh",
        default=DEFAULTS["all_versions"],
    )
    hidden_tools: StringProperty(
        name="Hide",
        description="Tools left out of the shelf, the asset shelf and the search: names or types, with * as a "
        "wildcard, separated by commas (e.g. *deprecated*, labs::ml_*)",
        default=DEFAULTS["hidden_tools"], update=_rebuild_library,
    )
    strip_labs_prefix: BoolProperty(
        name="Short Labs Names",
        description='Drop the "Labs" every SideFX Labs tool name starts with',
        default=DEFAULTS["strip_labs_prefix"],
        update=_rebuild_library,
    )
    group_by_category: BoolProperty(
        name="Group by Category",
        description="The sidebar list shows the category of the tools above them",
        default=DEFAULTS["group_by_category"],
    )
    search_help: BoolProperty(
        name="Search Help Text",
        description="The search also looks through the one-line help of each tool",
        default=DEFAULTS["search_help"],
    )
    refresh_on_start: BoolProperty(
        name="Update When Houdini Starts",
        description="Read the tools again the first time Houdini starts in a Blender session, "
        "to pick up new or changed HDAs",
        default=DEFAULTS["refresh_on_start"],
    )
    catalog_depth: IntProperty(
        name="Category Levels",
        description="Depth of the category tree in the asset shelf (Labs / World Building / Prop: 1, 2 or 3 levels). "
        "Deeper categories are merged into their parent",
        default=DEFAULTS["catalog_depth"], min=1, max=3,
        update=_rebuild_library,
    )

    # Houdini tab
    hython_path: StringProperty(
        name="hython",
        description="The hython executable to run; empty: the most recent installed Houdini",
        default=DEFAULTS["hython_path"], subtype="FILE_PATH",
    )
    skip_packages: BoolProperty(
        name="Skip Houdini Packages",
        description="Start hython with HOUDINI_PACKAGE_SKIP=1 (faster startup, "
        "but HDAs that depend on a package will not cook)",
        default=DEFAULTS["skip_packages"],
    )
    start_with_blender: BoolProperty(
        name="Start With Blender",
        description="Start Houdini in the background when Blender starts or opens a file with Houdini modifiers, "
        "so the first cook does not wait for it",
        default=DEFAULTS["start_with_blender"],
    )
    idle_minutes: IntProperty(
        name="Stop When Idle",
        description="Stop Houdini after this many minutes without a cook, to free the license it holds while it "
        "runs; it starts again on the next cook. 0: never",
        default=DEFAULTS["idle_minutes"], min=0, soft_max=120,
    )
    startup_timeout: IntProperty(
        name="Startup Timeout",
        description="Seconds to wait for Houdini to start before giving up (a cold start with many packages "
        "can take a minute)",
        default=DEFAULTS["startup_timeout"], min=10, max=900,
    )

    # Viewport tab
    show_feedback: BoolProperty(
        name="Cooking Feedback",
        description="Outline and elapsed time on the objects Houdini is cooking; red badge on errors",
        default=DEFAULTS["show_feedback"],
        update=_redraw,
    )
    show_indicator: BoolProperty(
        name="Connection Indicator",
        description="The Houdini Bridge button in the 3D Viewport header, its colored dot showing Houdini stopped, "
                    "starting, ready, cooking, failed",
        default=DEFAULTS["show_indicator"],
        update=_redraw,
    )
    show_legend: BoolProperty(
        name="Visualizer Legend",
        description="The badge naming what Visualize shows, with its range or count",
        default=DEFAULTS["show_legend"],
        update=_redraw,
    )

    def draw(self, context):
        layout = self.layout
        layout.row().prop(self, "tab", expand=True)
        getattr(self, "_draw_" + self.tab.lower())(layout.column())

    def _section(self, layout, name, title, icon, closed=False):
        """A collapsible section; returns its body, or None when folded."""
        header, body = layout.panel("houdini_bridge_preferences_" + name, default_closed=closed)
        header.label(text=title, icon=icon)
        if body is not None:
            body.use_property_split = True
            body.use_property_decorate = False
        return body

    def _draw_modifiers(self, layout):
        body = self._section(layout, "new", "New Modifiers", "ADD")
        if body:
            body.row().prop(self, "default_target", expand=True)
            body.row().prop(self, "default_output", expand=True)
            body.prop(self, "live_default")
            body.prop(self, "generator_at_cursor")
        body = self._section(layout, "parameters", "Parameters", "PROPERTIES")
        if body:
            body.prop(self, "parameter_limits")
            column = body.column(heading="Drawn by")
            column.prop(self, "houdini_interface")
            column = body.column(heading="Folders")
            column.prop(self, "folders_open")
            column = body.column(heading="Hidden Parameters")
            column.prop(self, "follow_hide_when")
        body = self._section(layout, "cooking", "Cooking", "FILE_REFRESH")
        if body:
            body.row().prop(self, "edit_mode_cook", expand=True)
            column = body.column(heading="Bring Back")
            column.prop(self, "bring_back_extras")

    def _draw_output(self, layout):
        body = self._section(layout, "instances", "Instances", "OUTLINER_OB_GROUP_INSTANCE")
        if body:
            body.prop(self, "points_above")
            body.prop(self, "sphere_frequency")
        body = self._section(layout, "curves", "Curves and Points", "OUTLINER_OB_CURVES")
        if body:
            column = body.column(align=True)
            column.prop(self, "curve_radius")
            column.prop(self, "point_radius")
        body = self._section(layout, "volumes", "Volumes", "OUTLINER_OB_VOLUME")
        if body:
            row = body.row(align=True)
            row.prop(self, "live_volume_folder")
            row.operator("houdini_bridge.open_folder", text="", icon="FOLDER_REDIRECT").path = streams.volume_dir()
            body.separator()
            body.prop(self, "applied_volumes")
            if self.applied_volumes == "BESIDE":
                body.prop(self, "applied_folder_name")
                body.prop(self, "relative_volume_paths")
            elif self.applied_volumes == "FOLDER":
                body.prop(self, "applied_folder")
                body.prop(self, "relative_volume_paths")
            hint = body.row()
            hint.alignment = "RIGHT"
            hint.label(text=_applied_volume_hint(self), icon="INFO")

    def _draw_shelf(self, layout):
        body = self._section(layout, "sources", "Sources", "FILE_FOLDER")
        if body:
            body.use_property_split = False
            body.label(text="HDA Folders (subfolders included)")
            column = body.column(align=True)
            for index, folder in enumerate(self.folders):
                row = column.row(align=True)
                row.prop(folder, "enabled", text="")
                row.prop(folder, "path", text="")
                remove = row.operator("houdini_bridge.folder", text="", icon="X")
                remove.action = "REMOVE"
                remove.index = index
            body.operator("houdini_bridge.folder", text="Add Folder…", icon="ADD").action = "ADD"
            row = body.row(heading="Also List")
            row.use_property_split = True
            row.prop(self, "include_packages")
            row.prop(self, "include_native")
            column = body.column()
            column.use_property_split = True
            column.prop(self, "all_versions")
        body = self._section(layout, "listing", "Listing", "VIEWZOOM")
        if body:
            body.prop(self, "hidden_tools", icon="HIDE_ON")
            column = body.column(heading="Sidebar")
            column.prop(self, "group_by_category")
            column.prop(self, "strip_labs_prefix")
            column.prop(self, "search_help")
            body.prop(self, "catalog_depth", text="Asset Shelf Levels")
        body = self._section(layout, "catalog", "Catalog", "ASSET_MANAGER")
        if body:
            body.prop(self, "refresh_on_start")
            row = body.row()
            count = len(catalog.state.tools)
            if catalog.state.refreshing:
                row.label(text="Houdini is reading the tools…", icon="TIME")
            elif count:
                read = time.strftime("%Y-%m-%d %H:%M", time.localtime(catalog.state.date))
                listed = len(catalog.listed_tools())
                text = "%d tools, read %s" % (count, read)
                if listed != count:
                    text += " (%d hidden)" % (count - listed)
                row.label(text=text, icon="CHECKMARK")
            else:
                row.label(text="The shelf has not been read yet", icon="INFO")
            row.operator("houdini_bridge.refresh_shelf", text="Refresh", icon="FILE_REFRESH")

    def _draw_houdini(self, layout):
        body = self._section(layout, "version", "Version", "NODETREE")
        if body:
            body.use_property_split = False
            installations = hython.find_installations()
            running = link.server.running_hython()
            chosen = os.path.normcase(bpy.path.abspath(self.hython_path)) if self.hython_path else ""
            latest = installations[0][1] if installations else ""
            box = body.box()
            column = box.column(align=True)

            def choice(text, path, selected, detail):
                split = column.split(factor=0.4, align=True)
                split.operator("houdini_bridge.use_houdini", text=text, depress=selected,
                               icon="RADIOBUT_ON" if selected else "RADIOBUT_OFF").path = path
                row = split.row()
                row.enabled = False
                row.label(text=detail)

            choice("Automatic", "", not chosen,
                   "the latest installed: %s" % installations[0][0] if installations else "no Houdini found")
            for version, path in installations:
                is_running = running is not None and os.path.normcase(running) == os.path.normcase(path)
                choice("Houdini %s%s" % (version, "  (running)" if is_running else ""), path,
                       chosen == os.path.normcase(path), os.path.dirname(os.path.dirname(path)))
            known = {os.path.normcase(path) for _, path in installations}
            if chosen and chosen not in known:
                choice("Other", self.hython_path, True, self.hython_path)
            split = body.split(factor=0.25)
            split.label(text="Other hython")
            split.prop(self, "hython_path", text="")
            if running and os.path.normcase(running) != os.path.normcase(bpy.path.abspath(self.hython_path) or latest):
                warning = body.row()
                warning.alert = True
                warning.label(text="Restart Houdini to use the chosen version", icon="ERROR")
                warning.operator("houdini_bridge.server", text="Restart", icon="FILE_REFRESH").action = "RESTART"
        body = self._section(layout, "session", "Session", "TIME")
        if body:
            body.prop(self, "start_with_blender")
            row = body.row(align=True)
            row.prop(self, "idle_minutes", text="Stop When Idle (min)")
            body.prop(self, "startup_timeout", text="Startup Timeout (s)")
            body.prop(self, "skip_packages")
        body = self._section(layout, "status", "Status", "INFO")
        if body:
            body.use_property_split = False
            text, icon = link.server.status()
            body.label(text=text, icon=icon)
            row = body.row(align=True)
            row.operator("houdini_bridge.server", text="Start", icon="PLAY").action = "START"
            row.operator("houdini_bridge.server", text="Restart", icon="FILE_REFRESH").action = "RESTART"
            row.operator("houdini_bridge.server", text="Stop", icon="X").action = "STOP"
            row.operator("houdini_bridge.server_log", icon="TEXT")

    def _draw_viewport(self, layout):
        body = self._section(layout, "overlays", "Overlays", "OVERLAY")
        if body:
            column = body.column(heading="Show")
            column.prop(self, "show_feedback")
            column.prop(self, "show_legend")
            column.prop(self, "show_indicator")

    def _draw_hotkeys(self, layout):
        user_config = bpy.context.window_manager.keyconfigs.user
        box = layout.box()
        for label, keymap, item in assets.hotkeys():
            box.label(text=label)
            rna_keymap_ui.draw_kmi([], user_config, keymap, item, box, 0)


def _applied_volume_hint(preferences):
    if preferences.applied_volumes == "FOLDER":
        folder = bpy.path.abspath(preferences.applied_folder) if preferences.applied_folder else ""
        return ("Written to %s" % folder) if folder else "Set a folder (until then: next to the .blend)"
    if bpy.data.filepath:
        return "Written to %s" % os.path.join(os.path.dirname(bpy.data.filepath), preferences.applied_folder_name)
    return "This file is not saved yet: applied volumes move next to it when it is saved"


class HOUDINI_BRIDGE_OT_use_houdini(bpy.types.Operator):
    """Run this Houdini version (Houdini restarts when it is running)"""

    bl_idname = "houdini_bridge.use_houdini"
    bl_label = "Use This Houdini"

    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        preferences = get()
        if preferences is None:
            return {"CANCELLED"}
        preferences.hython_path = self.path
        context.preferences.is_dirty = True
        if link.server.running_hython() is not None:
            bpy.ops.houdini_bridge.server(action="RESTART")
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_open_folder(bpy.types.Operator):
    """Open this folder in the file browser of the system"""

    bl_idname = "houdini_bridge.open_folder"
    bl_label = "Open Folder"

    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        os.makedirs(self.path, exist_ok=True)
        bpy.ops.wm.path_open(filepath=self.path)
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_open_preferences(bpy.types.Operator):
    """Open the RG Houdini Bridge preferences (shelf sources, Houdini, hotkeys)"""

    bl_idname = "houdini_bridge.open_preferences"
    bl_label = "RG Houdini Bridge Preferences"

    def execute(self, context):
        bpy.ops.screen.userpref_show()
        context.preferences.active_section = "EXTENSIONS"
        bpy.ops.preferences.addon_show(module=paths.PACKAGE)
        return {"FINISHED"}


CLASSES = (HoudiniBridgeFolder, HoudiniBridgePreferences, HOUDINI_BRIDGE_OT_use_houdini, HOUDINI_BRIDGE_OT_open_folder,
           HOUDINI_BRIDGE_OT_open_preferences)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
