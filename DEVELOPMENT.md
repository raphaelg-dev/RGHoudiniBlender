# RG Houdini Bridge: development

How the extension works inside, and where to add something. User documentation is in [README.md](README.md) and [docs/GUIDE.md](docs/GUIDE.md); building, testing and proposing a change in [CONTRIBUTING.md](CONTRIBUTING.md).

The first hour: read [What it is](#what-it-is), then [The path of a cook](#the-path-of-a-cook), then the recipe of [Adding something](#adding-something) that fits your change. The map below says where each file is.

- [What it is](#what-it-is)
- [Repository map](#repository-map)
- [Rules](#rules)
- [The path of a cook](#the-path-of-a-cook)
- [State and threads](#state-and-threads)
- [Signals](#signals)
- [Adding something](#adding-something)

The rest is in [docs/dev/](docs/dev/):

- [protocol.md](docs/dev/protocol.md): the wire format, the commands and their fields
- [parameters.md](docs/dev/parameters.md): the parameters and the code parameters
- [features.md](docs/dev/features.md): the shelf, groups, viewport selection and drawing, the flags, timeline, cache, native streams, chained modifiers, Apply, preferences
- [testing.md](docs/dev/testing.md): building, the suites, the demo file and the documentation images
- [pitfalls.md](docs/dev/pitfalls.md): Blender and Houdini pitfalls, measurements, known limitations, ideas for forks

## What it is

A Blender 5.2 extension, in Python, that runs Houdini HDAs and nodes as Geometry Nodes modifiers. It runs as two processes on one machine:

- **Blender** runs the add-on: the panels, the modifiers, and the cook engine, which reads the inputs of a modifier, sends them, and writes the result back into Blender's data.
- **hython**, Houdini's Python shell, runs a server the add-on starts on first use. It keeps one Houdini network per modifier, cooks it and sends the result back.

They talk over TCP on 127.0.0.1, with a random token: a JSON header, then binary blocks. Geometry on the wire is always in Houdini space, and the Blender side converts. Both sides are Python with the standard library and numpy, and no compiled module.

```
Blender: __init__.py, blender/                      hython: hython_entry.py, houdini/
  ui/: preferences, operators, Houdini panel           server.py: hello, HANDLERS, Server.cook
  features/: cache, groups, shelf, viewport            instance.py: /obj/houdini_bridge, one geo per modifier
  core/cook/: the queue, the main-thread timer           stash (inputs) -> HDA -> classify -> unpack
  core/link.py: the worker thread, the socket            -> convert -> keep_polygons -> export_topology
  core/mesh_io/: numpy, Z up <-> Y up,                 parms.py: describe and write the parameters
                 face winding reversed                 geometry.py, streams.py: what goes back
  core/parameters/, core/streams/: the node group      returns P, counts, indices, uv, N, Cd, materials,
                                                         attributes, groups, streams
        '---- TCP 127.0.0.1, token, JSON header + binary blocks (shared/protocol.py, shared/contract.py) ----'
```

### hython and Houdini Engine

This project uses hython rather than the Houdini Engine API; see your licence terms.

The add-on drives a `hython` process, which holds a Houdini licence as long as it runs. Which licence depends on the licences installed: on an Indie installation, hython was seen to take a Houdini Engine Indie licence first, when there is one. Houdini's License Administrator shows which licence is taken. *Stop When Idle* stops hython after some idle minutes, which releases it. A HAPI back end would be possible, and the protocol and the Blender side could stay the same.

## Repository map

Empty `__init__.py` files are not listed.

```
addon/                  the extension, the package Blender installs (bl_ext.user_default.rg_houdini_bridge)
  blender_manifest.toml
  __init__.py           Blender's entry point: the ordered list of the modules and features it registers
  hython_entry.py       hython's entry point: mounts the folder as a package, starts the server
  api.py                the scripting interface: the only module whose names stay from one version to the next
  shared/               imported by both processes (standard library and numpy only); imports only shared/
    protocol.py         wire format: a JSON header and binary blocks
    contract.py         what a header holds: version, commands, fields and their defaults, answer keys (standard library only)
  houdini/              runs in hython only; imports shared/ and houdini/
    server.py           main(), the hello, the Server class and its table of commands, the cook
    instance.py         Instance: the Houdini network of one modifier, its inputs and its stream branches
    parms.py            describing the parameters of an HDA and writing their values: the registry KINDS
    script_action.py    reading the script_action tag of a group field: what its viewport selection takes (no hou)
    geometry.py         meshes, groups, attributes and volumes in, the mesh of a result out, numeric attributes
    streams.py          native outputs: classification, exports, and the registry STREAMS (one class per stream)
    catalog.py          reads the tools and their icons
    output.py           log: what the server prints on its standard output, which the client reads
  blender/              runs in Blender only; imports flow one way: shared -> core -> features -> ui
    core/               the engine and the data it works on: no panel, and no operator but the mixin
                        modifiers.ModifierOperator; imports shared/ and core/
      paths.py          the package name Blender knows, the user folder and its subfolders
      feedback.py       log, the extension's logger, and redraw(), the areas to draw again
      icons.py          state dots and the node flags drawn by code, written as PNG files (no bpy)
      preferences.py    the default of every preference, get()
      trust.py          whether the open file may cook by itself: Blender's Auto Run Python Scripts, Allow Execution
      events.py         the signals the core emits for the features (no bpy)
      hython.py         starting hython, finding installs, the socket (no bpy)
      link.py           the link with the server: starting it, the worker thread, its queues, request_sync
      mesh_io/          mesh conversion between Blender and Houdini, attributes and groups
        __init__.py     what the callers use: mesh_io.read_object, mesh_io.write_mesh…
        convert.py      axes, face winding, topology digests (numpy only, no bpy)
        read.py         Blender to Houdini: groups, attributes, a mesh, an evaluated object, TO_HOUDINI
        write.py        Houdini to Blender: a mesh, its attributes and groups, point cloud and curves attributes
      parameters/       parameters as inputs of the modifier's node group
        __init__.py     what the callers use: parameters.read_values, parameters.sync_interface…
        kinds.py        the registry KINDS (Kind), kind_of, and the group fields of a string; no drawing
        interface.py    the node group: its inputs in panels, the usage chain, the nodes holding parameters, is_hidden,
                        who draws the inputs (apply_houdini_interface)
        layout.py       the parameters as the modifier panel lays them out, as data: Houdini's folders, tabs and order
        values.py       reading and writing values in the server's format, the limits they stay within
        ramps.py        the Float Curve and Color Ramp nodes holding ramps, and Houdini's interpolations
        inputs.py       the items of the modifier inputs: finding one, copying its value
      streams/          the native streams on the Blender side
        __init__.py     the registry STREAMS, and what walks it: write, clear, describe, the streams node, Apply
        common.py       Stream (what a stream declares), node group helpers, removing a holder, materials
        instances.py    the instances: point cloud, prototypes, the Houdini Instancer modifier, viewport display
        curves.py       the open curves: a Curves object
        points.py       the loose points: a point cloud
        volumes.py      the volume: a Volume object, its materials, the .vdb files Apply keeps, on_save
      targets.py        Target, finding the Houdini modifiers (houdini_tree, houdini_trees), their holders,
                        INPUT_TYPES (Blender data only)
      stored.py         JSON text kept in a string property of the .blend, read back (no bpy)
      stack_cuts.py     a slice of a modifier stack cut from the viewport and restored as it was, kept on the
                        object (the group selection and the Display flag); no bpy
      animation.py      whether an object or its mesh may change with the frame (no bpy)
      cook/             the cook engine; imports shared/ and core/, and only cook/ imports its files
        __init__.py     the facade: everything the rest of the extension calls, and how a cook goes through
        state.py        CookState: what was sent to Houdini, per modifier (no bpy)
        queue.py        the cooks asked for and in flight, the inputs marked, the caches being made
        request.py      building a request: reading the inputs and the parameters, peek_request
        result.py       applying a result and a description
        scheduler.py    the main-thread timer and its steps, cook_now, upkeep, commands, starting and stopping
        handlers.py     Blender handlers (depsgraph, frame, render, file, undo), timers, register()
      modifiers.py      creating, applying and removing a Houdini modifier, changing its HDA version
      properties.py     data stored in the .blend: settings of a Houdini modifier
    features/           optional features, each a module or a package with register() and unregister();
                        imports shared/, core/ and itself
      cache.py          frame cache, its operator and its section of the modifier panel
      code_editor.py    a code parameter edited in the Text Editor, in a window of its own: its mirror, Edit
      display.py        the Display flag: the viewport shows one HDA's output, the modifiers below it cut
      groups.py         Groups panel, its drawing and colors, the groups of a result
      selection/        selecting a group field's elements in the viewport; imports shared/, core/,
                        selection/ and viewport/overlay.py
        __init__.py     registers mode.py
        group_string.py Houdini group strings of numbers and edges, read and written (no bpy)
        session.py      entering a selection, reading what is selected, confirming it
        restore.py      what a selection changes, written down (also on the scene), and putting it back
        mode.py         the modal operator, the border and badge over the 3D view, the handlers that end it
      shelf/            the HDA shelf; imports shared/, core/ and shelf/
        __init__.py     registers catalog.py, then assets.py, then add_node.py
        catalog.py      catalog, favorites, sidebar shelf, Shift+A menu, Add Modifier sub-menu, newer versions
        assets.py       asset library, asset shelf, favorites pie, hotkeys
        add_node.py     Add Houdini Node: the button under Add Modifier, a search of every tool
      viewport/         viewport drawing; imports shared/, core/, viewport/, groups.py and cache.py
        __init__.py     registers overlay.py, then status.py
        overlay.py      cooking boxes and viewport badges
        visualize.py    viewport visualizers
        template.py     the Template flag: an HDA's result drawn as a ghost wireframe
        status.py       header button and connection states
    ui/                 the shared interface; imports shared/ and all of blender/
      preferences.py    the six preference tabs, their callbacks and operators
      operators.py      the operators of the Houdini modifiers
      modifier_panel.py the Houdini modifier in the Properties editor: a child panel of every Geometry Nodes modifier
      parameter_rows.py the rows of the parameters in that panel: ROWS, one per kind, and the painter of the layout
      panel.py          the Houdini sidebar: its tabs, and the overview of the Modifiers tab
build.ps1               builds dist/rg_houdini_bridge-<version>.zip and installs it
pyproject.toml          ruff configuration (lint only)
CONTRIBUTING.md         building, the suites, the frozen names, style
CHANGELOG.md            what each release holds, for users
SECURITY.md             what opening a file runs, and how to report a vulnerability
.gitattributes          line endings, and the binary files
.github/
  ISSUE_TEMPLATE/
    bug_report.yml      the fields a bug report asks for
    config.yml          no blank issue
  workflows/
    ci.yml              ruff and the pure tests, on every push and pull request
demos/                  the demo file and the script that rebuilds it
  make_visualization_demo.py    rebuilds the demo file
  visualization_demo.blend      the demo file
docs/                   the guide, its images and capture_images.py
  GUIDE.md              the user guide
  dev/                  the developer documentation this file links to
    protocol.md         the wire format, the commands and their fields
    parameters.md       the parameters and the code parameters
    features.md         the shelf, groups, selection, viewport, flags, timeline, cache, streams, Apply
    testing.md          building, the suites, what the tests pin down, the demo file and the images
    pitfalls.md         Blender and Houdini pitfalls, measurements, known limitations, ideas for forks
  capture_images.py     rebuilds docs/images/
  images/               the pictures of the README and the guide
tests/
  support.py            the only door into the extension: module names, private names, shared helpers
  test_server.py        the hython server alone
  hython_probe.py       run by test_server.py in hython: what the mounted package lets hython import
  test_blender.py       end to end inside Blender, without a window
  test_ui.py            the asynchronous path, in a window
  ui_selection.py       its step of the viewport group selection, with simulated events
  unit/                 pure tests, neither Blender nor Houdini, and the architecture rules
  snapshots/registration.json   everything the extension registers
  fixtures/             compat_0_17.blend and .json, a file saved before 1.0
  make_compat_fixture.py        wrote that fixture once, with a pre-release build
  make_test_hdas.py     builds the test HDAs (hython)
  hda/                  bridge_test.hda, built by make_test_hdas.py
```

### Two entry points

Blender imports the package and runs `__init__.py`. hython runs `hython_entry.py` (`hython.Connection.start` launches `hython hython_entry.py`), which never runs `__init__.py`: that file imports `bpy`. The script takes its own folder off `sys.path`, where hython put it, declares that folder as a package named `rg_houdini_bridge`, then imports `rg_houdini_bridge.houdini.server` and calls `main()`.

Both sides import relatively (`from ..shared import protocol`), and nothing of the extension imports by a bare name in hython, where the Python of the HDAs runs too. On the Blender side, `paths.PACKAGE` is the name Blender knows the package by (`bl_ext.user_default.rg_houdini_bridge` once installed), which the preferences and the user folder need; `blender/core/paths.py` is the only module that reads `__package__`, and takes the root package's name from its own.

`__init__.py` registers its `MODULES` in order and unregisters them in the reverse one. The order matters to Blender: the preferences first, and a `PropertyGroup` before anything that refers to it.

### Scripting interface

`api.py` is what scripts import (`from bl_ext.user_default.rg_houdini_bridge import api`): placing a tool, setting a parameter, cooking, caching and applying now, listing the Houdini modifiers of an object, refreshing the shelf, starting and stopping the server. It is the only module whose names promise to stay. Each function delegates in one line to the module that does the work and carries the whole docstring.

A function joins it only when a script needs it; `demos/make_visualization_demo.py` and `docs/capture_images.py` use it where it fits. The [guide](docs/GUIDE.md#scripting) has an example.

### Logging

Both sides log through Python's `logging`, under one logger each. On the Blender side it is `feedback.log` (`blender/core/feedback.py`), named after the extension's package, which prefixes every message with `RG Houdini Bridge:` (a `logging.LoggerAdapter`) and is given no handler: its warnings and errors go where Blender's Python sends them, the system console.

On the server it is `log` (`houdini/output.py`), which writes each message alone on a line of hython's standard output and does not propagate: the client looks there for the line `HOUDINI_BRIDGE_PORT <n>` and shows the rest in *Server Log*.

## Rules

The code follows a few principles:

- The path of a file says which process runs it: `blender/` in Blender, `houdini/` in hython, `shared/` in both.
- Dependencies go one way, and the core knows no optional feature. Where a feature must hear about something the core does, the core emits a [signal](#signals).
- The cook reads in one go: the parameters and the streams are part of the core, called directly by `cook/result.py` in a visible order. Signals only tell the features, and never move a cook forward.
- A registry, a table in one file, where a fork will add something: streams, kinds of parameters, protocol commands. It holds what several entries do, each its own way; what one entry alone does is a plain function, called where it is needed. An entry with no state is a frozen dataclass of functions (`Kind`, `Row`, the Blender `Stream`, the sidebar's `Tab`); a server stream is a class, since each `Instance` makes its own, with its nodes.
- Every state has one owner (see [State and threads](#state-and-threads)), and nothing is left alive after `unregister()`.
- A file is split when it has two jobs, not when it reaches a size.
- `shared/`, `blender/core/hython.py`, `blender/core/mesh_io/convert.py`, `blender/core/events.py`, `blender/core/animation.py`, `blender/core/cook/state.py`, `blender/core/icons.py`, `blender/core/stored.py`, `blender/core/stack_cuts.py`, `blender/features/selection/group_string.py`, `houdini/script_action.py` and `hython_entry.py` import neither Blender nor Houdini: the pure tests load them, and fail when one does.

`tests/unit/test_architecture.py` checks the rules below on the source, with `ast` and no tolerance: any breach fails.

| A module under | may import, of the package |
|---|---|
| `shared/` | `shared/` only; outside it, the standard library and numpy |
| `houdini/` | `shared/` and `houdini/`; never `bpy` or its family |
| `hython_entry.py` | nothing, at the top of the file |
| `blender/core/` | `shared/` and `blender/core/`; never `hou` |
| a feature of `blender/features/` | `shared/`, `blender/core/` and itself; `viewport/` also `groups.py` and `cache.py`, `selection/` also `viewport/overlay.py` (its badge), the declared exceptions |
| `blender/ui/` | `shared/` and all of `blender/` |
| `__init__.py`, the root | `blender/` |
| `api.py` | `blender/`; nothing of the package imports it |

- No cycle in the imports at the top of the files, module by module, and no module of the package imported inside a function, where the cycle check would not see it.
- Outside `blender/core/cook/`, nothing imports a file of the cook engine or reaches one through the package: only its facade, `cook/__init__.py`.
- No nested function calls itself on the Blender side (see [Pitfalls](docs/dev/pitfalls.md#blender)).
- Every operator named by its `bl_idname` (a string `"houdini_bridge.name"`, or `bpy.ops.houdini_bridge.name`) is one some module defines: Blender draws a button for an unknown operator, and says nothing.
- Nothing in `addon/` changes `sys.path`, which Blender's extensions must not do.

Run it alone with `python -m unittest tests.unit.test_architecture` (any Python, from the root of the repository).

A failure lists each breach, named by its file (from `addon/`) and what it does: `blender/features/groups.py: blender.features.groups imports blender.features.cache` (a layer), `blender/core/low.py: imports blender.core.high inside a function`, `a -> b -> a` (a cycle), `blender/ui/panel.py: imports blender.core.cook.queue past the facade`, `blender/core/low.py: names houdini_bridge.nothing, which no module defines`.

`test_the_scan_sees_each_rule` runs the scan on a small tree of its own, so a rule that stops seeing breaches fails too. A new exception between features is a line of `IMPORT_RULES`, discussed first.

## The path of a cook

What a cook works on: a **target** (`targets.Target`) is one Houdini modifier. Everything hangs on the node group of a Geometry Nodes modifier:

- Settings live on the node group (`GeometryNodeTree.houdini_bridge`, see `properties.py`). Parameter values are inputs of the node group.
- The node group's only nodes are `Object Info` (Original space) to `Group Output`, pointing at a hidden **holder** object, plus the usage chain described under [Parameters](docs/dev/parameters.md#parameters) and the streams group node.
- The holder is linked to no collection. The node group is its only user, which keeps it alive in the file.
- The cooked mesh is written into the holder, in the owner's local space. Other inputs are brought into that space.

A new object is an empty mesh carrying a Houdini modifier. There is no other kind of target.

From a change of an input to the mesh written, file by file:

1. **Something changes.** The depsgraph handler (`cook/handlers.on_depsgraph_update`: an input object edited or moved), the 10 Hz poll of the modifier inputs (`cook/scheduler.poll_modifier_inputs`: a value, a keyframe, a driver), the frame handler (`cook/handlers.on_frame_change`) or an operator marks the inputs to read again in `CookState` (`cook/state.py`) and calls `cook.cooks.request` (`cook/queue.py`). Quick changes merge into one request.
2. **The timer takes it.** `cook/scheduler.tick` runs on the main thread. In order: upkeep, the poll, a new session begun (`begin_session`), the answers already arrived applied, other commands sent, then the pending cooks (`_send_pending`). Those need the server: `ensure_server` starts hython in the background through `link.server.ensure_running` and says yes once it answers. A modifier already in flight, or held back by a Houdini modifier above that is pending or cooking (`_waits_for_stack`), waits.
3. **The request is built.**
   - `cook/request.prepare_request` gives each input a state (`contract.INPUT_STATES`) by comparing its digests with what `CookState` says was sent.
   - It reads only what changed: `mesh_io.read_mesh` and `mesh_io.read_object` (`mesh_io/read.py`, groups and attributes included, then Z up to Y up and faces reversed), input 1 with the modifiers above it (`_read_up_to_modifier`), the Houdini modifier right above as a chain (`chained_above`), a Volume object as its file. The parameter values come from `parameters.read_values`.
   - The header is built by `contract.request(contract.COOK, ...)`, and what is sent is recorded. Nothing changed: no request.
4. **The worker sends it.** `link.server.post` queues it. The worker thread, `houdini-bridge-cook`, sends it with `hython.Connection.request` (`protocol.send`, then `protocol.receive` of `shared/protocol.py`) and queues the answer for the main thread.
5. **The server cooks.**
   - `houdini/server.main` reads each request and gives it to `Server.handle`, which refuses an undeclared field and calls the method `HANDLERS` names.
   - `Server.cook` installs the HDA (`Server.install`, again when its file changed), finds or makes the `Instance` of the modifier (`houdini/instance.py`), and sets its inputs: `geometry.import_mesh`, `Instance.update_input` for positions only, `geometry.import_volume`, `Instance.set_chain`. An input it does not have makes it answer `missing_inputs` instead.
   - Then the frame and frame rate, the values (`parms.set_parms`), the buttons, the output mode, and the cook of the export node. The answer is the description of the parameters (`Instance.describe`, which calls `parms.describe_parms`), the errors, the mesh (`geometry.export_mesh`) and, in native mode, the streams (`Instance.export_streams`, `houdini/streams.py`), with the timings.
6. **The result is written.**
   - A later tick delivers the answer (`_deliver`) to `cook/result.apply_result`. An error becomes the status, and the last result stays; `result.error_text` puts the last line of a traceback, its cause, first, which the panel, the sidebar and the viewport badge show. The inputs sent are forgotten and the values kept, so changing a value tries again.
   - The server answers a missing HDA file or an unknown node type in one line (`server.RequestError`), and an `Instance` it cannot build leaves nothing in `/obj`. Missing inputs ask for a new cook that sends them in full.
   - Otherwise, `apply_description` updates the parameter list (`parameters.sync_interface` when the signature changes, the folder types, `parameters.apply_houdini_interface`), `mesh_io.write_mesh` writes the holder (`mesh_io/write.py`, Y up to Z up), `events.result_applied` tells the Groups panel, `streams.write` writes the streams (`blender/core/streams/`), `CookState` counts the result, and the Houdini modifiers below are asked to cook again (`_recook_below`).
7. **Blender shows it.** The modifier's Object Info reads the holder, and the depsgraph evaluates the object again.

`cook.cook_now` does steps 2 to 6 at once, without the timer, through `link.server.request_sync`: background mode has no timer. The cache makes its own cooks with `cook.prepare_request` and `cook.post_cook`, and the shelf and the viewport send other commands with `cook.send_command`.

### Cook loop

- The server starts on first use. A failed startup is not retried in a loop: the Start button clears the error.
- A file Blender would not let run its scripts cooks nothing until the user allows it (`core/trust.py`): `cook/handlers.on_file_loaded` calls `trust.file_loaded` first, which blocks a file opened with *Auto Run Python Scripts* off, in one of its Excluded Paths, under `--disable-autoexec`, or after Blender blocked one of its scripts.
  - Then nothing is asked for at the load, and `_send_pending` drops what is asked for later, without starting hython. `cook.allow_file` lifts it and recooks every live modifier: *Allow Execution*, and the user's own actions (adding a Houdini modifier, Recook, Start, Cache, an HDA button). A synchronous `cook.cook_now`, which only a script calls, is not held.
  - Registering the extension with a file open (installed, updated, enabled, *Reload Scripts*) brings no `load_post`: `cook/handlers.register` calls `trust.forget`, and the first `trust.is_blocked` works it out again, since `register()` cannot read `bpy.data`. A permission given before is asked again.
  - Blender 5.2 does not tell an add-on whether it let a file run its scripts: `trust.auto_run` reads what it exposes, so a file opened as a Trusted Source while the preference is off asks too.
- hython ends with Blender, however Blender ends.
  - Closing Blender disables the extension, and disabling or reloading it shuts the link down: `hython.Connection.stop` closes the socket, waits `STOP_TIMEOUT` (3 s) for hython and kills it, during a cook too; a start still under way stops its hython when it ends. hython also exits by itself when its client disconnects, but only between two cooks, and while it starts it waits two minutes for a client.
  - So on Windows, `hython.die_with_this_process` puts hython, right after it is launched, in a job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, whose only handle Blender holds and never closes: when Blender's process ends, killed (`taskkill /F`) or crashed included, the system closes the handle and kills hython.
  - Without it, a Blender killed or crashed while hython started or cooked left hython holding its licence, and a Blender closed while hython started too, its startup thread dying with it. hython's own children stay out of the job (`JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`): an `hserver` it starts serves the other Houdini sessions. A job that cannot be made or joined leaves a line in *Server Log*.
  - Linux and macOS have no job: see [Known limitations](docs/dev/pitfalls.md#known-limitations).
- `cook/scheduler.tick` runs every 20 ms while busy or playing, and every 100 ms otherwise. A worker thread does the socket work.
- At most one request is in flight per object, so quick parameter changes merge into a single cook. A value changed while a cook is in flight is sent by the poll once it is back: an answer records the values only when it has just built the parameter list.
- Input states are `full` (whole mesh), `positions` (same topology, P and values only), `unchanged`, `none` and `chain`. The server asks again with `missing_inputs` for what it lost (restart, reloaded HDA).
- Every input read gives a content digest. A cook whose inputs and parameter values all match the last one sent is skipped. This is what breaks the feedback loop of modifier mode: writing the holder updates the owner, which marks input 1 dirty, which reads the same content and stops there.
- Input 1 (`is_self`) is the owner evaluated up to this modifier, or `obj.data` when nothing is above. `cook/request.py` (`_read_up_to_modifier`) hides that modifier and the ones below from the viewport for one evaluation. The depsgraph handler ignores the owner's updates during that evaluation (`request.own_updates.reading_stack`), not those of other objects, which may be a new result another modifier reads; the update of showing them again is recorded as its own.
- In Edit Mode, input 1 is read after `update_from_editmode()`, which triggers one more depsgraph update of the owner. That update is recognised (`request.own_updates.take`) and ignored.
- With *Cook on Exit*, edits of input 1 only mark it dirty. Leaving Edit Mode updates the mesh once more and triggers the cook.
- When the topology is unchanged, Blender only rewrites positions and attributes.
- An HDA saved again from Houdini is noticed by its file date on the next cook and reloaded. Each `Instance` keeps the date of the file it was made from, so every modifier using that HDA gets a new one at its next cook, not only the first to cook. The parameter list is rebuilt, keeping the values already set.
- Parameter values never come back from the server after creation, or a dragged slider would jump back. Only the hidden, disabled and driven (`expression`) states are refreshed. A value Houdini drives with an expression is not sent until it changes in Blender (see [Protocol](docs/dev/protocol.md#protocol)).
- Positions-only cooks call `hou.Geometry.incrementAllDataIds`. Without it, nodes caching on data ids (VDB from Polygons, for example) keep the old shape.
- During any render (`render_init` to `render_complete` or `render_cancel`), the engine timer and the frame handler do nothing. A live result written while Blender renders could change the scene under the renderer.
- Undo and redo (`cook/handlers.on_undo`) bring data back, not the server state.
  - A modifier whose instance was released, whose `.vdb` file is gone (`streams.volume_file_gone`), or whose holder came back with another result than the last one written is forgotten and cooked again from scratch. That last case is the common one: a value typed in the panel pushes its undo step before the cook it asks for, so undoing back to that step brings the value with the result before it.
  - Each result written stamps the holder (`result.COOK_STAMP`) with a text `CookState` never gives twice, after a restart of hython or in another session either (`CookState.stamp`), which `result.result_undone` compares with the last one written: a count starting again at 1 would take an older result for the last one.
- The caches keyed by a mesh pointer, the topology last written into each mesh (`mesh_io.forget_written`) and the drawings of the groups (`groups.forget_meshes`), are emptied when a file is loaded and after an undo or a redo: an address may then belong to another mesh.

### Upkeep

`cook/scheduler.maintain` runs once a second:

- A node group no modifier uses any more is released: holder deleted, server instance dropped.
- A node group shared by several objects (Shift+D) or two node groups sharing a holder are split.

### Coordinates

Blender is Z up with counter-clockwise faces. Houdini is Y up with clockwise faces. Geometry on the wire is always in Houdini space, and `mesh_io/` converts on the Blender side: a 90 degree rotation around X, and the corners of every face in reverse order.

## State and threads

Every state lives in one object, held by one module under a public name (`cook.cooks`, `link.server`), whose docstring names the thread allowed to write it. The other modules talk to that object: through its methods, or through its fields when a read or a single write is all they need (`cook.cooks.pending`, `link.server.starting`). No module function relays to it.

| State | Owner | Written by | Read by |
|---|---|---|---|
| `ServerLink`: connection, start in progress and its error, generation | `link.py` | the thread that starts hython (startup thread, or main thread for a blocking start), under its lock; the main thread when it stops | main thread, worker (the connection a request was posted for) |
| `ServerLink`: outbox | `link.py` | main thread (`post`) | worker |
| `ServerLink`: results | `link.py` | worker | main thread (`link.server.take_results`) |
| `ServerLink`: last activity, idle stop, session announced | `link.py` | main thread, and the thread that publishes a connection | main thread |
| `CookState`: inputs, parameters, frame, stack sent, cook counts, stamps and renumbered results, inputs marked, released modifiers | `cook/state.py` | main thread | main thread |
| `CookQueue`: pending, in flight, sent at, caches being made | `cook/queue.py` | main thread (`features/cache.py` writes `cook.cooks.caching`) | main thread |
| `OwnUpdates`: depsgraph updates the engine causes, stack being read | `cook/request.py` | main thread | main thread (depsgraph handler) |
| `Trust`: the open file waits for *Allow Execution* | `trust.py` | main thread (`trust.file_loaded`, `trust.allow`, `trust.forget`) | main thread |
| `Scheduler`: next upkeep, status drawn, rendering, commands and their callbacks | `cook/scheduler.py` | main thread (the render handlers set `scheduler.timer.rendering`) | main thread |

The objects are created at import and never replaced; `unregister()` empties them, so a new `register()` starts from a clean state.

Three threads, all daemons named `houdini-bridge-*`:

- **`houdini-bridge-startup`** starts hython in the background (`link.server.ensure_running`), one at a time. It publishes the connection under the lock only if the link's generation is still the one it started under; `link.server.stop()`, `link.server.shutdown()` and a blocking start move the generation on, so a start that ends after them stops the hython it started at once.
- **`houdini-bridge-cook`**, the worker, sends each posted request to the connection it was posted for and queues the answer, or an error when that connection is gone. It runs from the first start to `link.server.shutdown()`, which sets its stop event and joins it for at most 5 s; a request in flight then ends with a connection error, since hython is stopped first.
- **`houdini-bridge-output`** (`hython.Connection`) reads hython's output and ends when hython exits.

A new session is announced, not acted on, by the link: `link.server.take_new_session()` is true once per new connection, and `cook/scheduler.begin_session` then resets `CookState`, removes the live `.vdb` files an ended session left, and emits `events.server_started`, on the main thread. Requests are prepared only after `cook.ensure_server()` returned true, and it calls `begin_session` first, so no request built from what an older session knew reaches a new one.

Unregistering `cook` removes its timers and handlers, delivers the answers already arrived, gives every request still waiting the error `server stopped` and empties the queues and `CookState`; then it shuts the link down (`link.server.shutdown`: hython stopped, worker joined, queues emptied). The link has no `register()`: hython starts on first use.

Still blocking, on purpose: the socket has no timeout, and `link.server.request_sync` (definitions of an HDA file, `cook_now`, `catalog.refresh_now`, `cache.cache_now`, writing an applied volume) waits for a cook the worker has in flight, so the interface can freeze while a long cook ends.

## Signals

The core knows no feature. Where a feature must hear about something the core does, the core emits a signal of `core/events.py`, and the feature connects to it in its `register()` and disconnects in its `unregister()`.

| Signal | Emitted by | When | Listened to by |
|---|---|---|---|
| `server_started()` | `cook/scheduler.begin_session` | a new Houdini session answers, once what was sent is forgotten and before anything is prepared for it (in `tick`, before the waiting results are applied) | `shelf/catalog.py`: reads the catalog at the first start of a Blender session |
| `result_applied(target, extras)` | `cook/result._write_result` | the cooked mesh is written, the streams not yet | `groups.py`: the groups of the result (`sync_result_groups`); `viewport/template.py`: the batch of a templated result is made again (`_on_result`) |
| `modifier_released(settings)` | `cook/scheduler.release_modifier` | a modifier is applied or removed, before its holder is deleted | `cache.py`: drops the cache (`clear`) |
| `setting_changed(settings, name)` | `properties.py` | the `update` callback of `use_cache` | `cache.py`: shows the cache, or goes back to live cooking (`apply_use`) |
| `modifier_applied(obj, outputs, summary)` | `modifiers.apply_modifier` | the modifier is applied, before an emptied object is replaced | `groups.py`: the groups join the Groups panel (`adopt_groups`), counted in `summary["groups"]` |

The shelf has one signal of its own, `catalog.refreshed`, which `assets.py` listens to so that the asset library is written again after each refresh.

A signal is synchronous: `emit` calls the listeners at once, on the main thread, in the order they connected, and an exception of a listener reaches the emitter as it would from a direct call. `summary` only feeds the message shown to the user, never a decision. `LISTENERS`, in `tests/test_blender.py`, says who listens to each signal while the extension is enabled, and `test_reenable` checks that none is left after it is disabled.

## Adding something

Six recipes, each with the files to touch in order and a real example to copy. After each, run the pure tests (`python -m unittest discover -s tests/unit -t .`) and `uvx ruff check .`, then the suites the recipe names. When the change touches what the extension registers or stores, see the frozen names in [CONTRIBUTING.md](CONTRIBUTING.md#frozen-names).

### Adding a stream

The instances, for example, are written in these places, and a new stream (closed curves kept as curves, say) takes the same ones:

1. `shared/contract.py`: the constant (`INSTANCES = "instances"`), its place in `STREAMS`, and its key in `ANSWERS[COOK]`; the cook row of the table under [Protocol](docs/dev/protocol.md#protocol). `test_contract.Values.test_streams` freezes the names: add it there. The name is stored in `.blend` files, so it is never renamed.
2. `houdini/streams.py`: its test in `VEX_CLASSIFY`, at the place where it must win over the others (an open curve is tested before a volume), giving a new number, named next to it (`KIND_INSTANCES`); and a class (`InstanceStream`) with `name`, that `kind`, `__init__` building its branch from the instance, `export` returning (description, blocks), and the steps it needs among `has`, `absent`, `resend`, `configure` and `destroy`; then its place in `STREAMS`.
3. `blender/core/properties.py`: a `PointerProperty` holder in `HoudiniBridgeSettings` (`instances_holder`), and the other properties it owns (`prototypes`). These are stored in the file, so they are named once and for all, and the registration snapshot changes.
4. `blender/core/streams/<stream>.py` (`instances.py`): its functions and its `STREAM = common.Stream(...)`, then its place in `STREAMS` of `streams/__init__.py`, and one more to `STREAMS_TREE_VERSION` next to it: the *Houdini Streams* group gets a row for the new stream, and `_streams_node` rebuilds a group of an earlier layout, which a file saved before 1.0 may hold, when it meets one.
5. A step of `test_streams` (`tests/test_blender.py`): a function of its own called from it, as `streams_curves`, and a test HDA if it needs one (`tests/make_test_hdas.py`).

No function that walks a registry changes, and neither do the panel, the cook engine or the cache.

### Adding a parameter type

The `menu` kind, for example, is written in these places, and a new kind (a file path with a button to pick the file, say) takes the same ones:

1. `shared/contract.py`: the constant (`MENU = "menu"`), its place in `PARM_KINDS`, and the fields only it sends in `PARM_FIELDS` (`"menu": ((MENU,), ...)`), or its name added to the kinds of a field it shares; the paragraph on parameter descriptions under [Protocol](docs/dev/protocol.md#protocol) names them (checked by `test_contract`). The name is stored in `.blend` files, so it is never renamed.
2. `houdini/parms.py`: its test in `kind_of`, at the place where it must win over the others (a menu is tested before the template types); `_describe_menu`, which adds its fields, and `_write_menu`, which sets a value, when `set_components` does not do; its entry in `KINDS`. `TEMPLATE_KINDS`, the template types that `kind_of` and `written_as` share, gets a line only for a template type no kind writes yet: a new kind on String templates is written by the `string` handler, as a menu on an Int template is by the `int` one.
3. `blender/core/parameters/kinds.py`: its functions and its `Kind` (`MENU = Kind(contract.MENU, socket=..., use=..., read=..., convert=..., copy=..., signature=("menu",))`), then its place in `KINDS`.
   - A kind without an input gives no `socket`, `read` or `convert`: what it needs instead is written where it is used, as the ramp's node (`ramps.py`, which `sync_interface`, `read_value` and `write_value` call for `contract.RAMP`).
   - The core draws nothing: its row in the modifier panel is a `Row` of `ROWS` in `ui/parameter_rows.py`, at the same place as in `KINDS` (`draw(layout, tree, modifier, parm, text, houdini_interface)`: with `houdini_interface` false, Blender draws the inputs and the row draws only what an input cannot show, and `beside_inputs(parm)` says whether it then draws something); it names the operators it presses by their classes (a new operator goes to `ui/operators.py`), or calls the feature that owns one (`selection.draw_arrow`).
4. Tests: a test HDA with a parameter of that kind (`tests/make_test_hdas.py`) and a check in `test_blender.py`; `test_contract.Values`, which freezes the names, gains it.

For a kind held by an input, no function that walks or asks a registry changes: neither `_describe_template`, `set_parm`, `describe_parms` nor `set_parms` on the server, nor `read_value`, `write_value`, `store_range`, `limits`, `sync_interface`, the usage chain, `apply_description`, `_signature` or the panel in Blender. `HoudiniBridgeParm` changes only when the kind needs a stored field none has. Multiparms are no kind but a structure of the walk: they stay in the walk of the templates.

### Adding a protocol command

The `logo` command, for example (hython renders Houdini's logo into a PNG for the viewport badges):

1. `shared/contract.py`: the constant (`LOGO = "logo"`) and its place in `COMMANDS`; its fields in `REQUESTS` (`LOGO: {"path": (REQUIRED, "the PNG file of the Houdini logo to write")}`), each with its default or `REQUIRED` and one line of specification; its answer keys in `ANSWERS` (`LOGO: {"ok": "the PNG file exists"}`). `test_contract.Values.test_commands` freezes the names: add it there.
2. `houdini/server.py`: a method of `Server` that takes `(header, arrays)`, reads each field with `contract.field(header, name)` and returns `(answer, arrays)` (`Server.logo`); its entry in `HANDLERS` (`contract.LOGO: Server.logo`). An exception it raises becomes an `error` answer.
3. The call, its header built by `contract.request(contract.LOGO, path=path)`: through `cook.send_command(key, header, callback)` from a feature, which starts the server when needed and calls `callback(answer, error)` on the main thread at a later tick (`viewport/overlay.py`); or through `link.server.request_sync(header)`, which waits for the answer and raises `hython.ServerError` on an `error` answer, and on `server stopped` when no server runs: start it first with `link.server.ensure_running(blocking=True)` (`modifiers.supported_definitions`, `catalog.refresh_now`).
   - Both sides name the command by its constant, never by its string, and the server reads a field only through `contract.field`: `test_contract` fails on a command written as a string or a `header.get(` outside the contract. Nothing checks at run time that an answer holds the keys `ANSWERS` declares.
4. Its row in the table under [Protocol](docs/dev/protocol.md#protocol): `test_contract` fails until the table names exactly its fields and keys.
5. `PROTOCOL_VERSION`: a new command reaches a server of another build only when the extension was updated while Blender kept its old modules, and that server answers `unknown command`. Raise the version when the other side would misread something (see [CONTRIBUTING.md](CONTRIBUTING.md#protocol-version)); `test_contract.Values.test_shared_values` holds the number.
6. A check in `tests/test_server.py`, which talks to the server with `connection.request(contract.request(...))` (the `ping` check is the smallest).

### Adding a field to the cook request

The `sphere_frequency` field, for example (the *Sphere Detail* preference, the detail of the polygon sphere quadric spheres become):

1. `shared/contract.py`: its entry in `REQUESTS[COOK]`, with its default and one line (`"sphere_frequency": (SPHERE_FREQUENCY, "detail of the polygon sphere quadric spheres become")`). A field without a sensible default is `REQUIRED`, and every request then carries it. `test_contract.Declarations.test_the_cook_fields_cooks_send_today` lists the fields: add it there.
2. `blender/core/cook/request.py`: the value, passed to the `contract.request(contract.COOK, ...)` call of `_read`, with a comment. `contract.request` refuses a field it does not declare.
3. The server reads it with `contract.field(header, "sphere_frequency")`, which gives the default when the field is missing: in `Server.cook`, or in the step that needs it (`InstanceStream.configure`, called through `Instance.configure_streams`). A request with an undeclared field is refused with `error`.
4. Its name in the `cook` row of the table under [Protocol](docs/dev/protocol.md#protocol) (checked by `test_contract`), and a sentence in **Cook request** when it needs one.
5. `PROTOCOL_VERSION`, when an older side would misread the request (see [CONTRIBUTING.md](CONTRIBUTING.md#protocol-version)).
6. A check in `tests/test_server.py` (its helper `cook(base, **fields)` sends a cook with the fields given).

A field read for a cook is not compared by the engine: a change of its value recooks only if something calls `cook.cooks.request` (the preference's `update` callback, `_recook_instances`, for this one).

### Adding a feature with its panel

The cache, for example (`blender/features/cache.py`: its operator, its section of the modifier panel, its handlers); a feature of several files is a package, as `shelf/`:

1. `blender/features/<feature>.py`, or a package whose `__init__.py` registers its modules in order (`shelf/__init__.py`). It imports `shared/`, `blender/core/` and itself, never another feature or `ui/`. It owns its operators and panels (`bl_idname` under `houdini_bridge.`, classes named `HOUDINI_BRIDGE_*`), and `register()` and `unregister()` register its classes, handlers and timers, and leave nothing behind.
2. When it must hear about the core, a signal: it connects in `register()` and disconnects in `unregister()` (`events.modifier_released.connect(clear)` in `cache.py`). A new signal is a `Signal` in `blender/core/events.py`, emitted by the core where the event happens, and a row in the table under [Signals](#signals). The core calls no feature.
3. `addon/__init__.py`: the module in `MODULES`, at the place its registration needs (after the property groups it uses).
4. `tests/unit/test_architecture.py`: its line in `IMPORT_RULES` (`("blender.features.cache", ("shared", "blender.core", "blender.features.cache"))`). It needs no other feature; an exception, as `viewport/` reading `groups.py` and `cache.py`, is discussed first.
5. Where it shows: a tab of the Houdini sidebar (`TABS` of `ui/panel.py`), or a section of the modifier panel drawn by `ui/modifier_panel.py` (`cache.draw_cache`), since `ui/` may import every feature.
6. Tests: its key in `MODULES` of `tests/support.py` and an accessor (`support.cache()`), its listeners in `LISTENERS` of `tests/test_blender.py`, a test in `test_blender.py`; the registration snapshot changes with its classes.

### Adding a preference

*Hide Like Houdini* (`follow_hide_when`), for example:

1. `blender/core/preferences.py`: its default in `DEFAULTS`, in the group of its tab (`"follow_hide_when": True`). `preferences.get()` gives that default while the add-on is not registered. A collection, as the HDA folders, has no default there: `_UNREGISTERED`, next to it, gives it an empty one (`folders=()`).
2. `blender/ui/preferences.py`: the property in `HoudiniBridgePreferences`, its default taken from `DEFAULTS` (`follow_hide_when: BoolProperty(name="Hide Like Houdini", description=..., default=DEFAULTS["follow_hide_when"], update=_refresh_visibility)`); an `update` callback when a change must recook, redraw or rebuild something (`_refresh_visibility`, `_recook_instances`, `_rebuild_library`); the line that draws it in its tab's `_draw_<tab>` (`_draw_modifiers`, section *Parameters*). The name is stored in the user's preferences: it is never renamed.
3. Where it is read: `preferences.get().follow_hide_when`, anywhere in the extension (`parameters.is_hidden`).
4. `docs/GUIDE.md`: a row in the table of its tab, under *Preferences*.
5. Tests: the registration snapshot lists the preferences, so it changes (rewrite it as [CONTRIBUTING.md](CONTRIBUTING.md#frozen-names) says and read its diff); a check in `test_preferences` (`tests/test_blender.py`) when the preference changes a result. The pure tests do not see a preference: only `test_registration` checks its name, type and default, and nothing checks the row of the guide.
