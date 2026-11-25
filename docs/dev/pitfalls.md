# RG Houdini Bridge: pitfalls and limits

What Blender and Houdini do that one would not guess, the measurements, the known limitations and ideas for forks. Part of the developer documentation: start with [DEVELOPMENT.md](../../DEVELOPMENT.md).

- [Pitfalls](#pitfalls)
- [Measurements](#measurements)
- [Known limitations](#known-limitations)
- [Ideas for forks](#ideas-for-forks)

## Pitfalls

### Blender

- bpy timers do not run with `--background`, hence `cook.cook_now`, `catalog.refresh_now` and `cache.cache_now` for tests and scripts (`api.cook_now`, `api.refresh_shelf`, `api.cache_now`).
- The sidebar tab (`region.active_panel_category`) is read-only from Python and raises `AttributeError` when written. `docs/capture_images.py` clicks down the tab column instead.
- `modifier.show_expanded` has its own open and closed icons, and passing `icon=` breaks them.
- A child panel of an instanced panel (`bl_parent_id = "MOD_PT_GeometryNodes"`) without a header (`HIDE_HEADER`) crashes Blender 5.2 (`ui::handler_panel`, or the redraw after) when any panel type is registered or unregistered while a Properties editor shows Geometry Nodes modifiers: enabling or disabling the extension, Reload Scripts, another add-on.
  - A child with its header survives all of them, so the modifier panel keeps its header (`PANEL_HEADER`). Registering a child while such modifiers show crashes too, header or not, until Blender frees its instanced panels: it does so whenever a panel type is unregistered, so `register()` registers and unregisters a panel of no use (`HOUDINI_BRIDGE_PT_rebuild`) right after the child.
- The open state of the sub-panels of an instanced panel is a bit of the modifier's `ui_expand_flag`, which Python cannot reach (`show_expanded` is the main panel only): the modifier panel starts folded on each modifier, and stays as the user leaves it, saved with the file.
- `poll` of a child of an instanced panel has no `context.modifier`; `draw` has the modifier as `self.custom_data`.
- An item of `modifier.properties.inputs` kept in Python across a change of the node group's interface (a rebuild, the Houdini interface applied) dangles: reading it crashes Blender. Fetch it again (`parameters.modifier_input`).
- `bpy.data.temp_data()` damages Python's memory on every Blender 5.2.x: its `__enter__` gives the data without the reference it owes (fixed in 5.3, commit 9b217bcbd6), so the context frees the data at the end of the block while the name bound by `as` still points at it, and releasing that name later writes into freed memory.
  - Blender crashes some time after, most often in the garbage collector (`EXCEPTION_ACCESS_VIOLATION` in `python313.dll`, under `Py_HandlePending`, `gc.collect` or `gc.get_objects`), far from the cause: a whole run of `tests/test_blender.py` used to crash at `test_reenable` one time in three or four, after the asset library had been written.
  - Use `assets.temp_data()`, which adds the missing reference when it is missing (`test_temp_data`).
- No nested function calls itself on the Blender side (`test_architecture`): a recursive closure is a reference cycle, which keeps the Blender structs it saw (the panels of a node group's interface, the layouts of a draw) alive until the collector runs.
- An extension's `register()` runs with `bpy.data` out of reach (`RestrictBlend`), and `addon.preferences` may be None while it registers again: `preferences.get()` then gives the defaults.
- Blender only enlarges an icon in `template_icon`, never on a button.
- In Edit Mode the active UV layer can read as empty, so UVs are then skipped.
- `Cd` is reserved on meshes as a color attribute, but is a plain attribute on point clouds and curves.
- `bound_box` of an evaluated object ignores its volume.
- Since Blender 4.5, normals are written straight into the `custom_normal` corner attribute. `Mesh.normals_split_custom_set` crashed Blender 5.2 (`EXCEPTION_ACCESS_VIOLATION` in `mesh_normals_corner_custom_set`) from the timer with a SideFX Labs Edge Damage result, although the normals were clean, and it could not be reproduced in the background. The extension never calls it.
- Removing an extension also deletes its user folder, favorites included.
- On Windows, `python -` can block on stdin through the WindowsApps alias. Run Blender's Python by its full path.

### Houdini

- `primintrinsic` needs the primitive number, and Mesh or NURBS primitives must be converted before export.
- `hou.findFile` raises instead of returning `None`.
- Parameter callbacks do not run on `parm.set()`, and some nodes keep a separate output name: Measure writes `area` whatever it measures unless `attribname` is set. Set both, as in Houdini.
- Writing `@P` in a primitive wrangle does not move packed primitives. Run over points.
- A node reading `@Time` makes the whole HDA time dependent even when the animation amount is zero. The test HDAs switch between a still and an animated wrangle.
- Convert VDB turns a fog into polygons only with an *isovalue* above 0.
- Some library paths come back in short form (`C:/PROGRA~1/…`).
- In hython, `hou.Parm.isHidden()` and `isDisabled()` are only right after `hou.OpNode.updateParmStates()`: neither a `set` nor a cook evaluates hide-when and disable-when conditions (in graphical Houdini, the Parameter Pane calls it). The call also covers parameters a native node disables from its own code, without a conditional (Object Merge).
- The state of a folder stays on the folder: the parameters of a hidden or disabled folder, or the instances of a hidden or disabled multiparm, keep `isHidden()` and `isDisabled()` false, though the Parameter Pane hides or greys them out. Labs tools often put a Simple folder's condition in its tab conditionals (`tabConditionals()`), which its folder-set parameter follows too. A set of tabs has one folder-set parameter (named after the first tab, with a number added), so the state of a later tab cannot be read.
- `hou.Parm.set` does not replace an expression. On Time Shift's *Frame* (`$F`, an expression keyframe), `set(1.0)` at frame 10 adds a keyframe at frame 10 that carries `$F` again: the value is ignored, it still evaluates the frame, and each such write at a new frame adds one more keyframe (observed in Houdini 22.0). `deleteAllKeyframes()` then `set` makes it a constant.
- `hou.Parm.set` follows a channel reference (`follow_parm_reference=True` by default): on Box's *Size Y* reading `ch("sizex")`, `set(3)` writes *Size X* and leaves the reference, and `parmTuple("size").set([1, 4, 1])` gives `[4, 4, 1]`, *Size Y* writing over *Size X*. `set(value, follow_parm_reference=False)` drops the reference and sets the value. An expression that is not a plain reference (`ch("sizex") * 2`) behaves as `$F`.
- `hou.Parm.unexpandedString()` raises `hou.OperationFailed` on a string parameter with an expression or keyframes; `evalAsString()` reads it.

### This code base

- `cook.prepare_request` records what it prepares as sent: a second call finds nothing changed. To look at a request without sending it, `cook.peek_request` builds the same request and records nothing (the inputs are still read, so the stack trick and `update_from_editmode` still run).
- The engine timer idles at 100 ms, which skips frames during playback and slows caching. It stays at 20 ms while either runs.
- SideFX Labs Edge Damage 1.0 needs `labs::voxelmesh`, which Labs 22.0 no longer ships. Version 2.1 works in every mode. Its Boolean mode takes about 4 s per cook, and up to 14 s on degenerate input, so editing its input live queues a cook per change. Such tools are better used locked.

## Measurements

On an RTX 5080 with Houdini 22.0.429 and Blender 5.2.

| What | Time |
|---|---|
| hython startup | 2.5 s, up to about 9 s cold |
| 250,000-quad grid through the rock HDA, full round trip | about 720 ms, of which creating the polygons in Houdini about 0.4 s |
| The same, positions only | about 270 ms (server 130, Blender 70, reading the input about 70) |
| Rock on a 4,500-face sphere | 40 to 170 ms |
| 100,000 instances, per cook | 80 to 100 ms, Blender 4 to 6 ms |
| 1,000,000 instances, per cook | 0.73 to 0.88 s, of which Houdini's scatter 0.28 to 0.42 s, instance export 0.22 to 0.25 s, Blender 40 ms |
| Drawing 1,000,000 instances in the viewport | 120 to 170 ms, against 1 ms as points |
| Socket throughput | 1.3 GB/s without copies, 0.5 GB/s with them |
| Caching 48 frames | 1.4 s at the 20 ms timer, 5.1 s at the idle timer |
| Catalog refresh | about 7 s for 1,200 tools, icons reused |
| `updateParmStates()` per description, 68 tools | median 0.01 ms, at most 0.9 ms (Tree Branch Generator, 147 parameters) |
| Folder states read by one description, 68 tools | median 0.01 ms, at most 0.08 ms (LOD Create) |
| 48 cached frames of a rock of about 2,000 faces | +3 MB in the file |
| Drawing the modifier panel of Tree Branch Generator (about 150 parameters), its body | median 1.0 ms, at most 1.4 ms |
| Template flag, a 250,000 face result (505,000 edges): making its batch | 28 ms, once per result |
| Template flag, the same: drawing it, per frame | 0.013 ms on the CPU; 80 more draws in one frame still fit the 13.3 ms of the vertical sync |

Shared memory was measured at only 30% more throughput than the socket without copies, so it is not used.

## Known limitations

- SOP HDAs only, 4 inputs at most.
- A parameter Houdini drives with an expression shows in Blender the value it had when the parameter list was built, not what Houdini evaluates. A vector is sent whole: changing one component in Blender also sends the others, and a component whose expression gives another value now (one reading the time) loses it.
  - The first cook of a session sends no driven value (`request._values_sent` compares with `CookState`, which a session starts empty): a value changed while no server ran (a blocked file, after Stop or Stop When Idle) is not sent until it changes again. A fix would keep, in the `.blend`, the value last seen with the expression (a stored field of `HoudiniBridgeParm`), and send `null` for a driven component that did not change (a protocol version).
  - A file saved before 1.0 has `expression` false on every parameter until its first cook describes them: that cook sends every value, and so replaces the expressions Houdini had on those modifiers (`$F`…). Only files of the pre-release builds are concerned.
- Not supported: data parameters. Radio folders are drawn as tabs, but the choice is not an input. The instances of a tabbed multiparm are boxes, not tabs. Parameter help is the tooltip of an input (`socket.description`), not of a ramp or a button. No slider range. Vectors are a column, not one line.
- The modifier panel keeps its header (see the Blender pitfalls): Blender folds it at first on each modifier, and a Geometry Nodes modifier that is not Houdini's gets an empty folded row (about one header high).
  - `PANEL_HEADER` in `ui/modifier_panel.py` is the switch for when Blender is fixed. The whole interface of a Houdini modifier hangs on that child panel of an internal panel of Blender (`MOD_PT_GeometryNodes`), held to the versions where its crashes were probed (`blender_version_max`).
  - With *Houdini Interface* off, Blender draws every value as a plain input and the panel adds only the ramps, the buttons and the group pickers and arrows; a ramp can also be edited on its node in the node group.
  - Were the child panel ever to go, nothing else would reach the buttons, the group arrows, the + and - of a multiparm (its count stays an input), Lock, Template, Display or Recook: Bypass is Blender's two switches, Apply All is in the sidebar, the rest in `api.py`.
- The viewport group selection reads Enter and Esc in the window where its arrow was clicked (a modal handler belongs to one window): with the 3D view in a second window, Enter there does nothing and the selection is confirmed from the first one. It writes the elements in order of their numbers, not in the order they were clicked (Houdini's `kwargs['ordered']`).
- In a 3D view in local view, the temporary copy of a group selection joins the local view of the views that show its source; a view in local view without the source does not show it.
- A tab hidden or disabled by its own tab condition, when it is not the only tab of its set, is not hidden or disabled in Blender: its state cannot be read in hython. Blender then shows more than Houdini, never less. Its parameters show unless a condition of their own, or a Simple or Collapsible folder inside the tab, hides them (Tree Branch Generator's Growth tab, in a sample of 68 tools).
- A parameter Houdini greys out is faded as the last cook found it, never locked.
- A Houdini modifier gets streams only through a chain. With a Blender modifier in between, or a cached modifier above, it reads the evaluated mesh only.
- Apply keeps only the mesh when it is Blender's own. The cache stores realized meshes. A packed primitive holding curves or volumes gives a mesh prototype only.
- Towards Houdini, materials, edge attributes (Houdini has none), string, quaternion and matrix attributes are not sent. Neither are instances, curves and points.
- Rendering an animation with a live Houdini modifier: cooks are asynchronous and paused during the render, so a time-dependent modifier must be cached.
- New objects: the output is in world space and the object keeps an identity transform, while modifiers work in local space.
- No cycle detection between objects (A input of B input of A).
- The faces' `material_index` is not sent to Houdini: on an owner with several materials, every face of a result Houdini gives no material shows the first slot, also after a result that named some (`material_index` is removed from the holder).
- Blender 5.2 does not expose whether it let the loaded file run its scripts, only its preference, its Excluded Paths, the command line and `bpy.app.autoexec_fail`: a file opened as a Trusted Source, or reloaded by Blender's own *Allow Execution*, waits for the extension's *Allow Execution* when the preference is off (`core/trust.py`).
- Blender has no integer attribute of 3 or 4 components: one from Houdini becomes a float vector, axes untouched, and goes back to Houdini as floats.
- Sculpt Mode edits of the original mesh have not been checked. Edit Mode and Object Mode are tested.
- Only Blender 5.2 and Windows have been tested.
- On Linux and macOS, a Blender killed or crashed leaves hython running until its cook ends, or for two minutes when hython was starting. `PR_SET_PDEATHSIG` would not do: Linux sends it when the thread that started the child ends, and hython is started from a thread that ends right after. A thread of the server reading a pipe from Blender until it closes would.
- Under a Display flag, a Houdini modifier below another modifier the flag turned off reads the stack without it, as for any modifier off in the viewport only, and a render made meanwhile uses that result. Lifting the flag cooks it again with the whole stack.
- A file saved with a Display flag set, opened without the extension, shows the modifiers below the flag off in the viewport. Nothing turns them back on before a save: it would evaluate the whole stack again and leave the file marked modified, as turning the Houdini interface off would.
- The Template flag draws the mesh of a result only. In cache mode, its batch is made again at each frame played.
- An object with an action has its input 1 read again on each frame, even when only parameters are keyed. That costs one stack read and no recook. Filtering on the fcurves' data paths would refine it.
- The code editor is Blender's Text Editor: VEX is colored as OSL (its functions and `@` attributes plain), and a language other than VEX and Python is plain text. Its window is a temporary window of Blender's Preferences turned into a Text Editor, which Blender sizes and places; a Preferences window already open becomes the code editor. Houdini's presets of code are not offered. Opening a file closes any temporary window left holding only an empty Text Editor, one of the user's own included.
- Vertex group weights are read in a Python loop, which has not been measured on a large painted mesh.

## Ideas for forks

- Instances, curves and points sent from Blender to Houdini.
- A cache of the native streams.
- Object-level and LOP HDAs.
- A compiled extension module on the Blender side, if the transfer becomes the bottleneck.
- A HAPI back end, for those with a Houdini Engine licence.
- Linux and macOS: hython is searched in the usual places, but nothing has been run there.
