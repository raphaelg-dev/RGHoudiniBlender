"""The Template flag of a Houdini modifier, like Houdini's: the result of this HDA is drawn as
a ghost wireframe in the viewport while the stack shows as usual, so that a step of the
stack can be seen without cutting it. Several modifiers of an object may be templated.

What is drawn is the mesh the modifier already holds (settings.holder, in the owner's local
space) or the cached frame being played: nothing is cooked. The native streams (instances,
curves, points, volume) are not drawn. A modifier bypassed, in error or not cooked yet draws
nothing.

The edges are one indexed batch of lines per result, dropped when a new result is written
and after an undo, a redo or a file load (another mesh may then sit at the same address).
"""

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ...core import events, feedback, targets
from .. import cache

COLOR = (0.93, 0.42, 0.72, 0.38)  # the pink of the flag (core/icons.py TEMPLATE_COLOR), faded

_batches = {}  # target key -> (what the batch was made from, batch)
_handles = []


def _mesh(target):
    """The mesh a templated modifier shows, or None when it draws nothing."""
    settings = target.settings
    if not settings.template or not target.valid or target.bypassed or settings.errors or not settings.status:
        return None
    try:
        if not target.owner.visible_get():
            return None
    except RuntimeError:  # not in the current view layer
        return None
    mesh = cache.shown_mesh(settings, bpy.context.scene.frame_current)
    return mesh if mesh is not None and len(mesh.edges) else None


def build(mesh):
    """The edges of a mesh as an indexed batch of lines."""
    positions = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", positions)
    edges = np.empty(len(mesh.edges) * 2, np.int32)
    mesh.edges.foreach_get("vertices", edges)
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return batch_for_shader(shader, "LINES", {"pos": positions.reshape(-1, 3)}, indices=edges.reshape(-1, 2))


def batch(target, mesh):
    """The batch of a templated modifier's mesh, rebuilt only when its result changed."""
    made_from = (mesh.as_pointer(), len(mesh.vertices), len(mesh.edges))
    entry = _batches.get(target.key)
    if entry is None or entry[0] != made_from:
        entry = (made_from, build(mesh))
        _batches[target.key] = entry
    return entry[1]


def templated():
    """(target, mesh) of each templated modifier with something to draw. Runs at each redraw
    of each 3D view, so the flag is read on the node groups first: a targets.Target, which
    looks for the modifier's object, is made only for the flagged ones."""
    found = []
    for tree in targets.houdini_trees():
        if not tree.houdini_bridge.template:
            continue
        target = targets.Target(tree)
        mesh = _mesh(target)
        if mesh is not None:
            found.append((target, mesh))
    return found


def _forget_unflagged():
    """Free the batches of the modifiers whose flag is lifted or that are gone. One merely not
    drawn now (bypassed, its owner hidden in this view layer) keeps its batch."""
    if not _batches:
        return
    keep = {tree.session_uid for tree in targets.houdini_trees() if tree.houdini_bridge.template}
    for key in [key for key in _batches if key not in keep]:
        del _batches[key]


def draw():
    """POST_VIEW: the wireframe of every templated modifier, depth-tested against the scene."""
    space = bpy.context.space_data
    if space is not None and getattr(space, "overlay", None) is not None and not space.overlay.show_overlays:
        return
    try:
        _forget_unflagged()
        shown = templated()
    except Exception as error:  # a template must never break the viewport
        feedback.log.warning("template: %s", error)
        return
    if not shown:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    try:
        for target, mesh in shown:
            gpu.matrix.push()
            gpu.matrix.multiply_matrix(target.owner.matrix_world)
            shader.uniform_float("color", COLOR)
            batch(target, mesh).draw(shader)
            gpu.matrix.pop()
    except Exception as error:  # a template must never break the viewport
        feedback.log.warning("template: %s", error)
    finally:
        gpu.state.depth_mask_set(True)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")


def _on_result(target, extras):
    """A new result: rebuild its batch at the next draw."""
    _batches.pop(target.key, None)


@bpy.app.handlers.persistent
def _forget_all(*_):
    _batches.clear()


HANDLERS = ("undo_post", "redo_post", "load_post")


def register():
    events.result_applied.connect(_on_result)
    for name in HANDLERS:
        getattr(bpy.app.handlers, name).append(_forget_all)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_VIEW"))


def unregister():
    while _handles:
        bpy.types.SpaceView3D.draw_handler_remove(_handles.pop(), "WINDOW")
    for name in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if _forget_all in handlers:
            handlers.remove(_forget_all)
    events.result_applied.disconnect(_on_result)
    _batches.clear()
