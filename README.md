# RG Houdini Bridge

![Blender's viewport with an orange 3D Houdini logo and a pig head, the logo's edge group highlighted, and a stack of Houdini modifiers in the Properties editor](docs/images/banner.png)

Use Houdini's nodes and your HDAs in Blender as live, non-destructive modifiers. [Install](#install)

![A cube cut and stretched in Blender's Edit Mode, with the Labs Edge Damage tool following on the result](docs/images/live-edit.gif)

*Screen recording. The cube is edited in Edit Mode, and SideFX Labs Edge Damage follows as a modifier.*

An **HDA** is a Houdini tool packaged as a file.
**Cooking** is Houdini computing a tool's result.

**Project status.** I started this as a personal project for my own work, and it is now open source under the GPL-3.0. If you feel like helping to improve it, don't hesitate: bug reports, ideas, pull requests and forks are all welcome. To find your way in the code, start with [DEVELOPMENT.md](DEVELOPMENT.md), then [CONTRIBUTING.md](CONTRIBUTING.md) for building and running the tests.

## How it works

Pick a Houdini tool and it becomes a modifier. Its parameters appear in the modifier panel, and it updates when the mesh below it changes. There is no export step.

- Houdini runs in the background on the same machine and holds a Houdini licence while it runs. Which one depends on the licences installed: with Houdini Indie, it takes a Houdini Engine Indie licence first when there is one. Houdini's License Administrator shows which one is taken.
- It has only been tested with Houdini Indie. Apprentice is untested. Make sure your use fits the terms of your licence.
- Without Houdini, a file keeps only what was cached or applied. See [Freezing results](#freezing-results).

Opening a .blend file with Houdini modifiers starts Houdini and loads the HDA files the file names, and an HDA can run Python. The extension follows Blender's *Auto Run Python Scripts* preference: a file Blender would not let run its scripts cooks nothing until you click *Allow Execution* in a Houdini modifier's panel or in the *Houdini Bridge* menu of the viewport header. Houdini's server listens on 127.0.0.1 only and answers only the Blender that holds its random token (see [SECURITY.md](SECURITY.md)).

## Quick start

### Requirements

- Blender 5.2, the only tested version.
- Windows. Linux and macOS are untested.
- Houdini 20.0 or later on the same machine, with a licence that can run `hython`, Houdini's Python shell: the server needs Python 3.10. Only Houdini 22.0 has been tested. The tool icons are drawn with Houdini's PySide6; a Houdini without it shows Blender's icons instead.

### Install

1. Download `rg_houdini_bridge-<version>.zip` from the latest [release](../../releases/latest).
2. In Blender: *Edit > Preferences > Get Extensions*, open the arrow at the top right, choose *Install from Disk*, and pick the zip.
3. In *Edit > Preferences > Add-ons > RG Houdini Bridge*, open the *Houdini* tab and check that your Houdini is listed.

**Do not install GitHub's *Code > Download ZIP* in Blender.** It is the source code, and it does not install as an extension. To build the extension from the source, see [CONTRIBUTING.md](CONTRIBUTING.md).

### First result

Use a dense mesh. On the default cube, with its 8 corners, Mountain has almost nothing to move.

1. Delete the default cube with `Delete`: the sphere would appear inside it. Add a UV Sphere with `Shift+A` and give it a Subdivision modifier.
2. With the sphere selected, press `N` to open the sidebar, then the *Houdini* tab.
3. Click *Read the Houdini Tools*. The first start can take a while. The dot in the viewport header turns green when Houdini is ready.
4. Type `mountain` in the search field and click *Mountain*.
5. Open the Modifiers tab of the Properties editor, unfold the panel of Mountain under the modifier's header, and drag *Height*.

![The Height parameter of Mountain swept up and down, the sphere turning into a rock](docs/images/live-parameter.gif)

*The sphere turns into a rock. The stone color here comes from a material added for this picture. The GIF is stepped, so it does not show real-time speed.*

**Use your own HDA.** Click the folder icon in the shelf to open a file, or add a folder in *Preferences > Shelf > HDA Folders* and press the refresh button.

Houdini stops by itself when Blender closes.

Stuck? See [Troubleshooting](docs/GUIDE.md#troubleshooting).

## Features

![The scene in Blender's viewport in daylight, the Houdini sidebar on its Shelf and Modifiers tabs, and the panel of Bridge Grass unfolded in the Properties editor](docs/images/hero-blender.png)

The terrain, the grass and the fragments here are all Houdini modifiers on Blender objects. The sidebar holds the shelf and an overview of the Houdini modifiers of the selected object; each modifier is set in the Properties editor, as close to Houdini's parameter pane as Blender allows.

![The three tabs of the Houdini sidebar side by side: the shelf, the Houdini modifiers of the object with one bypassed, and its groups](docs/images/sidebar-tabs.png)

The tabs on the left of the sidebar show one part, or several with Shift+click: the shelf, the overview of the modifiers, the groups of the mesh.

### The Houdini modifier

![A rock whose faces PolyExtrude pushes out as shards, the panel of PolyExtrude unfolded in the Properties editor with its group field, its parameters and its tabs](docs/images/modifier-panel.png)

Each Houdini modifier has its own panel under the modifier's header, laid out like Houdini's parameter pane: the status, the inputs, then the parameters in Houdini's order and folders. Blender folds that panel at first on each modifier; unfold it once and the modifier keeps it open.

![Four Houdini modifiers folded to their node rows, each with one flag set: Lock, Template, Display and Bypass](docs/images/node-row.png)

The header of the panel is the node: the HDA's icon and name, Houdini's four flags (Bypass, Lock, Template, Display), the color of its state, *Recook* and *Apply*. It stays readable with the panel folded.

| Template, a ghost of one step | Display, the output of one step |
|---|---|
| <img src="docs/images/template-flag.png" width="420" alt="A smooth rock with the low result of PolyReduce drawn around it as a pink wireframe, the Template flag set on PolyReduce"> | <img src="docs/images/display-flag.gif" width="420" alt="The Display flag moved down a stack of Mountain, PolyReduce and PolyExtrude on Suzanne, the viewport showing each output in turn"> |
| *Template* draws a result as a pink wireframe while the stack shows as usual: here the cage of PolyReduce around the smooth rock that Blender's Subdivision makes of it. | *Display* shows the output of one modifier and turns off the ones below until it is lifted. Here it moves down the stack, one click on an eye per step. |

### Finding tools

![The asset shelf popup over a rock, showing Houdini's Geometry tools with their icons](docs/images/asset-shelf.png)

`Ctrl+Shift+H` opens every tool as a thumbnail, with Houdini's icons and categories. That means your HDA folders, your Houdini packages such as SideFX Labs, and Houdini's own nodes. Click a tool, or drag it onto an object.

| Favorites pie, `Shift+Alt+H` | Search across everything |
|---|---|
| <img src="docs/images/favorites-pie.png" width="420" alt="Pie menu of favorite tools over a rock"> | <img src="docs/images/sidebar-search.png" width="320" alt="Sidebar search for scatter across HDAs, Houdini nodes and Labs tools"> |

Starred tools stay in the sidebar and in the pie. *Apply to* chooses between a modifier on the active object and a new object.

<img src="docs/images/add-houdini-node.png" width="520" alt="The Add Houdini Node button under Add Modifier, its search open with poly typed">

In the Properties editor, *Add Houdini Node*, under *Add Modifier*, searches every tool as Houdini's Tab menu does, your favorites first. *Add Modifier > Houdini* lists your favorites.

### Parameters

| Tabs, menus, a group field with its arrow | Repeated blocks, nested (Houdini multiparms) |
|---|---|
| <img src="docs/images/modifier-parameters.png" width="340" alt="PolyExtrude in the modifier panel: its group field with the list of groups and the arrow, menus, a row of tabs and boxes"> | <img src="docs/images/modifier-multiparm.png" width="340" alt="Two bumps of Bridge Ramps, each in a box, with + and - on the counts and two rings nested in the first bump"> |

The panel draws the parameters in Houdini's order and folders: tabs, boxes, collapsible folders, and buttons and ramps at their place. Underneath they are Geometry Nodes inputs, so you can keyframe or drive them. A repeated block is a count with + and −, and each instance gets its own box.

### Ramps

![A plate shaped by a float ramp and colored by a color ramp, with both ramps in the panel of the modifier](docs/images/ramps-window.png)

Float and color ramps are edited with Blender's own curve and color ramp widgets, at their place among the parameters of the modifier.

### Modifier stack

![Four steps on Suzanne: Subdivision from Blender, Mountain from Houdini, Decimate from Blender, PolyExtrude from Houdini](docs/images/stack-window.png)

Houdini modifiers mix freely with Blender's. Each one receives what the modifiers above it produced.

The original mesh stays editable, with the whole stack live on top of it, as in the recording at the top of this page. For heavy tools, a preference waits until you leave Edit Mode before cooking.

### Groups

![Colored groups on Suzanne in Edit Mode, with the Groups panel in the sidebar](docs/images/groups-panel.png)

Make point, edge or face groups from an Edit Mode selection and show them in color. In Blender a group is a boolean attribute, and it reaches Houdini as a group.

![A band of points sweeping over Suzanne as a Group Create parameter changes, with the point count updating in the Groups panel](docs/images/groups-live.gif)

The groups a Houdini tool makes show up as soon as it cooks, on the result of the active modifier, with their count. Faces are filled, edges are thick lines and points are dots. Tune the tool and you see the group move. This GIF is stepped, so it does not show real-time speed.

![Suzanne's ears, brow and jaw groups before and after Remesh, wireframe showing the new topology](docs/images/groups-through-remesh.png)

Groups follow a change of topology. Here they carry through Houdini's Remesh.

![Two HDAs on Suzanne: the first makes the group right, the second inflates that half, its Group field in the modifier panel](docs/images/groups-through-stack.png)

One HDA's group feeds the next. The first makes the group `right`, and the second inflates that half.

![PolyExtrude's group field filled from the viewport: the sphere in Edit Mode in an orange frame, faces clicked one by one, then Enter and the extrusion follows](docs/images/group-selection.gif)

The arrow of a group field selects the group in the viewport, as in Houdini. The input shows in Edit Mode in an orange frame, Enter writes the selected points, edges or faces into the field, and Esc cancels. This GIF is stepped, one click per frame.

### Visualize

| Attribute as a color ramp | Vector as arrows |
|---|---|
| ![Curvature on Suzanne as a color ramp with its legend](docs/images/visualize-curvature.png) | ![A flow vector on Suzanne as yellow arrows of varying length, with the Visualize menu in the Output section of the modifier](docs/images/visualize-tangents.png) |

The *Visualize* menu of a modifier draws any attribute of a result in the viewport, with a legend. There is no need to build a material to check your data.

### Attributes

![Weight Paint strokes on a sphere, with a second sphere displaced by them as each stroke lands](docs/images/attributes-painting.gif)

*One short stroke per frame, so this does not show real-time speed.*

**Blender to Houdini.** Paint weights and read them in a wrangle, a Houdini node that runs a line of code. Here it is `@P += @N * f@Weight * 0.55;`, which pushes the surface along its normals. Colors, vectors and UV maps go through too.

**Houdini to Blender.** The result's attributes come back for shaders and Geometry Nodes.

### Output types

Results arrive as Blender's own data types.

| Instances | Curves |
|---|---|
| ![Spheres and boxes instanced, each copy with its own color](docs/images/stream-instances.png) | ![Grass as Blender curves with color and width](docs/images/stream-grass.png) |
| Copies become Blender instances, each shape sent once (Houdini packed primitives). | Open curves become a Curves object, with color and width. |

| Points | Volumes |
|---|---|
| ![Fireflies as a point cloud on a dusk sky](docs/images/stream-points.png) | ![A cumulus cloud made by Houdini's Cloud and Cloud Noise nodes, stacked as modifiers](docs/images/stream-volume.png) |
| Loose points become a point cloud (`pscale` sets the radius). | VDBs, Houdini's volume format, become a Volume object. This cloud is Cloud then Cloud Noise, stacked as two modifiers. |

| Fragments | Many instances |
|---|---|
| ![A sphere cut into pieces with an orange inside material](docs/images/stream-fragments.png) | ![A field of instances from a low camera, sharp in front and dense behind](docs/images/stream-many-instances.png) |
| One shape per piece, inside faces in their own material. | 100,000 copies cooked in about 0.1 s on the development machine. Past 200,000, the viewport draws points and the render still gets the instances. |

Set *Output* to *Mesh* to get one editable mesh.

### Timeline and cache

![A wrangle reading the time, recooked on each frame of the timeline](docs/images/timeline-playback.gif)

Houdini follows Blender's frame and frame rate. A tool that reads the time recooks as the frame changes, and so does a tool whose input is animated. This GIF is stepped frame by frame, so it shows the result rather than real-time speed.

| Cache a range | Then play and render without Houdini |
|---|---|
| <img src="docs/images/cache-section.png" width="320" alt="The Cache section of a modifier with Scene Range, Cache Again and Use Cache"> | Like a File Cache in Houdini: cook a range once, and Blender plays it back frame accurately, with attributes, groups and materials. Cached meshes are saved in the .blend file. |

### Freezing results

![The Apply buttons in the node rows of two Houdini modifiers, beside the frozen rock](docs/images/apply-freeze.png)

- **Apply** freezes one result and drops its link to Houdini. The mesh becomes the object's mesh. Instances stay instances, curves become a Curves object, points a point cloud object and a volume a Volume object. *Apply All* does the whole stack.
- **Cache** keeps an animation. It stores meshes only.
- **To hand a file to someone without Houdini**, apply or cache first. An applied volume is a `.vdb` file next to the .blend, so send it too.

Use the *Apply* button of the Houdini modifier, or *Apply All* in the Houdini sidebar. Blender's own Apply, in the modifier's menu, keeps only the mesh.

### Several inputs and generators

![Boolean subtracting a wire cutter from a rock, with the Cutter object in the Geometry B field of the modifier panel](docs/images/multiple-inputs.png)

A tool can take up to 4 inputs. Input 1 is the object with the modifiers above it, and the others are object pickers in the modifier. Other selected objects fill them when you apply the tool. Here Boolean subtracts a second object, Cutter, from a rock.

A generator is a tool without inputs, such as Grid or Font. It makes a new object at the 3D cursor.

### Editing an HDA in Houdini

Save an HDA again in Houdini and Blender reloads it on the next cook. The parameter list is rebuilt and the values already set are kept. Keyframes and drivers stay as long as a parameter keeps its name and type.

When the shelf holds a newer version of an HDA, an *Update* button on the modifier switches to it.

### Status and cooking feedback

| Server controls | Cooking feedback |
|---|---|
| <img src="docs/images/header-popover.png" width="400" alt="Popover with the Houdini version, Restart, Stop, Show Log and Preferences"> | <img src="docs/images/cooking-feedback.png" width="400" alt="An orange box around a rock with Remesh 0.8 s and a summary badge"> |

The header dot shows stopped, starting, ready, cooking or failed. The object being cooked gets a pulsing box and a timer, and a failed cook gets a red badge.

*Stop When Idle* frees the licence after some idle minutes. Every setting is in the [guide](docs/GUIDE.md#preferences).

## Demo file

![All 13 numbered demo scenes in two rows, rendered in daylight](docs/images/demo-overview.png)

Open `demos/visualization_demo.blend` from a clone of the repository, so it can find the test HDAs in `tests/hda/`. Houdini starts and recooks everything when the file opens. With *Auto Run Python Scripts* off in Blender's preferences (*Save & Load*), the file opens with the results saved in it and cooks nothing: click *Allow Execution* in the panel of any Houdini modifier, or in the *Houdini Bridge* menu of the viewport header, and it cooks for this session. Scenes 11 and 13 are empty until then. The scenes are numbered, and their labels sit in the viewport.

| Scene | Name | What to try |
|---|---|---|
| 1 | Group through Remesh | Select it and open the Groups panel: the face group `cap` shows on the Remesh result. |
| 2 | Curvature | Select it and read Measure's curvature in the *Visualize* legend. |
| 3 | Tangents | Select it and see PolyFrame's surface direction drawn as arrows. |
| 4 | Groups made by an HDA | Select it: the HDA's groups show at once, among them the edge group `right_edges`, built on the face group `back` sent to it. |
| 5 | Group through the stack | Open both modifiers: the first makes `right`, the second uses it in its *Group* field. |
| 6 | Time, live | Press play. The HDA reads the time, and Measure stacked on it follows. |
| 7 | Cache | Scrub frames 1 to 48: a rock following a keyframed sphere plays from its cache. |
| 8 | Instances | Change *Count*: 1,500 copies of 3 shapes, colored per copy. |
| 9 | Open curves | Press play: 6,000 grass blades sway, with color and width. |
| 10 | Loose points | Press play: the fireflies move, and `pscale` sets their radius. |
| 11 | Volume | Switch the viewport to Rendered to see the fog filling a sphere. |
| 12 | Fragments | Change *Pieces* or *Explode*: a sphere cut into 40 pieces, inside faces in their own material. |
| 13 | Many instances | Render it: 100,000 copies are points in the viewport and instances in the render. |

The picture is a render with shortened labels, and the hero scene and the cloud are not in this file: `docs/capture_images.py` builds them.

## Limitations

- Geometry (SOP) HDAs and nodes only, with 4 inputs at most.
- The panel of each Houdini modifier starts folded: Blender keeps that state per modifier and offers no way to set it.
- Volumes, instances and curves pass between Houdini modifiers only when they are directly stacked. With a Blender modifier in between, only the mesh gets through.
- Blender's own Apply keeps only the mesh. Use the *Apply* button in the node row of the Houdini modifier's panel to keep the rest.
- The cache stores meshes. Curves, points and volumes are not cached.
- Live cooks pause while Blender renders, so cache an animated modifier before rendering an animation.
- An object with several materials sees all the faces of a Houdini result take its first material slot: the faces' `material_index` is not sent to Houdini.
- Windows only in practice, and Sculpt Mode edits are unverified.

The full list is in the [guide](docs/GUIDE.md#limitations).

## More documentation

- [docs/GUIDE.md](docs/GUIDE.md): every feature in detail, preferences tab by tab, the complete limitations, [troubleshooting](docs/GUIDE.md#troubleshooting).
- [Scripting](docs/GUIDE.md#scripting): `api.py`, the module a Python script uses.
- [DEVELOPMENT.md](DEVELOPMENT.md): architecture, then protocol, features, tests and pitfalls in [docs/dev/](docs/dev/), for anyone forking the project.
- [CONTRIBUTING.md](CONTRIBUTING.md): building, the test suites, the rules and the names that never change.
- [CHANGELOG.md](CHANGELOG.md): what each release holds.
- [SECURITY.md](SECURITY.md): what opening a file runs, and how to report a vulnerability.

## Licence

GPL-3.0-or-later, see [LICENSE](LICENSE).

Houdini is a product of SideFX. This project is not affiliated with or endorsed by SideFX, and it does not include any part of Houdini.
