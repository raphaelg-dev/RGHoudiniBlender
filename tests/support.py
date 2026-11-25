"""How the tests reach the extension.

This file knows where the modules of the extension live, and is the only one of
tests/ that reads or writes their private names: when a module moves, MODULES
changes; when an internal changes, the function below that reaches it changes.

The cook engine is reached through its public names (the facade cook(), and the
public functions of its files). The private names this file still reaches are fault
injection (a state set by hand, a function replaced) or measurement (what the
extension keeps and draws, read but never changed), each behind one function:
- hython._registered_folders, replaced (fake_installations); hython._version, the
  version a path names (hython_version);
- catalog._set_tools, state.favorites, _follow_favorites: tools and favorites set
  in memory, and favorites followed, without Houdini (replace_catalog,
  replace_favorites, favorites, follow_favorites); catalog._previews, the icons loaded
  (has_tool_icon);
- status._previews and modifier_panel._previews, the state icons loaded (status_icon_keys,
  state_icon_keys); assets._index, the asset names and their tools (asset_index);
  groups._mask_and_geometry, what the Groups panel
  draws (group_preview);
- cache._prepare, wrapped to time it (time_cache); ServerLink.post and request_sync, wrapped
  to record what is sent to Houdini (record_requests); template.build and templated, wrapped
  to count the batches the Template flag makes and its draws, and template._batches, the
  batches it keeps (template_stats);
- mesh_io/write.py's _written_topologies, groups._versions and groups._cache, the
  caches kept per mesh pointer (pointer_caches).

Three ways in:
- source(name): a module of the addon/ folder, loaded without Blender through
  synthetic packages (their __init__ is not run: the root's imports bpy, and
  cook/'s is the facade). Only for the modules that import neither bpy nor hou.
- installed(name) and the named accessors (cook(), modifiers()…): the extension
  as Blender installed it, which is what the Blender suites test.
- mount_like_hython() and hython_module(name): the addon/ folder mounted by
  hython_entry.py, as hython runs it.
"""

import importlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
import types

try:  # the pure tests and test_server.py run without Blender
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIR = os.path.join(ROOT, "addon")
SOURCE_PACKAGE = "addon"
INSTALLED_PACKAGE = "bl_ext.user_default.rg_houdini_bridge"
HDA = os.path.join(ROOT, "tests", "hda", "bridge_test.hda")

# Where each module lives inside the package. A module that moves is fixed here. The keys
# are the names the suites ask for: the module's own name, but "server_catalog" for
# houdini/catalog.py ("catalog" is the shelf's, blender/features/shelf/catalog.py),
# "server_streams" for houdini/streams.py ("streams" is blender/core/streams/, the package),
# "ui_preferences" for blender/ui/preferences.py ("preferences" is blender/core/preferences.py),
# "cook_<file>" for the files of blender/core/cook/ ("cook" is its facade, cook/__init__.py),
# "streams_<file>" for the files of blender/core/streams/, "parameters_<file>" for the
# files of blender/core/parameters/ ("parameters" is the package), and "mesh_io" for
# blender/core/mesh_io/convert.py, the part of that package that loads without bpy (its
# other files are "mesh_io_read" and "mesh_io_write"; the package itself is not needed).
MODULES = {
    "animation": "blender.core.animation",
    "api": "api",
    "assets": "blender.features.shelf.assets",
    "cache": "blender.features.cache",
    "code_editor": "blender.features.code_editor",
    "contract": "shared.contract",
    "display": "blender.features.display",
    "catalog": "blender.features.shelf.catalog",
    "cook": "blender.core.cook",
    "cook_handlers": "blender.core.cook.handlers",
    "cook_result": "blender.core.cook.result",
    "cook_scheduler": "blender.core.cook.scheduler",
    "cook_state": "blender.core.cook.state",
    "events": "blender.core.events",
    "geometry": "houdini.geometry",
    "group_string": "blender.features.selection.group_string",
    "groups": "blender.features.groups",
    "hython": "blender.core.hython",
    "hython_entry": "hython_entry",
    "icons": "blender.core.icons",
    "instance": "houdini.instance",
    "link": "blender.core.link",
    "mesh_io": "blender.core.mesh_io.convert",
    "mesh_io_read": "blender.core.mesh_io.read",
    "mesh_io_write": "blender.core.mesh_io.write",
    "modifier_panel": "blender.ui.modifier_panel",
    "modifiers": "blender.core.modifiers",
    "operators": "blender.ui.operators",
    "overlay": "blender.features.viewport.overlay",
    "panel": "blender.ui.panel",
    "parameters": "blender.core.parameters",
    "parameters_inputs": "blender.core.parameters.inputs",
    "parameters_interface": "blender.core.parameters.interface",
    "parameters_kinds": "blender.core.parameters.kinds",
    "parameters_layout": "blender.core.parameters.layout",
    "parameters_ramps": "blender.core.parameters.ramps",
    "parameters_values": "blender.core.parameters.values",
    "parameter_rows": "blender.ui.parameter_rows",
    "parms": "houdini.parms",
    "paths": "blender.core.paths",
    "preferences": "blender.core.preferences",
    "properties": "blender.core.properties",
    "protocol": "shared.protocol",
    "server": "houdini.server",
    "server_catalog": "houdini.catalog",
    "script_action": "houdini.script_action",
    "selection": "blender.features.selection.session",
    "selection_mode": "blender.features.selection.mode",
    "server_streams": "houdini.streams",
    "status": "blender.features.viewport.status",
    "streams": "blender.core.streams",
    "streams_common": "blender.core.streams.common",
    "streams_curves": "blender.core.streams.curves",
    "streams_instances": "blender.core.streams.instances",
    "streams_points": "blender.core.streams.points",
    "streams_volumes": "blender.core.streams.volumes",
    "stack_cuts": "blender.core.stack_cuts",
    "targets": "blender.core.targets",
    "template": "blender.features.viewport.template",
    "trust": "blender.core.trust",
    "ui_preferences": "blender.ui.preferences",
    "visualize": "blender.features.viewport.visualize",
}


