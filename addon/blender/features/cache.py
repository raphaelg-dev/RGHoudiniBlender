"""Houdini-style cache: a frame range cooked once, then played back by Blender itself.

Blender drives the frames one by one (an animated input can only be read by
Blender), Houdini cooks each of them, and each result is stored as a mesh, with
all its attributes, groups and materials, on an object of a hidden collection.
A Geometry Nodes modifier on the holder ("Houdini Cache") picks the object of
the current frame: playback and rendering are plain Blender evaluation, frame
accurate, with no handler touching the scene while it renders.

The frames are saved in the .blend file.
"""

import json
import time
from dataclasses import dataclass

import bpy
from bpy.props import EnumProperty

from ...shared import contract
from ..core import cook, events, feedback, hython, link, mesh_io, modifiers, streams, targets

PLAYER_MODIFIER = "Houdini Cache"
FIRST_STEP = 0.05  # seconds before the caching timer's first step
STEP = 0.01  # seconds between two steps of the caching timer


@dataclass
class Job:
    """A cache being made, one frame after the other. It holds names, not Blender data: an
    undo or a purge may free the data between two frames."""

    tree: str  # the node group's name
    uid: int  # its session_uid
    frames: list
    name: str  # the holder's name, which names the object of each frame
    collection: str  # the name of the collection the frames go to
    return_frame: int  # the user's frame, set again at the end
    started: float  # time.perf_counter()
    index: int = 0  # how many frames are stored
    waiting: bool = False  # the cook of a frame is in flight
    cancel: bool = False


_jobs = {}  # target uid -> Job


class FrameError(Exception):
    """A frame Houdini did not cook: the cache stops, and its status says why."""


# --- player ---------------------------------------------------------------------------


def _player_tree(settings):
    """Geometry Nodes group showing, from the cache collection, the object of the current frame."""
    name = "Houdini Cache · %s" % settings.holder.name
    tree = bpy.data.node_groups.get(name)
    if tree is None or tree.bl_idname != "GeometryNodeTree":
        tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
        tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        nodes, links = tree.nodes, tree.links
        frames = nodes.new("GeometryNodeCollectionInfo")
        frames.name = "Frames"
        frames.transform_space = "ORIGINAL"
        frames.inputs["Separate Children"].default_value = True
        frames.inputs["Reset Children"].default_value = True
        scene_time = nodes.new("GeometryNodeInputSceneTime")
        first = nodes.new("ShaderNodeValue")
        first.name = "First Frame"
        offset = nodes.new("ShaderNodeMath")
        offset.operation = "SUBTRACT"
        links.new(scene_time.outputs["Frame"], offset.inputs[0])
        links.new(first.outputs[0], offset.inputs[1])
        rounded = nodes.new("ShaderNodeMath")
        rounded.operation = "ROUND"
        links.new(offset.outputs[0], rounded.inputs[0])
        # Before the range: the first frame; after it: the last one.
        count = nodes.new("GeometryNodeAttributeDomainSize")
        count.component = "INSTANCES"
        links.new(frames.outputs["Instances"], count.inputs["Geometry"])
        last = nodes.new("ShaderNodeMath")
        last.operation = "SUBTRACT"
        last.inputs[1].default_value = 1.0
        links.new(count.outputs["Instance Count"], last.inputs[0])
        clamped = nodes.new("ShaderNodeMath")
        clamped.operation = "MINIMUM"
        links.new(rounded.outputs[0], clamped.inputs[0])
        links.new(last.outputs[0], clamped.inputs[1])
        positive = nodes.new("ShaderNodeMath")
        positive.operation = "MAXIMUM"
        positive.inputs[1].default_value = 0.0
        links.new(clamped.outputs[0], positive.inputs[0])
        index = nodes.new("GeometryNodeInputIndex")
        other = nodes.new("FunctionNodeCompare")
        other.data_type = "INT"
        other.operation = "NOT_EQUAL"
        links.new(index.outputs[0], other.inputs["A"])
        links.new(positive.outputs[0], other.inputs["B"])
        delete = nodes.new("GeometryNodeDeleteGeometry")
        delete.domain = "INSTANCE"
        links.new(frames.outputs["Instances"], delete.inputs["Geometry"])
        links.new(other.outputs[0], delete.inputs["Selection"])
        realize = nodes.new("GeometryNodeRealizeInstances")
        links.new(delete.outputs[0], realize.inputs[0])
        output = nodes.new("NodeGroupOutput")
        links.new(realize.outputs[0], output.inputs[0])
        for column, node in enumerate((scene_time, offset, rounded, clamped, positive, other, delete, realize, output)):
            node.location = (column * 200 - 400, 0)
        frames.location = (0, 300)
        first.location = (-600, -200)
        count.location = (200, 300)
        last.location = (400, 300)
        index.location = (600, -200)
        tree.description = "RG Houdini Bridge: plays back the frames cached from Houdini"
    tree.nodes["Frames"].inputs["Collection"].default_value = settings.cache_collection
    tree.nodes["First Frame"].outputs[0].default_value = float(settings.cache_first)
    return tree


