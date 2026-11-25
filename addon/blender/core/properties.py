"""Data stored in the .blend file: which HDA a Houdini modifier runs, its inputs, its parameters.

The settings live on the node group of each Houdini modifier. The parameter
values themselves are inputs of that node group (see parameters/); here
each parameter only keeps its description.
"""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from . import cook, events, feedback, stored, streams, targets


def _on_input_update(self, context):
    cook.cooks.input_changed(self.id_data, self)


def _on_live(self, context):
    """Once the lock is lifted, cook again as Houdini does (nothing is sent when nothing changed
    meanwhile)."""
    if self.live and self.enabled:
        cook.cooks.request(self.id_data)


def _on_output_mode(self, context):
    if self.live:
        cook.cooks.request(self.id_data, force=True)


def _on_use_cache(self, context):
    events.setting_changed.emit(self, "use_cache")


def _on_instance_display(self, context):
    streams.apply_instance_display(self)


def _is_other_input(self, obj):
    """Any mesh or Volume object, except a hidden result or the object carrying this modifier."""
    if obj.type not in targets.INPUT_TYPES or obj.houdini_bridge_result:
        return False
    return not any(modifier.type == "NODES" and modifier.node_group == self.id_data for modifier in obj.modifiers)


# Blender requires the strings of a dynamic enum to stay alive: the items of the last
# lists asked for are kept (one per result shown), the oldest dropped beyond LIVE_ITEMS_KEPT.
_live_items = {}
LIVE_ITEMS_KEPT = 32


def outputs(settings):
    """The groups and attributes of the last result: [{name, kind, domain, size, type}]."""
    return stored.parse(settings.outputs_json, [])


def _redraw_viewports(self, context):
    """The visualizer and group settings live on a node group, which the viewport does not watch."""
    feedback.redraw()


DOMAIN_LABELS = {"POINT": "points", "EDGE": "edges", "FACE": "faces", "CORNER": "corners"}


def _visualize_items(self, context):
    key = self.outputs_json
    items = _live_items.pop(key, None)
    if items is None:
        items = [("NONE", "Nothing", "No visualization", "HIDE_ON", 0)]
        attributes = [output for output in outputs(self) if output["kind"] != "group"]
        for index, output in enumerate(attributes, start=1):
            label = "%s (%s, %d)" % (output["name"], DOMAIN_LABELS.get(output["domain"], ""), output["size"])
            icon = "ORIENTATION_NORMAL" if output["size"] == 3 else "COLOR"
            items.append((output["name"], label, "", icon, index))
    _live_items[key] = items  # the most recent last
    while len(_live_items) > LIVE_ITEMS_KEPT:
        del _live_items[next(iter(_live_items))]
    return items


class HoudiniBridgeResultGroup(bpy.types.PropertyGroup):
    """A group of the last result. `name` (built in) is its Houdini name."""

    attribute: StringProperty(description="Boolean attribute of the result holding the group")
    domain: StringProperty()
    count: IntProperty()
    color: FloatVectorProperty(name="Color", subtype="COLOR", size=3, min=0.0, max=1.0, update=_redraw_viewports)
    show: BoolProperty(name="Show", description="Show the group in the viewport", default=True,
                       update=_redraw_viewports)


class HoudiniBridgeParm(bpy.types.PropertyGroup):
    """One HDA parameter. `name` (built in) holds the Houdini parameter name."""

    label: StringProperty()
    folder: StringProperty(description="Folder path, labels separated by tabs (nested panels)")
    folder_types: StringProperty(description="The type, tab set and Houdini name of each folder of the path "
                                 "(JSON: [type, tab set, name] per level, contract.FOLDER_TYPES), refreshed at "
                                 "every cook; in a file saved before 1.0, empty (collapsible folders) or "
                                 "[type, tab set] until its first cook")
    help: StringProperty()
    kind: StringProperty()  # one of contract.PARM_KINDS (what each does: parameters/kinds.py)
    size: IntProperty(default=1)
    is_color: BoolProperty()
    is_file: BoolProperty()  # a file path (not drawn differently yet)
    editor: StringProperty(description="The language of a code parameter (vex, python... or text), edited in "
                           "the Text Editor, refreshed at every cook; empty for any other parameter")
    hidden: BoolProperty()
    disabled: BoolProperty()  # written at every cook (Houdini's disable-when): the panel draws it faded
    expression: BoolProperty(description="Houdini drives it with keyframes or an expression (a channel reference "
                             "included), refreshed at every cook: its value is not sent until it changes here")
    menu_json: StringProperty()  # menu items, or the suggestions of a group field
    toggle: BoolProperty(description="Suggestions add up (a group field takes several groups)")
    group_selection: StringProperty(description="How the viewport selects this group field (JSON: input, types, "
                                    "type parameter), empty when it cannot be selected there")
    socket: StringProperty(description="Identifier of the node group input showing this parameter")
    multiparm: BoolProperty(description="The number of instances of a multiparm: changing it rebuilds the inputs")
    # Ramps are not inputs: a Float Curve or Color Ramp node of the node group holds them.
    ramp: StringProperty(description="float or color")
    node: StringProperty(description="Name of the node holding a ramp")
    ramp_basis: StringProperty(description="Houdini interpolation of each key at the last build (JSON), "
                               "kept when Blender's own interpolation still matches")
    # The Houdini range (see parameters.limits for which bounds become limits).
    has_range: BoolProperty()
    range_min: FloatProperty()
    range_max: FloatProperty()
    min_strict: BoolProperty(description="The minimum cannot be crossed in Houdini either")
    max_strict: BoolProperty(description="The maximum cannot be crossed in Houdini either")
    min_fits: BoolProperty(description="The default value is not below the range")
    max_fits: BoolProperty(description="The default value is not above the range")