# --- modules --------------------------------------------------------------------------


def source(name):
    """A module of addon/ without bpy (protocol, contract, hython, mesh_io, events, animation, the
    cook state, the icons, the cuts of a stack), from the repository. Its packages are synthetic: their
    __init__ is not run."""
    parts = [SOURCE_PACKAGE] + MODULES[name].split(".")
    for depth in range(1, len(parts)):
        package_name = ".".join(parts[:depth])
        if package_name not in sys.modules:
            package = types.ModuleType(package_name)
            package.__path__ = [os.path.join(SOURCE_DIR, *parts[1:depth])]
            sys.modules[package_name] = package
    return importlib.import_module(".".join(parts))


def installed(name):
    """A module of the installed extension (looked up at every call: the extension may be reloaded)."""
    return importlib.import_module(INSTALLED_PACKAGE + "." + MODULES[name])


_hython_package = {"name": None}


def mount_like_hython():
    """Do in this interpreter what hython_entry.py does before it starts the server, on the
    addon/ folder of the repository: hython has put the script's folder first on sys.path,
    mount() takes it off and declares the folder as a package without running its
    __init__. Returns the package's name. Changes sys.path and sys.modules: only for a
    process of its own (the hython probe) or a test that puts them back."""
    path = os.path.join(SOURCE_DIR, *MODULES["hython_entry"].split(".")) + ".py"
    spec = importlib.util.spec_from_file_location("bridge_hython_entry", path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    sys.path.insert(0, os.path.dirname(path))  # what hython does for the script it runs
    entry.mount()
    _hython_package["name"] = entry.PACKAGE
    return entry.PACKAGE


def hython_module(name):
    """A module of the package mount_like_hython() declared, imported by its full name."""
    return importlib.import_module(_hython_package["name"] + "." + MODULES[name])


def api():
    """The scripting interface (api.py), as a script imports it."""
    return installed("api")


def assets():
    return installed("assets")


def cache():
    return installed("cache")


def code_editor():
    """The code editor: open_editor(), follow(), mirrors(), close_all() (features/code_editor.py)."""
    return installed("code_editor")


def catalog():
    """The shelf's catalog (blender/features/shelf/catalog.py)."""
    return installed("catalog")


def animation():
    return installed("animation")


def cook():
    """The cook engine's facade (blender/core/cook/__init__.py)."""
    return installed("cook")


def events():
    return installed("events")


def display():
    """The Display flag: set_flag(), settle(), is_flagged(), is_cut(), cut_by() (features/display.py)."""
    return installed("display")


def stack_cuts():
    """The cuts of a modifier stack shared by the group selection and the Display flag (core/stack_cuts.py)."""
    return installed("stack_cuts")


def template():
    """The Template flag's drawing: templated() (features/viewport/template.py)."""
    return installed("template")


_template_counts = {}  # id of a module of the Template flag -> what it did since it is counted


def template_stats(template):
    """{"builds": batches made, "draws": viewport draws, "batches": batches kept} of a module of
    the Template flag. The first call on a module starts counting: it wraps its build() and
    templated(), which each draw calls once with the overlays shown."""
    counts = _template_counts.get(id(template))
    if counts is None:
        counts = _template_counts[id(template)] = {"builds": 0, "draws": 0}
        build, templated = template.build, template.templated

        def counted_build(mesh):
            counts["builds"] += 1
            return build(mesh)

        def counted_templated():
            counts["draws"] += 1
            return templated()

        template.build, template.templated = counted_build, counted_templated
    return dict(counts, batches=len(template._batches))


def groups():
    return installed("groups")


def selection():
    """The viewport group selection: begin(), finish(), active() (features/selection/session.py)."""
    return installed("selection")


def hython():
    return installed("hython")


def link():
    return installed("link")


def modifiers():
    return installed("modifiers")


def parameters():
    return installed("parameters")


def properties():
    return installed("properties")


def status():
    return installed("status")


def streams():
    return installed("streams")


def targets():
    return installed("targets")


def target(tree):
    """The target (cook engine's view) of a Houdini modifier's node group."""
    return targets().Target(tree)


def visualize():
    return installed("visualize")


def hython_version(client_module, path):
    """The (major, minor, build) a hython path names, as the client reads it."""
    return client_module._version(path)


def fake_installations(client_module, found, registered=()):
    """A context in which the client finds the hython paths of `found` (as if globbed)
    and the install folders of `registered` (as if read from the Windows registry,
    each one holding bin/hython.exe), and nothing else on disk."""
    import contextlib
    from unittest import mock

    registered_hythons = {os.path.normpath(os.path.join(folder, "bin", "hython.exe")) for folder in registered}
    first_pattern = client_module.PATTERNS[0]
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(client_module.glob, "glob",
                                          lambda pattern: list(found) if pattern == first_pattern else []))
    stack.enter_context(mock.patch.object(client_module, "_registered_folders", lambda: list(registered)))
    stack.enter_context(mock.patch.object(client_module.os.path, "exists",
                                          lambda path: os.path.normpath(path) in registered_hythons))
    return stack