def _player(settings, create=False):
    holder = settings.holder
    if holder is None:
        return None
    modifier = holder.modifiers.get(PLAYER_MODIFIER)
    if modifier is None and create:
        modifier = holder.modifiers.new(PLAYER_MODIFIER, "NODES")
    return modifier


def apply_use(settings):
    """Show the cache, or go back to live cooking (use_cache was changed): a live modifier
    cooks again, a locked one keeps the result it had before the cache."""
    if settings.use_cache and settings.cache_collection is None:
        settings.use_cache = False
        return
    if settings.use_cache:
        modifier = _player(settings, create=True)
        modifier.node_group = _player_tree(settings)
        modifier.show_viewport = modifier.show_render = True
        cook.cooks.cancel(settings.id_data)
        streams.clear(settings)  # the cached frames hold everything, realized
    else:
        modifier = _player(settings)
        if modifier is not None:
            modifier.show_viewport = modifier.show_render = False
        tree = settings.id_data
        if settings.enabled and settings.live:  # unless it is locked
            cook.cooks.request(tree, force=True)


def frame_count(settings):
    return len(settings.cache_collection.objects) if settings.cache_collection else 0


def shown_mesh(settings, frame):
    """The mesh the viewport shows: the cached frame in cache mode, else the holder's."""
    if settings.use_cache and settings.cache_collection is not None:
        objects = list(settings.cache_collection.objects)
        if objects:
            index = min(max(round(frame - settings.cache_first), 0), len(objects) - 1)
            return objects[index].data
    return settings.holder.data if settings.holder else None


def is_stale(target):
    """The parameters changed since the cache was made."""
    settings = target.settings
    return (bool(settings.cache_parms)
            and json.dumps(cook.parm_values(target), sort_keys=True) != settings.cache_parms)


# --- making a cache -------------------------------------------------------------------


def _new_collection(settings):
    # Linked to no scene: the player's Collection Info node is its only user.
    return bpy.data.collections.new("Houdini Cache · %s" % settings.holder.name)


def _collection(job):
    """The collection of a job, None once it is gone."""
    return bpy.data.collections.get(job.collection)


def _store(job, target, frame, answer, output):
    """One cooked frame as a mesh object of the job's collection. A frame Houdini gives no
    material shows the owner's, as the live result does."""
    collection = _collection(job)
    if collection is None:
        raise FrameError("frame %d: the collection of the cache was removed" % frame)
    name = "%s · %04d" % (job.name, frame)
    mesh = bpy.data.meshes.new(name)
    mesh_io.write_mesh(mesh, answer, output, target.own_materials())
    obj = bpy.data.objects.new(name, mesh)
    obj.houdini_bridge_result = True
    collection.objects.link(obj)


def _remove_collection(collection):
    for obj in list(collection.objects):
        mesh = obj.data
        bpy.data.objects.remove(obj)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.data.collections.remove(collection)


def _frames(first, last):
    return list(range(first, last + 1)) if last >= first else [first]


def _begin(tree, first, last):
    settings = tree.houdini_bridge
    frames = _frames(first, last)
    job = Job(tree=tree.name, uid=tree.session_uid, frames=frames, name=settings.holder.name,
              collection=_new_collection(settings).name, return_frame=bpy.context.scene.frame_current,
              started=time.perf_counter())
    cook.cooks.caching[job.uid] = (0, len(frames), job.started)
    settings.status = "caching %d frames…" % len(frames)
    return job


def _prepare(job, target):
    """Go to the job's next frame and prepare its cook."""
    frame = job.frames[job.index]
    bpy.context.scene.frame_set(frame)
    cook.mark_animated_inputs(target)
    # A cached frame is one mesh: instances are realized, like Houdini's own unpack.
    return frame, cook.prepare_request(target, always=True, output=contract.MESH)


def _received(job, target, frame, answer, output, error):
    """True when the frame was stored; False to ask for it again (lost inputs)."""
    if contract.MISSING_INPUTS in answer:
        cook.sent.forget(target.key)
        return False
    errors = [error] if error else answer.get("errors", [])
    if errors or "P" not in output:
        raise FrameError("frame %d: %s" % (frame, (errors or ["no geometry"])[0].splitlines()[-1]))
    if answer.get("parms"):
        cook.apply_description(target, answer)
    _store(job, target, frame, answer, output)
    job.index += 1
    cook.cooks.caching[job.uid] = (job.index, len(job.frames), job.started)
    return True


