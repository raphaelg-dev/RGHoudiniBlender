# Changelog

## 1.0.0 — first public release

RG Houdini Bridge runs Houdini's nodes and your HDAs in Blender 5.2 as live, non-destructive modifiers. Houdini (`hython`) runs in the background on the same machine; Blender sends it the geometry and writes back the result. See [how it works](docs/GUIDE.md#how-it-works).

**Finding tools.** The shelf of the Houdini sidebar lists your HDA folders, your Houdini packages such as SideFX Labs, and Houdini's own SOP nodes, with Houdini's icons and categories, a search and favorites. The asset shelf (`Ctrl+Shift+H`), a favorites pie (`Shift+Alt+H`), *Add Modifier > Houdini* and the *Add Houdini Node* search under *Add Modifier* reach the same tools. A tool becomes a modifier of the active object, or a new object for a generator. See [Finding and applying tools](docs/GUIDE.md#finding-and-applying-tools).

**The Houdini modifier.** Each one has its panel in the Properties editor, laid out like Houdini's parameter pane. Its header is the node: the HDA's icon and name, Houdini's four flags, the color of its state, *Recook* and *Apply*. Under it come the status and errors, the inputs, then the parameters in Houdini's order and folders. See [Houdini modifiers](docs/GUIDE.md#houdini-modifiers).

- *Bypass* turns the HDA off in the viewport and in renders, and the stack goes on without it.
- *Lock* keeps the result: nothing cooks it but *Recook*, and lifting the lock reads what changed meanwhile.
- *Template* draws the result as a ghost wireframe while the stack shows as usual.
- *Display* shows the output of one modifier and turns off the ones below until it is lifted.

**Parameters.** Collapsible folders, boxes, rows of tabs, menus, ramps edited with Blender's own widgets, buttons, multiparms with + and −, and group fields with their list of groups. The arrow of a group field selects the group in the viewport, as in Houdini. A code parameter, such as a wrangle's VEX, opens in Blender's Text Editor. Parameters are Geometry Nodes inputs, so they take keyframes and drivers. A parameter Houdini drives with an expression follows Houdini until you change it in Blender. See [Parameters](docs/GUIDE.md#parameters).

**Inputs and the stack.** Input 1 is the object with the modifiers above it, live in Edit Mode; up to three more inputs are objects, meshes or Volume objects. Houdini modifiers mix with Blender's, and two stacked Houdini modifiers pass each other their full result. See [Inputs](docs/GUIDE.md#inputs).

**Groups and attributes.** Point, edge and face groups made in Blender reach Houdini as groups, and the groups a tool makes come back, drawn in color. Attributes, vertex groups, colors, UV maps and normals go to Houdini; numeric attributes, colors, UVs and materials come back. See [Groups](docs/GUIDE.md#groups) and [Attributes](docs/GUIDE.md#attributes-uvs-colors-and-materials).

**Native output.** Packed primitives become Blender instances, open curves a Curves object, loose points a point cloud, and VDBs a Volume object. *Visualize* draws any attribute of a result in the viewport, with a legend. See [Native output](docs/GUIDE.md#native-output) and [Visualize](docs/GUIDE.md#visualize).

**Time, cache and Apply.** Houdini follows Blender's frame and frame rate. *Cache Frames* cooks a range once, and Blender plays it back and renders it without Houdini. *Apply* freezes a result into Blender data, instances, curves, points and volumes included, and *Apply All* does the whole stack. See [Timeline](docs/GUIDE.md#timeline), [Cache](docs/GUIDE.md#cache) and [Apply](docs/GUIDE.md#apply).

**Houdini in the background.** A button in the viewport header shows whether Houdini runs, with *Start*, *Stop* and *Show Log*. Houdini starts on the first cook, stops with Blender, and *Stop When Idle* frees its licence. An HDA saved again from Houdini reloads on the next cook. See [Connection and feedback](docs/GUIDE.md#connection-and-feedback).

**Opening a file.** A file follows Blender's *Auto Run Python Scripts* preference: when Blender would not run its scripts, it cooks nothing, and starts no Houdini, until you click *Allow Execution*. See [Opening a file](docs/GUIDE.md#opening-a-file) and [SECURITY.md](SECURITY.md).

**Scripts.** `api.py` places tools, sets parameters, cooks, caches and applies from Python. See [Scripting](docs/GUIDE.md#scripting).

It needs Blender 5.2 and Houdini 20.0 or later. It has been tested only on Windows, with Houdini 22.0. The [limitations](docs/GUIDE.md#limitations) are listed in the guide.