# --- the extension --------------------------------------------------------------------


def enable(auto_run=True):
    """Enable the installed extension. A suite (always --factory-startup, whose preferences
    are never saved) also turns Auto Run Python Scripts on: the files it opens are its own,
    and test_auto_run turns it off for a while (auto_run=False leaves it as it is)."""
    import addon_utils

    addon_utils.enable(INSTALLED_PACKAGE, default_set=True)
    if bpy.app.factory_startup and auto_run:
        bpy.context.preferences.filepaths.use_scripts_auto_execute = True


def disable():
    import addon_utils

    addon_utils.disable(INSTALLED_PACKAGE, default_set=True)


def reload(auto_run=True):
    """Disable the extension, forget its modules and enable it again, as Blender does when it
    installs or updates an extension (bl_pkg's _preferences_ensure_disabled): every module is
    imported anew."""
    disable()
    for name in list(sys.modules):
        if name == INSTALLED_PACKAGE or name.startswith(INSTALLED_PACKAGE + "."):
            sys.modules.pop(name, None)
    enable(auto_run)


def preferences():
    return bpy.context.preferences.addons[INSTALLED_PACKAGE].preferences


def package_name():
    """paths.PACKAGE of the installed extension: the name it computes for its root package."""
    return installed("paths").PACKAGE


def preference_defaults():
    """{preference name: default value} of the installed extension."""
    return dict(installed("preferences").DEFAULTS)


_user_dir = {"path": None}


def redirect_user_dir():
    """Point the extension's user folder (catalog, favorites, library, applied volumes)
    at a temporary folder, so a test never touches the user's own. The same folder
    is kept for the whole run; call again after the extension is reloaded."""
    if _user_dir["path"] is None:
        _user_dir["path"] = tempfile.mkdtemp(prefix="bridge_test_")
    folder = _user_dir["path"]
    installed("paths").user_dir = lambda: folder
    catalog().load()  # forget what was read from the real user folder at registration
    return folder


def library_dir():
    """The folder of the asset library written from the catalog."""
    return installed("paths").library_dir()


# --- checks and common helpers --------------------------------------------------------

failures = []


def check(condition, message):
    print(("  ok    " if condition else "  FAIL  ") + message, flush=True)
    if not condition:
        failures.append(message)
    return bool(condition)


def skip(message):
    print("  skipped  " + message, flush=True)


def note(message):
    """A known defect, reported but not counted as a failure."""
    print("  known  " + message, flush=True)


def face_normals(mesh):
    import numpy as np

    normals = np.empty(len(mesh.polygons) * 3, np.float32)
    mesh.polygons.foreach_get("normal", normals)
    return normals.reshape(-1, 3)


def positions(mesh):
    import numpy as np

    values = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", values)
    return values.reshape(-1, 3)


def hda(obj):
    """(modifier, node group) of the first Houdini modifier of an object."""
    modifier = next(m for m in obj.modifiers
                    if m.type == "NODES" and m.node_group and m.node_group.houdini_bridge.enabled)
    return modifier, modifier.node_group


