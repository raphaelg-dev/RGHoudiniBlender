# RG Houdini Bridge: building and testing

What the build does, what each suite covers and what some tests pin down, the demo file and the documentation images. Part of the developer documentation: start with [DEVELOPMENT.md](../../DEVELOPMENT.md).

- [Building](#building)
- [Tests](#tests)
- [Demo file and documentation images](#demo-file-and-documentation-images)

## Building

[CONTRIBUTING.md](../../CONTRIBUTING.md#build-and-install) gives the commands, by hand outside Windows too.

The script stops if `blender.exe` is not at the default 5.2 path or at `-Blender`. Building needs Blender but not Houdini. It copies `LICENSE` into `addon/` for the zip and removes the copy afterwards.

Extension manifest tags come from a fixed list.

The manifest sets `blender_version_min = "5.2.0"` and `blender_version_max = "5.3.0"`, which Blender reads as exclusive (`filter_blender_version >= version_max` in its `bl_pkg`): every 5.2.x installs, no older version and no 5.3. The modifier panel hangs on an internal panel of Blender and avoids two of its crashes (see the Blender pitfalls), so the maximum moves only once the crash probes have run again on the new version.

## Tests

The Blender suites and the demo script use the **installed** extension, so run `build.ps1` first. Every suite but the pure tests needs Houdini, and each one ends with exit code 0 when it passes. Run one suite at a time: each one starts its own hython. [CONTRIBUTING.md](../../CONTRIBUTING.md#tests) gives the commands, what each suite needs and how long it takes; `.github/workflows/ci.yml` runs ruff and the pure tests on Linux at every push.

Rebuilding the test HDAs (only when `make_test_hdas.py` changes):

```powershell
$env:HOUDINI_AUTHOR = "bridge@localhost"             # keeps your login and machine name out of the HDA
hython tests/make_test_hdas.py                       # writes tests/hda/bridge_test.hda
```

### The door into the extension

`tests/support.py` is the only file of `tests/` that knows where the modules of the extension live (`MODULES`) and the only one that reads or writes their private names, each behind a function named after what a test needs (`is_requested`, `peek_request`, `poll_inputs`, `simulate_idle`…). It reaches the cook engine through public names only.

The private names it still reads are listed at its top, each as fault injection (the Windows registry of `hython.py`, replaced; tools and favorites set in memory) or as measurement (icons loaded, what the Groups panel draws, the caches keyed by a mesh pointer, the time a cache spends, the batches the Template flag makes and its draws).

When a module moves or an internal changes, that file is the one to fix; its keys are the modules' own names (`catalog` is the shelf's, `server_catalog` is `houdini/catalog.py`; `streams` is the package `blender/core/streams/`, `streams_<file>` its files, `server_streams` is `houdini/streams.py`; `parameters` is the package `blender/core/parameters/`, `parameters_<file>` its files; `mesh_io` is `blender/core/mesh_io/convert.py`, the part of its package that loads without `bpy`, and `mesh_io_read`, `mesh_io_write` its other files; `api` is `api.py`).

`source(name)` loads one of the modules without `bpy` (listed under [Rules](../../DEVELOPMENT.md#rules)) from `addon/` through synthetic packages that skip their `__init__`.

### The suites

- `tests/unit/` runs with any Python that has numpy:
  - the wire format (`test_protocol`), the protocol contract and the code that must go through it (`test_contract`);
  - the numpy side of the mesh conversion (`test_mesh_math`);
  - the two stream registries, where a stream may be named, and the files of the streams read from the registry (`test_stream_registry`);
  - the registries of parameter kinds and the panel's rows, the fields each kind sends, and a kind named by its constant (`test_parm_registry`);
  - finding hython, a failed start that stops it, a `stop()` from two threads and one during a request, whether a process runs (`test_client`);
  - the mount of `hython_entry.py` and its refusal of an older Python (`test_hython_entry`);
  - the signals (`test_events`), `CookState` (`test_cook_state`), the icons drawn by code (`test_icons`), the cuts of a stack (`test_stack_cuts`);
  - the architecture rules (`test_architecture`, see [Rules](../../DEVELOPMENT.md#rules)) and the documents (`test_docs`, below).
- `tests/test_server.py` runs with any Python that has numpy, Blender's included.
  - After the main connection is closed, it starts hython again, one at a time: the probe (what the mounted package lets hython import, and that a cook of an unknown node type leaves nothing in `/obj`), then a hello with another protocol version, with none and with a wrong token (the server answers an error and exits by itself), and a client of another version (it names both and stops its hython); then, on Windows, a client of its own killed while its hython starts, after which no hython may be left (`check_killed_client`).
- `tests/test_blender.py` is end to end. `BRIDGE_TEST=ramps_multiparms,streams` runs only those tests. The whole suite runs in a temporary user folder.
- `test_registration` describes everything the extension registers (operators and their properties, panels, menus, the asset shelf, lists, property groups, preferences, the properties attached to Blender's types, the add-on hotkeys) and compares it with `tests/snapshots/registration.json`. These names are stored in .blend files and keymaps, so any difference fails and prints the diff. `BRIDGE_UPDATE_SNAPSHOT=1` rewrites the file, for a change that is meant. The snapshot holds names, types and defaults, not the descriptions shown as tooltips.
- `test_native` checks the Houdini interface (creation, rebuild, the preference), what the panel draws for the ramps and multiparm HDA and for Poly Extrude (order, folders, tabs, hidden, faded), the + and - of a multiparm, a keyframe and a driver on an input the panel draws, the tabs (no data written), Lock, the navigation from the sidebar and the Add Modifier sub-menu; `test_lock_inputs` edits and moves the inputs of a locked modifier, which the cook asked for by lifting the lock reads; `test_lock_on_load` opens a file with a locked modifier.
- `test_code_editor` checks that an Attribute Wrangle's snippet is VEX and a Python SOP's script Python, the code block's preview and errors, one mirror per parameter, what is typed written once typing stops and cooked, the parameter followed, a VEX error, a saved file without the mirror, and the mirror gone with its modifier; `test_ui.py` draws the block with the Houdini interface on and off and opens the editor window (once), cooks through the timer, closes it.
- The flags:
  - `test_template` checks what the Template flag draws (cooked, bypassed, in error, hidden, two at once);
  - `test_display` the Display flag (the rule, moved, modifiers moved, added, removed or turned on by hand under it, Bypass on it and under it, Template beside it, a duplicated object, undo and redo, the flagged modifier removed and applied);
  - `test_display_and_selection` a flag set during a group selection and the other way round;
  - `test_display_files` a file saved with a flag, opened again with the extension and by a Blender of its own without it, and leaves a flag set, which `test_reenable` sees lifted when it disables the extension.
- `test_compat` opens `tests/fixtures/compat_0_17.blend`, a file saved before 1.0 (`make_compat_fixture.py` wrote it once, with a pre-release build: the oldest format the extension reads), and checks it against `compat_0_17.json`: settings, parameter values of every kind, a keyframed input, ramps, a multiparm count, groups of the Groups panel, an instance stream, two chained Houdini modifiers and a cache of a few frames. Then every live modifier cooks again with its values in place. It opens a file, so it runs near the end, and `BRIDGE_TEST=compat` runs it alone.
- `test_auto_run` opens a file with *Auto Run Python Scripts* off: nothing is asked for, a cook asked for is dropped and hython does not start, until *Allow Execution* or Recook allows the file; a file in an Excluded Path is blocked too, and one elsewhere cooks at once. `test_auto_run_reload`, last, opens a blocked file, imports the extension's modules anew as an update does (`support.reload`), and checks that it is still blocked and nothing cooks. The suites turn the preference on otherwise (`support.enable`): the files they open are their own.
- `test_apply_refused` refuses, before anything changes, a shared mesh, a mesh with shape keys and a volume whose live `.vdb` is gone.
- `test_api` places, sets, cooks, caches and applies through `api.py` alone.
- `test_reenable`, near the end, disables the extension, checks that no handler, timer, listener of a signal, `houdini-bridge-*` thread or `GeometryNodeTree.houdini_bridge` is left, enables it again, checks that each signal has its listeners once, and cooks a new modifier.
- `tests/test_ui.py` covers the asynchronous path (timer and worker thread), which `--background` cannot.
  - Each step checks what must be true, a wait that runs out is a failure, and a watchdog thread stops the suite after `BRIDGE_UI_WATCHDOG` seconds (300 by default). Steps that need SideFX Labs are skipped when Labs is not in the catalog. It writes screenshots to `tests/screenshots/`, the node flags in their states among them (the Template batch made once per result, not at each frame).
  - Its last steps are on a cook of a 250,000 face grid caught in flight: a value typed during it, whose result must then show (the same points as a recook); then failures, hython killed (the modifier shows the error, nothing loops, the Start button recovers), the server stopped with a cook waiting, the extension disabled during a cook and during a start (no thread and no hython left, read from the operating system, and no draw of the Template flag), and another file opened.
  - It also undoes for real (`bpy.ops.ed.undo`): back to the step of a parameter change, which must show that value's result, and during a cache of 48 frames, which must stop it and leave the modifier cooking.
  - Started with `--enable-event-simulate`, it runs one step alone instead, the viewport group selection (`tests/ui_selection.py`): simulated clicks in the 3D view, Enter and Esc read by the modal operator, real undo and redo after Enter in place and on a copy (no copy, hidden object, cut or marker left, no crash), the ways a selection ends (Esc, Tab, a save, undos past its start, an undo inside it), edges, a locked modifier below, a chained scatter above (refused), and the extension disabled during a selection.
  - Such a Blender reads no event of the system and draws its windows only when asked, which the other steps need; so the suite runs twice.

**Test HDAs** (`make_test_hdas.py`): `bridge::rock`, `rings`, `groups`, `animated`, `scatter`, `grass`, `fireflies`, `fog`, `shatter` and `ramps`. Each exists to exercise one path, as listed at the top of the script.

What some tests pin down:

- `test_streams` compares the realized instances with Houdini's own unpack (Output Mesh), for a scatter with rotation and non-uniform scale and for Voronoi fragments, with point sets matched by KD-tree. It also rebuilds a level set's zero level with Blender's Grid to Mesh and checks it wraps the input.
- `test_cache` checks that each cached frame equals the live cook.
- `test_hide_when` (and the hide-when checks of `test_server.py`) use native nodes, Poly Extrude, Sphere, Color and Group Create, whose conditions are known: the test HDAs have none. Poly Extrude's Pivot Transform and Pre-Transform check a hidden folder: their parameters have no condition of their own.
- `test_ui` caches 48 frames with the button and plays an animation.

### The registries and the documents

The two registries are checked without Blender or Houdini: `test_stream_registry` and `test_parm_registry` (see [Native streams](features.md#native-streams) and [Parameters](parameters.md#parameters)). `test_contract` keeps the table under [Protocol](protocol.md#protocol) equal to the contract.

`tests/unit/test_docs.py` keeps `README.md`, `CONTRIBUTING.md`, `DEVELOPMENT.md`, `docs/GUIDE.md` and the files of `docs/dev/` true to the code.

Every path of the repository they name between backquotes exists, found by its path or by the end of one (`cook/request.py`); every `module.name` whose module is a module of the extension (`link.ServerLink.request_sync`, `cook/scheduler.tick`) names a function, a class, a constant or an imported name of that module, read with `ast`, and `module.Class.member` a member of that class; the tree under [Repository map](../../DEVELOPMENT.md#repository-map) lists every folder and every file of `addon/` but the empty `__init__.py` files, and nothing else.

It cannot check prose: what a sentence says a function does, a name written without backquotes, a Blender or Houdini name (`hou.…`, `bpy.…`), an attribute of a stored setting (`settings.…`), which module is meant when a name ends two module paths (`streams.write` passes when `blender/core/streams/` or `houdini/streams.py` has it), or the anchors of the links.

## Demo file and documentation images

`demos/make_visualization_demo.py` rebuilds `demos/visualization_demo.blend`:

```powershell
blender --background --factory-startup --python demos/make_visualization_demo.py
```

It stores HDA paths relative to the file. Scenes 11 and 13 are emptied before saving and recook when the file opens: scene 13 to keep the file small, scene 11 because a saved volume holds the absolute path of its `.vdb`. Unset `OCIO` first if your machine sets it, or the file stores that config's color settings. The script starts from the factory scene, not your `startup.blend`.

`docs/capture_images.py` rebuilds everything in `docs/images/`:

```powershell
blender --factory-startup --python docs/capture_images.py
```

- It needs the installed extension, Houdini and `ffmpeg` on PATH. It opens its own window without the `OCIO` variable and ignores the mouse and keyboard while it runs. The window asks for 1920x1080, but the system keeps it within the work area of the screen: on a 1080-pixel screen with a taskbar it comes out shorter (1920x1001), and so do the shots cropped to its height.
- `CAPTURE_ONLY=shelf,streams` retakes only those shots: the names of the `shot_` functions, without that prefix.
- It works on copies of the demo and the HDAs in a temporary folder, and leaves the preferences alone. The tools of the other packages of the machine are hidden with the *Hide* preference of that session, so the shelf shows Houdini's nodes, Labs and the test HDAs.
- Blender folds the Houdini panel of each new modifier and Python cannot unfold it (see [Pitfalls](pitfalls.md#pitfalls)), so the script clicks its node row, found in a screenshot by the color of its state dot, as a user does. The flags of the node row and the Output section are clicked the same way, and the positions it clicks are set at its top for a UI scale of 1.
- It builds the hero scene and the cloud itself. Those are not in the demo file.
- `stream-fragments.png` uses 110 pieces and the hero 150, against 40 in the demo file. `demo-overview.png` is a Cycles render of a temporary copy of the demo, with shorter labels than the file's.
- `live-edit.gif` is cut from `live_edit.mp4`, a screen recording kept next to it in `docs/images/` but not in the repository. `banner.png` is a screenshot made by hand too: no script rebuilds it.
- Captions use Segoe UI, so a run outside Windows should set `CAPTION_FONT`. Caption text must avoid colons.
- A full run also writes `parameter-before.png`, `parameter-after.png` and `timeline-cache.png`, which the documentation does not use.
- The rock materials are reapplied by the script after the last cook, because each cook resets the holder mesh's material slots. A real HDA would set `shop_materialpath`.
- The GIFs are stepped frame by frame, so they show results and not interactive speed.

Both scripts use `api.py` where it does what they need, and reach the other modules they use through a table at their top (`EXTENSION_MODULES`), the one place to fix when a module moves.