class HoudiniBridgeInput(bpy.types.PropertyGroup):
    label: StringProperty()
    object: PointerProperty(type=bpy.types.Object, poll=_is_other_input, update=_on_input_update)
    # Input 1 of a modifier on an existing object: the geometry the modifier receives.
    is_self: BoolProperty()


class HoudiniBridgeSettings(bpy.types.PropertyGroup):
    """The HDA run by a Houdini modifier (stored on its node group)."""

    enabled: BoolProperty()
    holder: PointerProperty(type=bpy.types.Object, description="Hidden object holding the cooked mesh")
    path: StringProperty(name="HDA file", subtype="FILE_PATH", options={"PATH_SUPPORTS_BLEND_RELATIVE"})
    node_type: StringProperty(name="Type")
    label: StringProperty()
    live: BoolProperty(
        name="Live",
        description="Recook as soon as a parameter or an input changes. Off, the modifier is locked, as "
        "Houdini's lock flag: it keeps its result and cooks only on Recook",
        default=True,
        update=_on_live,
    )
    inputs: CollectionProperty(type=HoudiniBridgeInput)
    parms: CollectionProperty(type=HoudiniBridgeParm)
    structure: StringProperty()
    status: StringProperty()
    errors: StringProperty()
    timings: StringProperty()
    time_dependent: BoolProperty(description="The HDA reads the time ($F, $T, keyframes): it recooks on frame change")
    use_cache: BoolProperty(
        name="Use Cache",
        description="Play the cached frames instead of cooking live (playback and rendering are frame accurate)",
        update=_on_use_cache,
    )
    cache_scene_range: BoolProperty(name="Scene Range", description="Cache the scene's frame range", default=True)
    cache_start: IntProperty(name="Start", description="First frame to cache", default=1)
    cache_end: IntProperty(name="End", description="Last frame to cache", default=100)
    cache_collection: PointerProperty(type=bpy.types.Collection, description="The cached frames, one object each")
    cache_first: IntProperty(description="Frame of the first cached object")
    cache_parms: StringProperty(description="Parameter values when the cache was made (JSON)")
    output_mode: EnumProperty(
        name="Output",
        items=[
            ("NATIVE", "Native", "Instances, open curves, points and volumes come back as Blender instances, "
             "curves, point clouds and volumes, next to the mesh"),
            ("MESH", "Mesh", "Everything is realized as one mesh (packed primitives unpacked, open curves dropped): "
             "an editable result, and all of it reaches a Houdini modifier below"),
        ],
        default="NATIVE",
        update=_on_output_mode,
    )
    instance_display: EnumProperty(
        name="Instances in Viewport",
        items=[
            ("AUTO", "Auto", "Instances up to the count set by the Points Beyond preference, points beyond it "
             "(the render always gets the instances)"),
            ("INSTANCES", "Instances", "Draw every instance in the viewport"),
            ("POINTS", "Points", "Draw one point per instance in the viewport; the render gets the instances"),
        ],
        default="AUTO",
        update=_on_instance_display,
    )
    instances_holder: PointerProperty(type=bpy.types.Object, description="Hidden point cloud of the instances")
    prototypes: PointerProperty(type=bpy.types.Collection, description="Shapes of the instances, one object each")
    curves_holder: PointerProperty(type=bpy.types.Object, description="Hidden curves object of the open curves")
    points_holder: PointerProperty(type=bpy.types.Object, description="Hidden point cloud of the loose points")
    volume_holder: PointerProperty(type=bpy.types.Object, description="Hidden volume object of the VDB grids")
    streams_json: StringProperty(description="What the last result holds besides the mesh (JSON)")
    outputs_json: StringProperty(description="Groups and attributes of the result (JSON)")
    result_groups: CollectionProperty(type=HoudiniBridgeResultGroup)
    result_group_index: IntProperty()
    visualize: EnumProperty(
        name="Visualize",
        description="Show an attribute of the Houdini result in the viewport",
        items=_visualize_items,
        update=_redraw_viewports,
    )
    template: BoolProperty(
        name="Template",
        description="Draw this HDA's result as a ghost wireframe in the viewport, as Houdini's template "
        "flag, while the stack shows as usual",
        update=_redraw_viewports,
    )


CLASSES = (HoudiniBridgeResultGroup, HoudiniBridgeParm, HoudiniBridgeInput, HoudiniBridgeSettings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.GeometryNodeTree.houdini_bridge = PointerProperty(type=HoudiniBridgeSettings)
    bpy.types.Object.houdini_bridge_result = BoolProperty(
        description="This object is the hidden result of a Houdini modifier",
    )
    # The modifiers hidden from the viewport for a while, and how to put them back (JSON, see
    # core/stack_cuts.py): kept in the data, so that an undo or a saved file keeps it.
    bpy.types.Object.houdini_bridge_cuts = StringProperty(options={"HIDDEN"})


def unregister():
    del bpy.types.Object.houdini_bridge_cuts
    del bpy.types.Object.houdini_bridge_result
    del bpy.types.GeometryNodeTree.houdini_bridge
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