def evaluated(obj):
    """(face count, world positions) of an object's evaluated mesh."""
    import numpy as np

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_object = obj.evaluated_get(depsgraph)
    mesh = evaluated_object.to_mesh()
    try:
        local = positions(mesh)
        matrix = np.array(obj.matrix_world, dtype=np.float32)
        return len(mesh.polygons), local @ matrix[:3, :3].T + matrix[:3, 3]
    finally:
        evaluated_object.to_mesh_clear()


def set_parm(modifier, settings, name, value):
    parameters().write_value(modifier, settings.parms[name], value)


def get_parm(modifier, parm):
    return parameters().read_value(modifier, parm)


def modifier_input(modifier, identifier):
    """The item of a modifier input (value, keyframes) in Blender 5.2."""
    return parameters().modifier_input(modifier, identifier)


# --- the server -----------------------------------------------------------------------


def server_request(header, arrays=None):
    """A synchronous request to the running server: (answer, output)."""
    return link().server.request_sync(header, arrays)


def definitions(path=HDA):
    """{type: definition} of the HDAs of a file, read by the server."""
    contract = installed("contract")
    answer, _ = server_request(contract.request(contract.DEFINITIONS, path=path))
    return {d["type"]: d for d in answer["definitions"]}


def kill_server():
    """Kill hython whatever the main thread is doing (fault injection, and last resort of a watchdog)."""
    connection = link().server.connection
    process = connection.process if connection is not None else None
    if process is not None:
        process.kill()


def simulate_idle(minutes):
    """As if nothing had been asked of Houdini for that long (fault injection), then run
    the idle check of the timer."""
    link().server.last_activity -= minutes * 60
    installed("cook_scheduler").stop_when_idle(False)


# --- the cook queue -------------------------------------------------------------------


def is_requested(tree):
    """A cook of this Houdini modifier waits in the queue."""
    return cook().cooks.is_requested(tree)


def cancel_request(tree):
    cook().cooks.cancel(tree)


def clear_requests():
    cook().cooks.pending.clear()


def is_busy():
    """Cooks wait in the queue or are on their way to Houdini."""
    return cook().cooks.is_busy()


def is_in_flight(tree):
    """The cook of this Houdini modifier is on its way to Houdini (sent, not answered)."""
    return tree.session_uid in cook().cooks.in_flight


def settle(rounds=3000):
    """Run the engine timer by hand until every cook is done and applied (background mode)."""
    tick = installed("cook_scheduler").tick

    def idle():
        return not (cook().cooks.is_busy() or link().server.results)

    for _ in range(rounds):
        tick()
        if idle():
            time.sleep(0.05)
            tick()
            if idle():
                return True
        time.sleep(0.01)
    return False


def maintain():
    """What the timer does once a second: release removed modifiers, split shared ones."""
    installed("cook_scheduler").maintain()


def on_undo():
    """What Blender calls after an undo or a redo."""
    installed("cook_handlers").on_undo()


def chained_above(tree):
    """The Houdini modifier whose result feeds input 1 of this one on the server, or None."""
    return cook().chained_above(target(tree))


def changed_inputs(tree):
    """The input indices marked as changed since the last cook."""
    return cook().sent.dirty(tree.session_uid)


def mark_input_changed(tree, index):
    cook().sent.mark_dirty(tree.session_uid, index)


def poll_inputs():
    """What the 10 Hz timer does: compare the modifier inputs with the values last sent."""
    installed("cook_scheduler").poll_modifier_inputs()


def forget_sent(tree):
    """Forget what was sent for this modifier: the next cook sends everything."""
    cook().sent.forget(tree.session_uid)


def apply_error(tree, error):
    """Apply an answer that is an error, as the timer does, and return the errors kept."""
    installed("cook_result").apply_result(target(tree), {}, {}, error, 0.0)
    return tree.houdini_bridge.errors


def mark_released(tree):
    """As if the server instance of this modifier had been deleted (applied, removed)."""
    cook().sent.release(tree.session_uid)
    cook().cooks.cancel(tree)


def is_rendering():
    return installed("cook_scheduler").timer.rendering


def peek_request(tree):
    """What a cook would send now, without recording it as sent."""
    return cook().peek_request(target(tree))


def record_applied_results(frames):
    """Append the current frame to `frames` each time a result is written (events.result_applied).
    Returns the undo."""
    signal = events().result_applied

    def counting(target, extras):
        frames.append(bpy.context.scene.frame_current)

    signal.connect(counting)
    return lambda: signal.disconnect(counting)