def _finish(job, target):
    settings = target.settings
    collection = _collection(job)
    if collection is None:
        _fail(job, settings, "caching failed: the collection of the cache was removed")
        return
    old = settings.cache_collection
    settings.cache_collection = collection
    settings.cache_first = job.frames[0]
    settings.cache_parms = json.dumps(cook.parm_values(target), sort_keys=True)
    if old is not None and old != collection and not _shared(old, settings):
        _remove_collection(old)
    if settings.use_cache:
        apply_use(settings)  # new collection and first frame for the player
    else:
        settings.use_cache = True  # its update shows the player
    _end(job)  # back to the user's frame, now played from the cache
    settings.status = "cached %d frames (%d–%d) in %.1f s" % (
        len(job.frames), job.frames[0], job.frames[-1], time.perf_counter() - job.started)


def _fail(job, settings, message):
    _drop_collection(job)
    _end(job)
    settings.status = message


def _drop_collection(job):
    collection = _collection(job)
    if collection is not None:
        _remove_collection(collection)


def _end(job):
    cook.cooks.caching.pop(job.uid, None)
    _jobs.pop(job.uid, None)
    bpy.context.scene.frame_set(job.return_frame)


def _shared(collection, settings):
    """Another Houdini modifier (a duplicate) plays the same cache."""
    return any(tree.houdini_bridge != settings and tree.houdini_bridge.cache_collection == collection
               for tree in targets.houdini_trees())


def start(tree, first, last):
    """Cache frames first to last in the background (main-thread timer + worker)."""
    if tree.session_uid in _jobs:
        return
    _jobs[tree.session_uid] = _begin(tree, first, last)
    cook.ensure_server()
    if not bpy.app.timers.is_registered(_step):
        bpy.app.timers.register(_step, first_interval=FIRST_STEP)


def cancel(tree):
    job = _jobs.get(tree.session_uid)
    if job is not None:
        job.cancel = True


def progress(uid):
    """(frames done, frame count) of a cache being made, or None."""
    state = cook.cooks.caching.get(uid)
    return (state[0], state[1]) if state else None


def _step():
    for uid, job in list(_jobs.items()):
        tree = bpy.data.node_groups.get(job.tree)
        if not targets.is_houdini_tree(tree) or tree.session_uid != uid:
            _drop_collection(job)
            _end(job)
            continue
        target = targets.Target(tree)
        settings = target.settings
        if job.waiting:
            continue
        if job.cancel:
            _fail(job, settings, "caching cancelled")
            continue
        if not cook.ensure_server():
            if link.server.startup_error:
                _fail(job, settings, "hython server unavailable")
            continue
        if job.index >= len(job.frames):
            _finish(job, target)
            continue
        try:
            frame, (header, arrays) = _prepare(job, target)
        except Exception as error:
            feedback.log.exception("a frame of the cache could not be prepared")
            _fail(job, settings, "caching failed: %s" % error)
            continue
        job.waiting = True

        def done(answer, output, error, job=job, frame=frame):
            job.waiting = False
            tree = bpy.data.node_groups.get(job.tree)
            if tree is None or _jobs.get(job.uid) is not job:
                return  # stopped (an undo), maybe with a new job of the same modifier
            target = targets.Target(tree)
            try:
                _received(job, target, frame, answer, output, error)
            except FrameError as failure:
                _fail(job, target.settings, "caching failed: %s" % failure)
            except Exception as failure:
                feedback.log.exception("a cached frame could not be stored")
                _fail(job, target.settings, "caching failed: %s" % failure)

        # The job in the key: a job started again after an undo does not take the answer of the old one.
        cook.post_cook(("cache", uid, id(job), frame), header, arrays, done)
    return STEP if _jobs else None


def cache_now(tree, first, last):
    """Synchronous version of start (tests, background mode). Returns the status."""
    cook.ensure_server(blocking=True)
    job = _begin(tree, first, last)
    target = targets.Target(tree)
    try:
        while job.index < len(job.frames):
            frame, (header, arrays) = _prepare(job, target)
            try:
                answer, output = link.server.request_sync(header, arrays)
                error = ""
            except hython.ServerError as failure:
                answer, output, error = {}, {}, str(failure)
            _received(job, target, frame, answer, output, error)
    except Exception as failure:
        if not isinstance(failure, FrameError):
            feedback.log.exception("caching failed")
        _fail(job, target.settings, "caching failed: %s" % failure)
        return target.settings.status
    _finish(job, target)
    return target.settings.status


def clear(settings):
    """Drop the cache of a Houdini modifier and go back to live cooking."""
    collection = settings.cache_collection
    modifier = _player(settings)
    if modifier is not None:
        tree = modifier.node_group
        settings.holder.modifiers.remove(modifier)
        if tree is not None and tree.users == 0:
            bpy.data.node_groups.remove(tree)
    settings.cache_collection = None
    settings.cache_parms = ""
    if collection is not None and not _shared(collection, settings):
        _remove_collection(collection)
    if settings.use_cache:
        settings.use_cache = False


