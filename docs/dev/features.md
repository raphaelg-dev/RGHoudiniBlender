# RG Houdini Bridge: features

The features one by one: what each does inside, and where. Part of the developer documentation: start with [DEVELOPMENT.md](../../DEVELOPMENT.md).

- [Shelf and asset library](#shelf-and-asset-library)
- [Groups and attributes](#groups-and-attributes)
- [Selecting a group in the viewport](#selecting-a-group-in-the-viewport)
- [Viewport drawing](#viewport-drawing)
- [Bypass, Lock and the modifier panel](#bypass-lock-and-the-modifier-panel)
- [Template and Display](#template-and-display)
- [Timeline](#timeline)
- [Cache](#cache)
- [Native streams](#native-streams)
- [Chained Houdini modifiers](#chained-houdini-modifiers)
- [Apply](#apply)
- [Preferences](#preferences)

## Shelf and asset library

**Sources** are the HDA folders of the preferences (subfolders included, `backup/` skipped), the SOP HDAs loaded by Houdini packages minus the ones shipped with Houdini, and Houdini's own SOP nodes. `$HFS` is compared on long paths, because Houdini reports some library paths as `C:/PROGRA~1/…`.

**The catalog** is built by hython (`houdini/catalog.py`, `catalog` command): label, group, summary (the `"""…"""` line of the `Help` section), input counts, and a 128 px PNG icon rendered by off-screen Qt (`QT_QPA_PLATFORM=offscreen`).

- Group: the folder, or the `toolSubmenu` of the `Tools.shelf` section. For Houdini nodes, the Tab menu location when a shelf tool declares one (`hou.shelves.tools()`), else `Houdini`.
- Icon sources, in order: the HDA section named by its icon (`opdef:…`), a file in a package's `config/Icons`, or a Houdini icon name resolved through `IconMapping` then `icons.zip`. The fallback is `COMMON/subnet.svg`.
- Houdini nodes are every visible, non-deprecated SOP node type, compiled or shipped as an HDA in `$HFS`. They have no file: `path` is empty and the server skips the install.
- One entry per tool (`houdini.catalog.latest_versions`, from `hou.hda.componentsFromFullNodeTypeName`), since SideFX Labs ships several versions of many tools as distinct node types. Favorites on another version follow the version kept. The *Every Version* preference skips this, and names then get their version.

**Cache**: the catalog is kept in the extension's user folder (`catalog.json`, `favorites.json`, `icons/`), so the shelf shows without starting hython. `events.server_started` is emitted on the main thread each time a new Houdini session answers, and the shelf, which listens to it, rebuilds its catalog at the first start of a Blender session, unless the preference is off. Otherwise only Refresh rebuilds it.

**Asset library**: `assets.py` writes the catalog as an asset library after every refresh (it listens to `catalog.refreshed`), in the extension's user folder (`library/houdini_bridge_hdas.blend`, `blender_assets.cats.txt`, `index.json`), and registers it as *Houdini Bridge HDAs*.

- One Empty object per tool, marked as an asset. Preview: the HDA icon, with pixels copied from the PNG. Catalog: the tool group, with `:` replaced since it is the separator of the catalog file. Custom properties on `asset_data` hold the file, type, label and input count.
- The objects are made in `bpy.data.temp_data()`, so their names cannot clash with objects of the open file and nothing is left in it. Two tools with one label get their group in parentheses, and `index.json` maps every asset name to its tool.
- `HOUDINI_BRIDGE_AST_hdas` is the AssetShelf, with `bl_activate_operator` and `bl_drag_operator`. Blender passes the asset through `asset_library_type`, `asset_library_identifier` and `relative_asset_identifier` properties, which the operators must declare. The asset name is the last part of the relative identifier.
- The drop operator ray-casts under the mouse: a mesh hit gets the HDA as a modifier, empty space gets a new object there.
- Blender unfolds the whole catalog tree of the asset shelf popup and offers no way to fold it, so catalogs are cut to the *Asset Shelf Levels* depth. Deeper groups merge into their parent. Changing it rewrites the library from the cached catalog.
- Hotkeys sit in the add-on keyconfig (3D View): `wm.call_asset_shelf_popover` on Ctrl+Shift+H and `wm.call_menu_pie` on Shift+Alt+H (7 favorites plus the whole shelf). Both are free in a stock keymap. The preferences draw them with `rna_keymap_ui.draw_kmi`, from the user keyconfig copies of the add-on keymap items.

**Sidebar shelf** (the Shelf tab of `HOUDINI_BRIDGE_PT_sidebar`, drawn by `catalog.draw_tab`): without a search it lists the favorites, and a search looks through every tool. In the asset shelf, the right click menu comes from `AssetShelf.draw_context_menu`.

**Version updates**: a modifier whose HDA has a newer version in the shelf shows an *Update* button (`houdini_bridge.upgrade`). The node type changes, the server rebuilds the instance, and the inputs are rebuilt, keeping values by parameter name.

## Groups and attributes

**Blender to Houdini groups** (`mesh_io.read_groups`): every boolean attribute on points, edges or faces, with `sharp_face` excluded. That covers the Groups panel, the groups brought back by a Houdini modifier above (which is how a group flows down the stack) and Geometry Nodes selections. They travel with the topology, so a changed group means a full send, as `input_groups` plus index blocks, edges as point pairs. The server rebuilds them with `createPointGroup`, `createPrimGroup` and `createEdgeGroup` plus `findEdge`, under a valid Houdini name.

**Blender to Houdini attributes** (`mesh_io.read_attributes`): every non-boolean attribute of points, faces and corners, sent as `input_attributes` `[name, class, type, size, block, qualifier]`.

- Skipped: names starting with `.`, `position`, `material_index`, and the active UV map (sent as `uv`).
- The active color becomes `Cd`, plus `Alpha` when not opaque. Custom normals (`has_custom_normals`, read from `corner_normals`) become `N`. Other UV maps are 3-component `texturecoord`.
- Vectors are transformed by the input matrix and change axes like P, normals by the inverse transpose.
- Vertex groups are not attributes: their weights are read vertex by vertex, with no `foreach`, and sent as point floats.
- On the Houdini side, names are cleaned like groups (two names that clean the same within a class: the second gets `_2`), and the qualifier is set with `attrib.setOption("type", …)`.

**The light path**: the topology digest covers the attribute layout and whether a UV map exists. The content digest covers P, uv and every attribute value. The `positions` state carries all of them (`meta["values"]`), and the server rewrites them in place (`Instance.update_input`).

**Houdini to Blender** (`houdini.geometry._export_extras`, `mesh_io.write_extras`): every numeric point, primitive or vertex attribute of 1 to 4 components, and every point, primitive or edge group. The server reads them with `geometry.numeric_attributes` and the material paths with `geometry.material_paths`, for the mesh as for the streams; Blender converts them with one function (`_converted` in `mesh_io/write.py`) and finds the materials by name with `mesh_io.set_materials`, for the mesh as for the streams.

- Attributes keep name, domain and size. Groups become BOOLEAN attributes, with edges matched by sorted vertex pairs.
- A 3-component float attribute is converted like P unless its qualifier is `Color`. VEX attributes have no qualifier.
- Names Blender owns (`sharp_edge`, `position`…) get a `houdini_` prefix.
- Attributes of a previous cook that are gone are removed (`mesh["houdini_bridge_extras"]`). Their description is kept in `settings.outputs_json`.
- The evaluated result takes its materials from the holder's mesh, through the Object Info node, and never from the owner's slots.
  - So when Houdini names no material, `mesh_io.write_mesh` gives the holder the owner's materials (`Target.own_materials`, a mesh owner only) and marks it (`mesh["houdini_bridge_follows_owner"]`, saved in the file); `cook/result.follow_owner_materials` copies them again when the depsgraph handler sees the owner change, which a material assigned to it does, so it shows without a cook, on a locked modifier too.
  - The cached frames do the same. Materials Houdini names replace them, as before.
- Round trips stay stable (`_blender_type` in `mesh_io/write.py`): a Houdini `Color` attribute of 3 or 4 components comes back as FLOAT_COLOR and goes back as RGB when its alpha is 1, a `Texture Coord` one as FLOAT2 (a UV map on corners), and 4 floats as FLOAT4, so an `orient` quaternion stays intact.

**Groups panel** (`groups.py`): groups are plain boolean attributes named like the group. `Mesh.houdini_bridge_groups` only adds a color and a show toggle. Renaming renames the attribute, in Edit Mode through a new bmesh layer. After Apply, the Houdini groups join the list.

**Groups of a result**: an empty group stays in Houdini, where a node may still name it, but `mesh_io.write_extras` skips it and the string kind of `houdini/parms.py` takes it out of the suggestions. `settings.result_groups` lists the groups of the last result, refreshed by `groups.sync_result_groups` after each cook (it listens to `events.result_applied`) and keeping colors and visibility.

Groups already in the mesh's list are left out, and so is `sharp_edge`, which travels to Houdini on purpose so that an HDA can read the sharp edges. `draw_groups` adds those of the active modifier (`groups.active_result`) to the mesh's groups.

**Group preview**: in Object Mode with a Houdini modifier active, each group is previewed on that modifier's holder (`groups.preview_stage`). In Edit Mode, it is previewed on the mesh being edited. Previews are lifted along the normals to avoid z-fighting.

## Selecting a group in the viewport

In Houdini, the arrow next to a group field enters a selection in the viewport, and Enter writes the elements picked into the field (`0-11 15 20-22`). `blender/features/selection/` does the same, from an arrow the feature draws next to the field itself (`selection.draw_arrow`, which the row of a group field calls in `ui/parameter_rows.py`): a fork without the feature draws no arrow.

- **What the field takes** comes from the server.
  - Its template's `script_action` tag is read without running it (`script_action.read`, with `ast`): `soputils.selectGroupParm` must be called; `kwargs['geometrytype']` gives fixed types (`hou.geometryType.Primitives`, or a tuple) or a menu parameter (`kwargs['node'].parmTuple('grouptype')`, or one of a node inside the HDA, followed to the HDA parameter it references, else its value is sent); `kwargs['inputindex']` the input, -n meaning input n when connected, else the first.
  - A script that sets `kwargs['node']` to another node selects a geometry inside the HDA: no arrow. A type chosen in an `if` is unknown. `parms.py` sends the result as `group_input`, `group_types` and `group_type_parm`, and the string kind stores it in `HoudiniBridgeParm.group_selection` (JSON) at every cook.
- **The type** follows `soputils`: a menu token gives it through `contract.GROUP_TYPE_TOKENS` (Guess gives none: the current selection mode stays); a field taking edges and another type starts in edges when its text is an edge string. Points are vertex mode, primitives face mode, edges edge mode; Houdini's vertices (corners) and breakpoints cannot be selected. On Enter, a menu giving the type takes the type selected, as Houdini sets it.
- **Numbers**: `mesh_io/read.py` sends the vertices in their order as points and the faces in their order as polygons (`geometry.import_mesh` creates them in that order), so a Blender index is the Houdini number. Edges are paths of point numbers (`p0-1-2 p5-6`), as `hou.Selection.selectionString` writes them and `hou.Geometry.globEdges` reads them. `group_string.py` reads the numbers, the ranges (backwards and with a step too) and the edge paths a field holds, to preselect them, and writes a selection compact; anything else (a group name, `@P.y>0`) preselects nothing and stays until Enter.
- **What is shown** (`session.begin`):
  - the modifier and every modifier below it are hidden from the viewport (a cut of the stack, `core/stack_cuts.py`, shared with the Display flag: see [Template and Display](#template-and-display)), and the Houdini ones among them paused (`cook.cooks.pause`: their cooks are dropped, the inputs that change are still marked).
  - An input that is the object itself with no modifier above, or another object showing no modifier, goes into Edit Mode itself. Otherwise a temporary object holds `bpy.data.meshes.new_from_object` of the evaluated source, the mesh a cook reads (`mesh_io.read_object`), and the source is hidden; the badge says that edits of its geometry are not kept.
  - The copy is left out of renders (`hide_render`), and joins the local view of each 3D view that shows the source in one.
- **What it refuses**:
  - a second selection, a field Blender cannot select, an empty input or one that is not a mesh, a cache being made (`cook.cooks.caching`: its job sets frames and reads the stack meanwhile), and input 1 chained on the Houdini modifier above (`cook.chained_above`) when that modifier's result is renumbered (`cook.sent.is_renumbered`): the server then reads the whole Houdini result through an Object Merge, while Blender shows the mesh it received, without the instances, curves, volumes or loose points the mesh path left out, so the numbers would not match.
  - A chain of polygons only (a rock, then a Group Create) selects as usual.
- **Ending it** (`session.finish`):
  - Enter or Esc (`mode.py`, which passes every other event to Blender).
  - `restore.revert` restores what the session's `restore.Record` names: Edit Mode left, the temporary object and mesh removed, the selection flags of a mesh edited in place (when its element counts are unchanged), the objects and modifiers hidden or shown, the selection mode, the selected and active objects and the mode; the paused modifiers resume, which cooks them, except a locked one (`cook.cooks.resume` asks for no cook then).
  - The record holds names, never Blender structs.
  - The session is cancelled when its object leaves Edit Mode or another object becomes active (checked at each event and on `undo_post`), when an undo takes the data back to before it, before a file is saved (`save_pre`: nothing of it is ever written by a save, and the operator says so at the next event; autosave waits while a modal operator runs) and when the extension is disabled; a file being loaded drops it (`load_pre`).
- **Undo**:
  - Enter pushes one undo step once the field is written (`restore.push_undo`); Python's own calls to `bpy.ops` push none. The clicks of the user push Edit Mode steps, and before the first one Blender keeps a copy of the data as it is then, in the middle of the selection.
  - An undo or a redo after the selection can therefore bring back that data, and so can an autosave or a recovered file: the record is also stored on the scene while the selection runs (`restore.MARKER`), and `restore.repair` (on `undo_post`, `redo_post` and `load_post`) restores what it names.
  - A removed temporary object needs an undo step right after it: Blender decodes an Edit Mode step of the copy on the data it last loaded, and crashes when the object is gone (`ED_undo_object_editmode_restore_helper`); a newer step makes it load the data holding the copy first. So a cancel that removed a copy, and a repair that removed one, push a step too, which drops the redo steps.
- **Drawing**: every 3D view gets a Houdini-orange border and the badge of `viewport/overlay.py` (`overlay.badge`, the declared exception of the layers), with the field, the node, the elements of the current mode and the keys; the status bar says the same.

## Viewport drawing

- **Visualizers** (`visualize.py`, drawn from the overlay) read the holder mesh, or the cached frame being played. A scalar or 2 to 4 component attribute is a blue to red ramp over the 2nd to 98th percentiles, and a 3-component point or face attribute is arrows. The drawing is cached per cook. The setting redraws the viewports itself, since it lives on a node group the viewport does not watch.
- **HDA viewport visualizers** (`hou.ViewportVisualizer`) cannot be read: `hou.viewportVisualizers` does not exist in hython, HDAs carry no section for them, and the geometry has no attribute for them.
- **Badges** (`overlay.py`): every viewport badge is a dark label with the Houdini logo then the text, and error text is red. The logo is not shipped: hython renders `MISC/logo.svg` of the local install into `<user dir>/icons/houdini_logo.png`. It is loaded by a timer, not in a draw callback, as a "Non-Color" (or "Raw") image so its orange is not darkened.
- **Header button**
  - (`status.py`), after the Object menu (`VIEW3D_MT_editor_menus`). Blender icons cannot be tinted, so the state icons are PNGs drawn by `core/icons.py` (a small PNG writer, `icons.dot` and the flag shapes of `icons.SHAPES`) into the user folder, which the modifier panel and the Modifiers tab of the sidebar share.
  - Previews load lazily, so `icons.load_preview` reads `image_pixels` and `icon_pixels` at registration, which loads them at once. Otherwise the first appearance of a state shows Blender's loading icon. The engine timer redraws the viewports whenever the server state changes.

## Bypass, Lock and the modifier panel

- **Bypass** is Houdini's bypass flag on a Blender modifier: `targets.is_bypassed` is true when the modifier is off in the viewport and in renders (`show_viewport` and `show_render`), and nothing else is stored. The Bypass flag of the modifier panel's node row (`houdini_bridge.bypass`, `targets.set_bypassed`) sets both of Blender's switches; a click on a bypassed modifier turns both on, a click on any other turns both off. Only one of them off (set in the Properties editor) is no bypass: the modifier is still needed where it shows, so it cooks, and its flag is drawn as a yellow outline.
- **Nothing is cooked** for a bypassed modifier: `cook.can_cook` is false, so the timer drops its cooks, the poll and the frame handler skip it, and `cook.cook_now` returns `{}`.
  - The stack goes on without it as for any hidden modifier: `Target.modifiers_above` and `cook/request.chained_above` leave it out, the modifiers below read the stack without it (`_read_up_to_modifier`), and a render, which never cooks (see [Cook loop](../../DEVELOPMENT.md#cook-loop)), leaves it out too.
  - The depsgraph handler still marks the inputs that change; `cook.cooks.track_bypass`, from the poll, notices the end of a bypass and asks for a cook, which reads them (nothing is sent when nothing changed; locked, nothing is asked).
- **Lock** is Houdini's lock flag: the setting `live`, shown the other way round (padlock of the node row).
  - Locked, a modifier keeps its result and cooks only on Recook: the depsgraph handler, the poll, the frame handler, `_recook_below`, `input_changed`, and every path that would cook it by itself check `live` (`cook.cooks_by_itself` for the poll and the frame handler, `live` alone for the depsgraph handler; a file loaded, the server started, a new output mode, the cache turned off, a duplicate's split, an undo bringing it back, the preferences of the sphere detail and of the curve and point radius, the end of a viewport group selection: `cook.cooks.resume`).
  - What follows a Recook (inputs or a chain the server lost) still cooks. The depsgraph and frame handlers still mark the inputs of a locked modifier that change (a bypassed or paused one too, for the frame handler), so lifting the lock (`live` on) asks for a cook, as Houdini cooks a node unlocked, and that cook reads what changed meanwhile; the parameters, the stack above and the frame are compared at each cook anyway.
  - A cached modifier is locked anyway: its padlock is shown set and greyed out, and the Cache section says why. A modifier is created with a cook asked for, even with *Live Updates* off.
- **Apply, cache**:
  - `modifiers.apply_modifier` refuses a bypassed modifier (`ValueError`): its holder keeps a result made before the bypass, which Blender's own Apply writes into the mesh even for a modifier off in the viewport. *Apply All* applies the last Houdini modifier that is not bypassed and leaves the bypassed ones on the object.
  - Both refuse while the Display flag cuts the stack: a modifier the flag cuts cooked reading the stack the viewport shows, so Apply refuses it, and Apply All refuses the whole stack, until the flag is lifted (the flagged modifier itself applies, and the flag goes with it). The cache refuses to start on one. Keyframes on `show_viewport` and `show_render` bypass a modifier over time, and the poll follows.
- **Drawing** (`ui/modifier_panel.py`):
  - the node row draws `NODE_FLAGS` in Houdini's order, Bypass, Lock, Template and Display, each an icon of `core/icons.py` (`SHAPES`: set, filled and colored; not set, the same shape as a light outline). A bypassed modifier is faded (`UILayout.active` false) but for its flags, and its status line says *Bypassed*.
  - The node row shows the HDA's icon, the one the shelf shows (`catalog.tool_of`, `catalog.icon`), else `MODIFIER`, and an icon for the state (`modifier_panel.modifier_state`: bypassed yellow, cooking orange, error red, not cooked yet a grey ring, up to date green). No band or rectangle is drawn over the panels: the state is an icon, or the color of a text.

## Template and Display

Houdini's two other node flags, each an optional feature of its own.

- **Template** (`features/viewport/template.py`):
  - `settings.template`, a toggle stored on the modifier, draws its result as a ghost wireframe in the pink of the flag, faded, behind what stands in front of it (depth tested, depth not written), from its own `POST_VIEW` handler. What is drawn is what the modifier keeps: its holder's mesh, or the cached frame being played (`cache.shown_mesh`), never a cook.
  - One indexed batch of lines per modifier (the vertices once, the edges as index pairs, `UNIFORM_COLOR`), made at the first draw that needs it and kept until a result is written (`events.result_applied`), an undo, a redo or a file load, or until the mesh it was made from changes (pointer and counts); the batch of a modifier whose flag is lifted, or that is gone, is freed at the next draw.
  - Each draw reads the flag on the node groups first, so a file with no flag set costs one loop over its node groups. Nothing is drawn for a bypassed modifier, one in error or not cooked yet, a hidden owner, or with the overlays off. The streams (instances, curves, points, volume) are not drawn: the mesh only.
- **Display** (`features/display.py`):
  - the viewport shows the mesh at the output of one Houdini modifier per object; `display.cut_by` is the rule, every modifier below it, whatever its type. The flag is the anchor of a cut of the stack (`core/stack_cuts.py`, below). `houdini_bridge.display` sets it, moves it from another modifier of the object or lifts it, in one undo step.
  - `display.settle` applies the rule to the stack as it is: run from a timer after a depsgraph update of a flagged object (never inside the depsgraph's evaluation, where the cook engine may be reading the stack with modifiers hidden for a moment), and after an undo, a redo and a file load.
  - It cuts a modifier added or moved below the flagged one, restores one moved above it, and lifts the flag when its modifier is gone (removed, applied) or is no Houdini modifier any more. Disabling the extension lifts every flag of the open file.
  - The Houdini modifiers it cuts are off in the viewport only, so they still cook, for renders (the rule of Bypass, unchanged): they then read the stack the viewport shows (see [Known limitations](pitfalls.md#known-limitations)). In the panel, a modifier the flag cuts says *below the Display flag* in its status line, and its Bypass flag is not drawn as partly set (`modifier_panel.bypass_look`).
- **Cuts of the stack** (`core/stack_cuts.py`):
  - notes the viewport switches of a slice of a stack, turns them off and restores them, used by the Display flag and by the viewport group selection (`selection/session.py`, the modifier and every one below it).
  - A cut has a name, an anchor and the modifiers it holds, and lives on the object (`Object.houdini_bridge_cuts`, JSON), so an undo, a redo, a duplicated object and a saved file bring it back with the switches it changed. Modifiers are named by their `persistent_uid`, which a rename, a move, a duplicate and a file keep.
  - A cut turns off only a modifier shown in the viewport (one the user turned off is left alone, and stays off); a modifier two cuts hold comes back when the last one goes, so a Display flag set during a selection, or a selection begun under a flag, restores only its own; a held modifier the user changed since (shown again, or its render switch changed by a bypass) is released to the user, and nothing restores it; `changed_by_user` tells such a change, on which the Display flag lifts itself.
  - Only `show_viewport` is written, and only when it differs.

## Timeline

Every cook sends `frame` (frame plus subframe) and `fps` (`render.fps / fps_base`). The server calls `hou.setFps(fps, False)`, so keys stay at their time, and `hou.setFrame(frame)` before setting the parameters. Frame 1 is time 0 on both sides, so frame 25 at 24 fps and frame 31 at 30 fps give the same shape.

The answer carries `time_dependent` (`isTimeDependent()` of the export node after the cook), stored in `settings.time_dependent`.

On frame change (`cook/handlers.on_frame_change`, `frame_change_post`), a live time-dependent target is requested. Inputs are only marked there and read later by the timer.

- Input 1 is marked when `changes_shape(owner, before=modifier)` is true: keyframes or drivers on the object, its mesh or shape keys, or a modifier above that animates (pointer to an animated object, Geometry Nodes reading Scene Time, a time-dependent Houdini modifier). Modifier settings are keyframed on the object, so any object action counts.
- Other inputs are marked when they are animated or the owner moves (`moves`).
- The content digest then tells whether anything really changed. `prepare_request` skips a static HDA whose inputs did not change, and recooks a time-dependent one when the frame recorded in `CookState` differs.

## Cache

Alembic is not used. Houdini writes everything, but Blender's reader (Mesh Sequence Cache) drops arbitrary attributes, groups and materials, and reads `Cd` as 8-bit corner colors.

Instead (`cache.py`), Blender drives the frames, since an animated input can only be read by Blender. For each frame: `scene.frame_set`, `cook.mark_animated_inputs`, `cook.prepare_request(always=True, output=contract.MESH)`, then a normal cook sent with `cook.post_cook`, whose callback receives the geometry without writing it to the holder.

Each frame becomes a mesh, written by `mesh_io.write_mesh`, on an object of a collection linked to no scene (`settings.cache_collection`). The holder gets a Geometry Nodes modifier, *Houdini Cache*, whose group does Collection Info (Separate Children), then Delete Geometry on instances whose index differs from the clamped frame offset, then Realize Instances. Playback and rendering are Blender's own evaluation.

- A target with `use_cache` is not live (`cook.can_cook`): `cook.cooks.request` ignores it, and the frame handler and the parameter poll do not cook it. Turning the cache off recooks, unless the modifier is locked.
- While a cache is made, the target is marked in `cook.cooks.caching` and the engine timer stays at 20 ms. A job (`cache.Job`) holds names, not Blender data, and an undo or a redo stops it (`undo_pre`, `redo_pre`): the collection it fills is in no undo step, since the operator pushes none, and the step loaded frees it. The answer of a frame still in flight then goes to nobody: the job is part of the key of its cook, so a cache started again at once does not take it. A collection gone between two frames otherwise (a purge) fails the job with a message.
- The parameter values at caching time are kept (`cache_parms`), and the Cache section of the modifier warns when they changed. Changes of the inputs are not tracked.
- The cache cooks in mesh mode, and the streams are emptied while it is used.

A disk cache read frame by frame would need a format Blender reads natively without losing attributes.

## Native streams

**Registries.** A stream is described in one place on each side, and the rest of the code walks the registry. Its name is a constant of `shared/contract.py` (`contract.STREAMS`, in the order both sides go through them: instances, curves, points, volume), a key of the cook answer and of `streams_json`, so it never changes. [Adding a stream](../../DEVELOPMENT.md#adding-a-stream) lists the places a new one takes.

- Server: `houdini/streams.py` has one class per stream (`InstanceStream`, `CurveStream`, `PointStream`, `VolumeStream`, all `Stream`), listed in `STREAMS`.
  - `Instance` makes one of each, in that order, and each builds its branch of nodes from what the instance lends it (`container`, `classify`, `wrangle`, `keep_kind`, `mesh_export`) and keeps its own state (`InstanceStream.sent_keys`, `VolumeStream.volume_files`).
  - `Instance.export_streams` asks each one whether it has something (`has`), then for its description and blocks (`export`), or tells it it is absent (`absent`: the instances forget their keys); `resend`, `configure` (the sphere detail, read from the request) and `destroy` (the `.vdb` files) are its other steps. Timings are kept under the stream's name, only when it exported.
- Blender: `blender/core/streams/__init__.py` has `STREAMS`, the `Stream` of each file (`common.Stream`: a record of functions, no state, since the state is in the modifier's settings).
  - A stream declares its name, the property of `HoudiniBridgeSettings` that holds its hidden object (`instances_holder`…) and the ones it owns (`prototypes`), and how it writes its part of a result (False when the server must send it again), what it counts in `streams_json`, its text of the status line, how it is emptied, how its holder enters the *Houdini Streams* group (`show`), and what it becomes at Apply.
  - `write`, `clear`, `describe`, `apply_to_objects`, `release`, `detach` and the streams node walk the registry. What only one stream does stays a function of its file, called by name: `instances.apply_instance_display`, the applied volume files and `volumes.on_save`.
- `tests/unit/test_stream_registry.py` checks that both registries hold the names of `contract.STREAMS`, in that order, the kinds of the classification, and that a stream is named by its string only in the contract, in the file of each stream and in `core/properties.py`.

**Classification** (`Instance.classify`): one primitive wrangle, `VEX_CLASSIFY`, sets `__kind`: 0 mesh, 1 instance (any `Packed*`, `AlembicRef`, quadric `Sphere`), 2 open curve (open Poly, NURBS or Bezier curve), 3 volume (VDB, or a Houdini Volume that is not flat, since height fields keep the mesh path).

It is one decision for the whole result: each primitive goes to exactly one stream and the order of the tests matters, so it stays one snippet next to the registry, and each number it gives is named by a constant (`KIND_MESH` is 0, `KIND_INSTANCES` 1...), the `kind` of one stream class. The registry derives the rest: the number of kinds counted (`KIND_COUNT`), and one Blast per kind (`Instance.keep_kind`).

The loose points are no kind of primitive: `PointStream` has no `kind`, reads the classification's output and is cooked at every export. In native mode the mesh path removes the other kinds with their points (`remove_streams`) and the loose points (`remove_loose`). In mesh mode `remove_streams` and `remove_loose` are bypassed: the classification still runs, and the mesh keeps only its polygons. Each stream is only cooked when the classification found something.

**Instances**: `VEX_INSTANCES` writes per packed primitive:

- `4@__xform`: the `packedfulltransform` already in Blender's convention (C·M·Cᵀ, whose row-major bytes are what a column-major FLOAT4X4 attribute expects).
- `4@__houdini`: the matrix as Houdini has it.
- `i@__key`: shape identity from the type, `geometryid`, `fragmentname`, file, Alembic path, and the instance's own `shop_materialpath`.
- `i@__point`.

A quadric sphere is a unit sphere placed by its 3×3 `transform` intrinsic and its point. Its shape is a polygon sphere (`unit_sphere`), since Convert meshes transformed quadrics differently. The frequency is part of the shape key.

Fragments of one Assemble share a `geometryid`, so `fragmentname` tells them apart. `geometryid` changes when the shape is recooked and is stable otherwise.

**Prototypes**: one representative per new key goes through Blast, Unpack (no iteration limit, for packed inside packed), Convert, closed polygons, the topology export and `export_mesh`. Then `localize` brings P by the inverse matrix and N by its transpose. The server remembers the keys sent per instance (`InstanceStream.sent_keys`) and only sends new ones. `resend` sends them all again. A material set on the instance applies to a shape that has none.

**Per-instance attributes**: numeric point attributes (through `__point`) and primitive attributes, the primitive winning. Placement attributes (`orient`, `pscale`, `scale`, `N`, `up`…) are skipped.

**Curves**: Convert to polylines, then the vertex topology export. Points are taken per vertex, since a Blender curve owns its points. Point and vertex attributes go to the curve points, and primitive attributes to the curves.

**Volumes**: Convert VDB (Volume to VDB), written with `saveToFile` into `volume_dir` under a new name at every cook, which makes Blender reload it. Previous files are removed when Blender no longer holds them, and all of them when the server exits. The default folder is one per Blender process, `volumes/<pid>/` in the system's temporary folder, so that two Blenders never remove each other's files: Blender reads a `.vdb` and closes it, so a file in use is not locked. A hython killed or crashed leaves its files: each new session removes those that no `bpy.data.volumes` reads, in its own folder and in the folders of the processes that have ended (`streams.remove_orphan_volume_files`, from `cook/scheduler.begin_session`, with `hython.process_alive`), and leaves the folder of a Blender still running, and a folder set in the preferences, alone.

**Level sets**: a VDB of class `level set` stays a volume, so Blender 5.2's SDF grid nodes can use it. Blender exposes no grid class, so the server sends `classes` (the `vdb_class` intrinsic), and a level set without a Houdini material gets *Houdini Level Set · <grid>*: Volume Scatter at density 200 where the distance is negative. That is solid in Cycles and a flat silhouette in EEVEE. The Solid viewport draws the grid as smoke (the positive band outside the surface).

**Blender side** (`blender/core/streams/`): one hidden holder per stream next to the mesh holder.

- Instances: a PointCloud with `transform` (FLOAT4X4), `proto` (INT) and the attributes, instanced by its own *Houdini Instancer* modifier: Collection Info of the prototypes (Separate Children), Instance on Points, Pick Instance, Set Instance Transform, and a Switch on Is Viewport AND *Points in Viewport*.
- Curves: a Curves object. Points: a PointCloud. Volume: a Volume object.
- The modifier's node group gets a *Houdini Streams* group node between the usage chain and the Group Output: Object Info of each holder, then Join Geometry. Curves go *As Instance*, otherwise Blender 5.2 does not render them inside a mesh object, and the volume is rotated +90 degrees around X. The node is only added when a stream first appears, and `parameters` keeps it when it rebuilds the inputs.
- Collection Info gives its children in name order, so prototype objects are renamed `<holder> · shape 00000…` in the order of the sorted keys (two passes), and carry their key (`houdini_bridge_key`). Stale shapes are removed. A key with no shape on either side asks for a forced cook.
- A stream missing from a result is emptied, not removed. Duplicating the object (`make_unique`) detaches the copy from the original's streams. Removing the modifier removes the holders, the shapes and the node groups.
- `shop_materialpath` on curves and points becomes their materials and `material_index`. A volume takes the first one, or a default *Houdini Volume* (Principled Volume reading `density`).
- Volume display and render settings are drawn under the modifier, since the volume lives on a hidden object. Apply copies the datablock, settings included.

## Chained Houdini modifiers

Reading the evaluated object from Blender (`to_mesh`) keeps only its mesh, so a VDB, packed primitives or curves would never reach the modifier below. When the visible modifier right above is a live Houdini modifier that has cooked (`cook/request.chained_above`), input 1 is sent as state `chain` with `chains: {index: instance}`. The server links it with an Object Merge on that instance's HDA (`Instance.set_chain`), so the result passes untouched and stays live.

Those links make the cook order matter. After a reorder, a modifier now above another may still be chained on it on the server, and linking the other way first closes a loop (`Infinite recursion in evaluation`). So `cook/scheduler.tick` holds a modifier back while a Houdini modifier above it is pending or cooking (`_waits_for_stack`), `poll_modifier_inputs` compares the modifiers above each target with the ones at its last cook, since a reorder is not a reliable depsgraph signal, and every written result asks the Houdini modifiers below to cook again (`cook/result._recook_below`).

What Blender follows is the result count of the modifier above (`CookState.cook_count`), not the mesh. A chained instance the server lost answers `missing_chains`: the modifier above cooks again, and the one below reads Blender's mesh meanwhile.

## Apply

`modifiers.apply_modifier(context, tree, with_above)` applies one Houdini modifier. The operators of `ui/operators.py` only call it and report, and so does `api.apply_modifier`.

- What Blender's `modifier_apply` refuses on a mesh object (a mesh with other users, shape keys, linked data) is checked first, by `modifiers._check_blender_applies`, and refused with a `ValueError` before anything changes; a refusal it did not foresee is a `ValueError` too. So is a volume whose live `.vdb` is gone (`streams.volume_file_gone`): a file opened again that has not cooked, blocked or locked; the streams before it would be objects already.
  - `streams.apply_to_objects` runs before `modifier_apply`: instances become a point cloud object with a copy of the instancer node group (the shapes collection is handed over and renamed `<object> · shapes`), curves a Curves object, points a point cloud object, and the volume a Volume object. They take the owner's collections and world matrix.
- With `with_above`, it also removes the modifiers above it that are shown in the viewport, since its result already holds theirs. `modifier_apply` of a modifier that is not first applies it alone to the original data, which is right here: a Houdini modifier's output does not depend on the data Blender passes it.
- *Apply All* (`houdini_bridge.apply_all`, `modifiers.apply_all`) is the last Houdini modifier as a block, then the Blender modifiers below it; the modifiers above it off in the viewport only, which its result left out, stay on the object, as the bypassed ones do. It refuses while the Display flag cuts the stack, and where Blender's Apply would refuse (`ValueError`).
- The groups of the result join the Groups panel through `events.modifier_applied`, before an emptied owner is replaced.
- When the owner is left with an empty mesh, no modifier and no child, the single new object takes its place and its name.
- When the single new object can carry Houdini modifiers (a Volume object), the modifiers below move onto it with their input values (`modifiers._move_modifiers`, `parameters.copy_input`, in `parameters/inputs.py`). Their server instances are dropped and they cook again, reading the volume.
- A Houdini modifier on a Volume object cannot be applied by Blender: its mesh becomes a mesh object of its own, which takes the volume's place when nothing else is left.

**Volume objects as inputs**: `targets.INPUT_TYPES` is mesh and volume, for owners, input pickers and *Apply to*. A Volume source travels as `input_volumes: {index: {path, matrix}}` with state `full` and no blocks. The server loads the `.vdb` (`import_volume`) and transforms it by the placement in the owner's space, then Z up to Y up (`mesh_io.TO_HOUDINI`, sent as row vectors). Its digest is path, modification time and matrix. A Volume owner gets a mesh holder. A Volume object displays and renders the mesh a Geometry Nodes modifier outputs.

**Applied volumes**: the `.vdb` is written by Houdini turned to Blender's axes (`write_volume` command), and the Volume object has the owner's matrix with no extra rotation. Without a server, the file is copied and the object turned instead. It goes next to the .blend (folder name, relative path) or into a folder. *In a Folder* with no folder set acts as next to the .blend, in `streams.applied_volume_mode` and at the save.

A file not saved yet keeps them in `<user dir>/applied_volumes`, and `streams.on_save` (`streams/volumes.py`, on `save_pre`, which receives the target path) moves them next to the new .blend and rewrites their path before the file is written.

Packing does not work: `bpy.ops.file.pack_all` packs a volume, and there is no way to pack one alone, but Blender 5.2 never reads a packed volume. The grids load from the file path, also after reopening the file.

Blender's own Apply in the modifier panel cannot be intercepted and keeps only the mesh. Blender's Object > Convert has no Volume target.

## Preferences

`ui/preferences.py` has six tabs (Modifiers, Output, Shelf, Houdini, Viewport, Hotkeys), each made of collapsible sections (`UILayout.panel`), and the callbacks that recook or rewrite the library when a preference changes. `preferences.get()` (`core/preferences.py`) returns them, and `DEFAULTS`, in the same file, holds the default of each one. Each setting is described in the [guide](../GUIDE.md#preferences); [Adding a preference](../../DEVELOPMENT.md#adding-a-preference) lists the places a new one takes.

- **Houdini versions** (`hython.find_installations`): the usual folders plus the values of `HKLM\SOFTWARE\Side Effects Software\Houdini`, newest first. An empty `hython_path` means automatic. `houdini_bridge.use_houdini` pins one and restarts a running Houdini. `link.server.running_hython()` tells which one runs.
- **Session**: *Start With Blender* is a one-shot timer, not in background mode. *Stop When Idle* (`cook/scheduler.stop_when_idle`) needs no cook posted for N minutes, nothing pending and no playback. The next cook starts it and sends every input again.
- **Shelf**: *Hide* is `catalog.listed_tools`, fnmatch on label, short name and type, and favorites stay. *Update* only offers a newer version (`catalog.newer_version`).
- **Output**: *Sphere Detail* is sent as `sphere_frequency`.