def time_cache(timings):
    """Add up, in `timings`, the time a cache spends preparing frames ("prepare") and
    waiting for them ("round_trip"). Returns the undo."""
    cache_module, cook_module = cache(), cook()
    prepare, post = cache_module._prepare, cook_module.post_cook

    def timed_prepare(*arguments):
        start = time.perf_counter()
        try:
            return prepare(*arguments)
        finally:
            timings["prepare"] += time.perf_counter() - start

    def timed_post(key, header, arrays, callback):
        timings["sent"] = time.perf_counter()

        def received(*results):
            timings["round_trip"] += time.perf_counter() - timings["sent"]
            return callback(*results)

        return post(key, header, arrays, received)

    cache_module._prepare, cook_module.post_cook = timed_prepare, timed_post

    def restore():
        cache_module._prepare, cook_module.post_cook = prepare, post

    return restore


# --- shelf, assets, icons -------------------------------------------------------------


def catalog_state():
    """{"refreshing": bool, "error": text} of the catalog."""
    module = catalog()
    return {"refreshing": module.state.refreshing, "error": module.state.error}


def replace_catalog(tools, date=None):
    """Put these tools in the catalog (not written to disk). Returns (tools, date) before."""
    module = catalog()
    previous = (module.state.tools, module.state.date)
    module._set_tools(tools, time.time() if date is None else date)
    return previous


def favorites():
    return set(catalog().state.favorites)


def replace_favorites(keys):
    """Set the favorites in memory only (favorites.json is left as it is)."""
    catalog().state.favorites = set(keys)


def follow_favorites():
    """Move favorites on an old version of a tool to the version the catalog keeps."""
    catalog()._follow_favorites()


def has_tool_icon(icon):
    return icon in catalog()._previews


def status_icon_keys():
    return set(status()._previews.keys())


def asset_index():
    """{asset name: tool key} of the asset library."""
    return dict(assets()._index())


def group_preview(obj, group, mesh):
    """(points, triangles) the Groups panel draws for a group on a mesh."""
    return groups()._mask_and_geometry(obj, group, mesh)


def pointer_caches():
    """{name: entries} of the caches the extension keys by mesh pointer (measurement)."""
    groups_module = groups()
    return {"written topologies": len(installed("mesh_io_write")._written_topologies),
            "group versions": len(groups_module._versions), "group drawings": len(groups_module._cache)}


def run_undo_handlers():
    """What Blender runs after an undo: every function of the extension in undo_post."""
    for function in list(bpy.app.handlers.undo_post):
        if _from_extension(function):
            function(bpy.context.scene)


# --- signals --------------------------------------------------------------------------

SIGNALS = ("server_started", "result_applied", "modifier_released", "setting_changed", "modifier_applied")


def signals():
    """{name: signal} of the five signals of events.py and of the shelf's own "shelf.refreshed"."""
    module = events()
    found = {name: getattr(module, name) for name in SIGNALS}
    found["shelf.refreshed"] = catalog().refreshed
    return found


def listeners(found):
    """{name: ["module.function", ...]} of the signals of signals(), in the order they call them."""
    return {name: [function.__module__.rsplit(".", 1)[-1] + "." + function.__qualname__
                   for function in signal.listeners]
            for name, signal in found.items()}


# --- registration ---------------------------------------------------------------------


def _from_extension(cls):
    return getattr(cls, "__module__", "").startswith(INSTALLED_PACKAGE)


def _subclasses(base):
    for cls in base.__subclasses__():
        yield cls
        yield from _subclasses(cls)


def _jsonable(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, (list, tuple)) or hasattr(value, "__len__") and not isinstance(value, str):
        try:
            return [_jsonable(item) for item in value]
        except TypeError:
            pass
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    return str(value)


def _describe_property(prop):
    entry = {"identifier": prop.identifier, "type": prop.type, "subtype": prop.subtype}
    if prop.type in ("POINTER", "COLLECTION"):
        entry["fixed_type"] = prop.fixed_type.identifier if prop.fixed_type else None
    elif prop.type == "ENUM":
        entry["flag"] = bool(prop.is_enum_flag)
        entry["default"] = _jsonable(prop.default_flag if prop.is_enum_flag else prop.default)
        items = [item.identifier for item in prop.enum_items]
        if items:  # a dynamic enum lists nothing here
            entry["items"] = items
    elif getattr(prop, "is_array", False):
        entry["size"] = prop.array_length
        entry["default"] = _jsonable(tuple(prop.default_array))
    else:
        entry["default"] = _jsonable(getattr(prop, "default", None))
    return entry


def _rna_properties(rna):
    return [_describe_property(prop) for prop in rna.properties if prop.identifier != "rna_type"]


def _operator_properties(idname):
    category, name = idname.split(".")
    operator = getattr(getattr(bpy.ops, category), name)
    return [{"identifier": prop.identifier, "type": prop.type}
            for prop in operator.get_rna_type().properties if prop.identifier != "rna_type"]