def _on_setting_changed(settings, name):
    if name == "use_cache":
        apply_use(settings)


# --- interface ------------------------------------------------------------------------


class HOUDINI_BRIDGE_OT_cache(modifiers.ModifierOperator, bpy.types.Operator):
    """Cook a frame range once and keep it: playback and rendering then read the cached frames"""

    bl_idname = "houdini_bridge.cache"
    bl_label = "Cache Frames"

    action: EnumProperty(items=[
        ("START", "Cache", "Cook the frame range and keep it"),
        ("CANCEL", "Cancel", "Stop caching (the frames already cooked are dropped)"),
        ("CLEAR", "Clear", "Delete the cached frames and go back to live cooking"),
    ])

    def execute(self, context):
        tree = self.node_group()
        if tree is None:
            return {"CANCELLED"}
        settings = tree.houdini_bridge
        if self.action == "START" and targets.Target(tree).bypassed:
            self.report({"ERROR"}, "%s is bypassed: turn Bypass off to cache it" % settings.label)
            return {"CANCELLED"}
        if self.action == "START":
            cook.allow_file()  # the user's own action: the open file may cook (core/trust.py)
            scene = context.scene
            if settings.cache_scene_range:
                first, last = scene.frame_start, scene.frame_end
            else:
                first, last = settings.cache_start, settings.cache_end
            start(tree, first, last)
        elif self.action == "CANCEL":
            cancel(tree)
        else:
            clear(settings)
            settings.status = "cache cleared"
        return {"FINISHED"}


def draw_cache(layout, target, tree):
    """The Cache section of the modifier panel: frame range, Cache / Cancel, and the cached
    frames (use, clear, out of date)."""
    settings = target.settings
    column = layout.column(align=True)
    running = progress(tree.session_uid)
    row = column.row(align=True)
    row.label(text="Cache", icon="FILE_CACHE")
    row.prop(settings, "cache_scene_range", text="Scene Range", toggle=True)
    if not settings.cache_scene_range:
        row = column.row(align=True)
        row.prop(settings, "cache_start")
        row.prop(settings, "cache_end")
    if running is not None:
        row = column.row(align=True)
        row.label(text="Frame %d of %d" % running, icon="TIME")
        operator = row.operator("houdini_bridge.cache", text="Cancel", icon="CANCEL")
        operator.tree, operator.action = tree.name, "CANCEL"
        return
    operator = column.operator("houdini_bridge.cache", text="Cache Frames" if settings.cache_collection is None
                               else "Cache Again", icon="RENDER_ANIMATION")
    operator.tree, operator.action = tree.name, "START"
    if settings.cache_collection is not None:
        count = frame_count(settings)
        row = column.row(align=True)
        row.prop(settings, "use_cache", text="Use Cache: %d frames from %d" % (count, settings.cache_first),
                 toggle=True)
        operator = row.operator("houdini_bridge.cache", text="", icon="TRASH")
        operator.tree, operator.action = tree.name, "CLEAR"
        if settings.use_cache:
            column.label(text="Locked: the cached frames play, nothing cooks", icon="LOCKED")
        if settings.use_cache and is_stale(target):
            row = column.row()
            row.alert = True
            row.label(text="Parameters changed since: cache again", icon="ERROR")


@bpy.app.handlers.persistent
def _on_file_loaded(*_):
    _jobs.clear()


@bpy.app.handlers.persistent
def _on_undo(*_):
    """undo_pre and redo_pre: the caches being made stop. Their collections are in no undo
    step (the operator pushes none), so the step loaded frees them; the user's frame and the
    modifier's status come back with it."""
    for job in list(_jobs.values()):
        cook.cooks.caching.pop(job.uid, None)
        _jobs.pop(job.uid, None)


# (name of a bpy.app.handlers list, function), in the order they are added.
HANDLERS = (
    ("load_post", _on_file_loaded),
    ("undo_pre", _on_undo),
    ("redo_pre", _on_undo),
)


def register():
    bpy.utils.register_class(HOUDINI_BRIDGE_OT_cache)
    for name, function in HANDLERS:
        getattr(bpy.app.handlers, name).append(function)
    events.modifier_released.connect(clear)
    events.setting_changed.connect(_on_setting_changed)


def unregister():
    events.setting_changed.disconnect(_on_setting_changed)
    events.modifier_released.disconnect(clear)
    for name, function in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    if bpy.app.timers.is_registered(_step):
        bpy.app.timers.unregister(_step)
    bpy.utils.unregister_class(HOUDINI_BRIDGE_OT_cache)