def describe_registration():
    """Everything the extension registers in Blender, sorted, without module paths:
    the names stored in .blend files, keymaps and scripts."""
    types_ = bpy.types
    description = {"operators": [], "interface": [], "property_groups": [], "preferences": [],
                   "attached_properties": [], "hotkeys": []}
    for cls in _subclasses(types_.Operator):
        if _from_extension(cls) and cls.is_registered:
            description["operators"].append({
                "bl_idname": cls.bl_idname, "bl_label": cls.bl_label,
                "properties": _operator_properties(cls.bl_idname)})
    for kind, base in (("panel", types_.Panel), ("menu", types_.Menu), ("asset_shelf", types_.AssetShelf),
                       ("list", types_.UIList)):
        for cls in _subclasses(base):
            if not (_from_extension(cls) and cls.is_registered):
                continue
            entry = {"kind": kind, "class": cls.__name__}
            for key in ("bl_idname", "bl_label", "bl_space_type", "bl_region_type", "bl_category"):
                # Only what the extension's classes (and their mixins) set, not Blender's defaults.
                value = next((vars(owner)[key] for owner in cls.__mro__ if _from_extension(owner)
                              and key in vars(owner)),
                             None)
                if value is not None:
                    entry[key] = value
            description["interface"].append(entry)
    for cls in _subclasses(types_.PropertyGroup):
        if _from_extension(cls) and cls.is_registered:
            description["property_groups"].append({"class": cls.__name__, "properties": _rna_properties(cls.bl_rna)})
    for cls in _subclasses(types_.AddonPreferences):
        if _from_extension(cls) and cls.is_registered:
            description["preferences"].append({
                "class": cls.__name__, "bl_idname_is_package": cls.bl_idname == INSTALLED_PACKAGE,
                "properties": _rna_properties(cls.bl_rna)})
    # Properties the extension adds to Blender's own types (all named houdini_bridge…).
    for name in dir(types_):
        cls = getattr(types_, name)
        rna = getattr(cls, "bl_rna", None)
        if rna is None or not isinstance(cls, type):
            continue
        try:
            props = list(rna.properties)
        except (AttributeError, TypeError):
            continue
        base = rna.base
        for prop in props:
            if (prop.is_runtime and prop.identifier.startswith("houdini_bridge")
                    and (base is None or prop.identifier not in base.properties)):
                entry = _describe_property(prop)
                entry["owner"] = name
                description["attached_properties"].append(entry)
    key_config = bpy.context.window_manager.keyconfigs.addon
    for keymap in key_config.keymaps if key_config is not None else ():
        for item in keymap.keymap_items:
            names = list(item.properties.keys()) if item.properties is not None else []
            values = {key: _jsonable(getattr(item.properties, key)) for key in names}
            ours = item.idname.startswith("houdini_bridge.") or any(
                isinstance(value, str) and value.startswith("HOUDINI_BRIDGE") for value in values.values())
            if ours:
                description["hotkeys"].append({
                    "keymap": keymap.name, "space_type": keymap.space_type, "region_type": keymap.region_type,
                    "operator": item.idname, "key": item.type, "value": item.value,
                    "ctrl": bool(item.ctrl), "shift": bool(item.shift), "alt": bool(item.alt),
                    "oskey": bool(item.oskey), "properties": values})
    for key in ("operators",):
        description[key].sort(key=lambda entry: entry["bl_idname"])
    description["interface"].sort(key=lambda entry: (entry["kind"], entry["class"]))
    description["property_groups"].sort(key=lambda entry: entry["class"])
    description["preferences"].sort(key=lambda entry: entry["class"])
    description["attached_properties"].sort(key=lambda entry: (entry["owner"], entry["identifier"]))
    description["hotkeys"].sort(key=lambda entry: (entry["keymap"], entry["operator"], entry["key"],
                                                   sorted(entry["properties"].items())))
    return description


# --- what a .blend file holds (compatibility fixture) ---------------------------------

COMPAT_SETTINGS = ("live", "output_mode", "instance_display", "visualize", "use_cache", "cache_scene_range",
                   "cache_start", "cache_end", "cache_first", "time_dependent")


def _holds_value(parm):
    """The kind of the parameter has a value (an input or a ramp's node holds it; a button has none)."""
    return parameters().kind_of(parm.kind).read is not None or parm.kind == installed("contract").RAMP


def describe_modifier(modifier):
    """What a Houdini modifier keeps in the file, as JSON values."""
    settings = modifier.node_group.houdini_bridge
    holder = settings.holder
    return _jsonable({
        "hda": {"name": modifier.name, "node_type": settings.node_type, "label": settings.label,
                "path": settings.path},
        "settings": {key: getattr(settings, key) for key in COMPAT_SETTINGS},
        "inputs": [{"label": item.label, "is_self": item.is_self, "object": item.object.name if item.object else None}
                   for item in settings.inputs],
        "parms": [[parm.name, parm.kind] for parm in settings.parms],
        "values": {parm.name: get_parm(modifier, parm) for parm in settings.parms if _holds_value(parm)},
        "streams": streams().summary(settings),
        "result": {"points": len(holder.data.vertices), "faces": len(holder.data.polygons)} if holder else None,
        "cache": {"frames": cache().frame_count(settings),
                  "first": settings.cache_first} if settings.use_cache else None,
    })


def describe_houdini_scene():
    """{object name: {"type", "groups", "modifiers"}} for every object carrying Houdini
    modifiers or groups of the Groups panel."""
    scene = {}
    for obj in sorted(bpy.data.objects, key=lambda item: item.name):
        if obj.houdini_bridge_result:
            continue
        modifiers = [modifier for modifier in obj.modifiers if modifier.type == "NODES" and modifier.node_group
                     and modifier.node_group.houdini_bridge.enabled]
        groups_ = list(obj.data.houdini_bridge_groups) if obj.type == "MESH" else []
        if not modifiers and not groups_:
            continue
        scene[obj.name] = {
            "type": obj.type,
            "groups": _jsonable([{"name": group.name, "attribute": group.attribute, "domain": group.domain,
                                  "color": tuple(group.color), "show": group.show} for group in groups_]),
            "modifiers": [describe_modifier(modifier) for modifier in modifiers],
        }
    return scene


def differences(expected, found, path="", tolerance=1e-4):
    """Where two JSON values differ (floats within `tolerance`): a list of short texts."""
    if isinstance(expected, dict) and isinstance(found, dict):
        found_keys = set(found)
        result = ["%s.%s missing" % (path, key) for key in sorted(set(expected) - found_keys)]
        result += ["%s.%s unexpected" % (path, key) for key in sorted(found_keys - set(expected))]
        for key in sorted(set(expected) & found_keys):
            result += differences(expected[key], found[key], "%s.%s" % (path, key), tolerance)
        return result
    if isinstance(expected, list) and isinstance(found, list):
        if len(expected) != len(found):
            return ["%s: %d items, not %d" % (path, len(found), len(expected))]
        result = []
        for index, (left, right) in enumerate(zip(expected, found, strict=True)):
            result += differences(left, right, "%s[%d]" % (path, index), tolerance)
        return result
    numbers = (int, float)
    if (isinstance(expected, numbers) and isinstance(found, numbers)
            and not isinstance(expected, bool) and not isinstance(found, bool)):
        return [] if abs(expected - found) <= tolerance * max(1.0, abs(expected)) else [
            "%s: %r, not %r" % (path, found, expected)]
    return [] if expected == found else ["%s: %r, not %r" % (path, found, expected)]


def installed_version():
    """The version of the installed extension, from its manifest."""
    folder = os.path.dirname(importlib.import_module(INSTALLED_PACKAGE).__file__)
    with open(os.path.join(folder, "blender_manifest.toml"), encoding="utf-8") as file:
        for line in file:
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def extension_handlers():
    """[(handler list, function name)] of the extension's functions in bpy.app.handlers."""
    found = []
    for name in dir(bpy.app.handlers):
        handlers = getattr(bpy.app.handlers, name)
        if isinstance(handlers, list):
            found.extend((name, function.__name__) for function in handlers if _from_extension(function))
    return sorted(found)


def extension_functions():
    """Every function defined at the top of a module of the extension (to look for timers)."""
    functions = []
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith(INSTALLED_PACKAGE):
            continue
        for value in vars(module).values():
            if isinstance(value, types.FunctionType) and value.__module__ == name:
                functions.append(value)
    return functions


def registered_timers(functions):
    """The functions of `functions` that are registered bpy timers."""
    return sorted(function.__module__.rsplit(".", 1)[-1] + "." + function.__name__
                  for function in functions if bpy.app.timers.is_registered(function))


def server_running():
    return link().server.running_hython() is not None


def server_starting():
    """hython is being started in the background."""
    return link().server.starting


# --- threads and processes ------------------------------------------------------------

THREAD_PREFIX = "houdini-bridge"


def bridge_threads():
    """Names of the extension's threads still alive (houdini-bridge-*), sorted."""
    return sorted(thread.name for thread in threading.enumerate() if thread.name.startswith(THREAD_PREFIX))


def hython_processes():
    """Ids of the hython processes this Blender started and that still run, as the
    operating system lists them (children of this process named hython*)."""
    if sys.platform == "win32":
        command = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-CimInstance Win32_Process -Filter 'ParentProcessId=%d' | "
                   "Where-Object { $_.Name -like 'hython*' } | ForEach-Object { $_.ProcessId }" % os.getpid()]
    else:
        command = ["pgrep", "-P", str(os.getpid()), "hython"]
    found = subprocess.run(command, capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return sorted(int(word) for word in found.split() if word.isdigit())


def kill_processes(ids):
    """Kill these processes (a hython a known defect left behind)."""
    for pid in ids:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, 9)


def record_applied_targets(keys):
    """Append the target's uid each time a result is written (events.result_applied). Returns the undo."""
    signal = events().result_applied

    def counting(target, extras):
        keys.append(target.key)

    signal.connect(counting)
    return lambda: signal.disconnect(counting)


def record_requests(found):
    """Append (command, instance) of every request sent to Houdini from now on, posted to the
    worker or sent synchronously (ServerLink.post, ServerLink.request_sync), to `found`; the
    instance is None for a command without one. Returns the undo, which puts both methods back."""
    server = link().server
    post, request_sync = server.post, server.request_sync

    def noted(header):
        found.append((header.get(installed("contract").COMMAND), header.get("instance")))

    def recording_post(key, header, arrays=None):
        noted(header)
        return post(key, header, arrays)

    def recording_request_sync(header, arrays=None):
        noted(header)
        return request_sync(header, arrays)

    server.post, server.request_sync = recording_post, recording_request_sync

    def undo():
        del server.post, server.request_sync  # the methods of the class again

    return undo


def fcurves(obj):
    """The F-curves of an object's action (its first layer and strip), [] without one."""
    animation = obj.animation_data
    if animation is None or animation.action is None:
        return []
    found = []
    for layer in animation.action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                found.extend(bag.fcurves)
    return found


def count_depsgraph_updates(datablocks):
    """Count the depsgraph updates of these datablocks from now on: returns a function that gives
    the count and stops counting."""
    count = [0]
    watched = {datablock.session_uid for datablock in datablocks}

    def counted(scene, depsgraph):
        count[0] += sum(1 for update in depsgraph.updates if update.id.original.session_uid in watched)

    bpy.app.handlers.depsgraph_update_post.append(counted)

    def stop():
        if counted in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(counted)
        return count[0]

    return stop


def watch_panel_draws():
    """Count the draws of the modifier panel (header and body) and catch their exceptions
    (Blender prints an exception of a draw and goes on). Returns a function that stops watching
    and gives (draws, [tracebacks])."""
    import traceback

    module = panel()
    found = {"calls": 0, "errors": []}
    originals = {name: getattr(module, name) for name in ("draw", "draw_header")}

    def watching(function):
        def watched(panel_, context):
            found["calls"] += 1
            try:
                function(panel_, context)
            except Exception:
                found["errors"].append(traceback.format_exc()[-600:])
        return watched

    for name, function in originals.items():
        setattr(module, name, watching(function))

    def stop():
        for name, function in originals.items():
            setattr(module, name, function)
        return found["calls"], found["errors"]

    return stop


def show_panel_bodies():
    """Draw the body of the modifier panel of each Houdini modifier of the active object in a
    panel of the 3D view's sidebar too: Blender folds the modifier panel at first (its open
    state cannot be set from Python), and a test must see its body drawn. The modifier panel's
    draw only reads `layout` and `custom_data`. Returns a function that removes the panel."""

    class Shim:
        def __init__(self, layout, modifier):
            self.layout, self.custom_data = layout, modifier

    class TEST_PT_houdini_bodies(bpy.types.Panel):
        bl_label = "Houdini Modifier Bodies (test)"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "Houdini"

        def draw(self, context):
            obj = context.active_object
            for modifier in obj.modifiers if obj is not None else ():
                if modifier.type == "NODES":
                    panel().draw(Shim(self.layout.column(), modifier), context)

    bpy.utils.register_class(TEST_PT_houdini_bodies)
    return lambda: bpy.utils.unregister_class(TEST_PT_houdini_bodies)


def peek_parms(tree):
    """The parameter values the last cook sent for a modifier."""
    return cook().sent.parms(tree.session_uid)


def menu_draws(menu, function_name):
    """Whether a function of that name was appended to a Blender menu."""
    draw = getattr(bpy.types, menu).draw
    return function_name in [function.__name__ for function in getattr(draw, "_draw_funcs", [])]


def panel():
    """The modifier panel (blender/ui/modifier_panel.py): the state and the icon it draws for a
    modifier, which the overview of the sidebar shares."""
    return installed("modifier_panel")


def state_icon_keys():
    """The state icons the modifier panel loaded."""
    return set(panel()._previews.keys())
