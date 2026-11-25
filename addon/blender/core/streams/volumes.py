"""The volume stream: a Volume object reading the .vdb file the server writes, its
default materials, and the copies of that file Apply keeps."""

import math
import os
import re
import shutil
import tempfile

import bpy
from mathutils import Matrix

from ....shared import contract
from .. import feedback, hython, link, mesh_io, paths, preferences
from . import common

OWN_MATERIALS = ("Houdini Volume", "Houdini Level Set")  # the default materials, by name
LEVEL_SET_DENSITY = 200.0  # the density inside a level set, in its default material
MIN_DISPLAY_DENSITY = 1e-3  # a lower viewport density hides the volume: it is set to 1


def _default_root():
    return os.path.join(tempfile.gettempdir(), "rg_houdini_bridge", "volumes")


def volume_dir():
    """Where the server writes the .vdb files Blender reads (preference "Live Volume
    Files"). They are temporary: a file opened again recooks every modifier. By default, a
    folder of this Blender's own, named by its process id: another Blender's files are
    never this one's to remove."""
    folder = preferences.get().live_volume_folder
    if folder:
        return bpy.path.abspath(folder)
    return os.path.join(_default_root(), str(os.getpid()))


def remove_orphan_volume_files():
    """Remove the .vdb files that no volume of this Blender reads, from the default folders
    of this Blender and of the Blenders that have ended, and those folders once empty. A
    server removes its files when it exits, but a hython killed (with Blender, or by Stop
    during a long cook) or crashed left them. Called when a new server session begins. The
    folder of a Blender still running, and a folder set in the preferences, are left alone."""
    if preferences.get().live_volume_folder:
        return
    root = _default_root()
    try:
        names = os.listdir(root)
    except OSError:
        return  # no volume written yet
    read = {os.path.normcase(os.path.abspath(bpy.path.abspath(volume.filepath)))
            for volume in bpy.data.volumes if volume.filepath}
    own = str(os.getpid())
    for name in names:
        folder = os.path.join(root, name)
        if not name.isdigit() or not os.path.isdir(folder) or (name != own and hython.process_alive(int(name))):
            continue
        try:
            file_names = os.listdir(folder)
        except OSError:
            continue  # removed meanwhile
        for file_name in file_names:
            path = os.path.join(folder, file_name)
            if not re.fullmatch(r"volume_.*\.vdb", file_name) or os.path.normcase(os.path.abspath(path)) in read:
                continue
            try:
                os.remove(path)
            except OSError:
                pass  # still held
        if name != own:
            try:
                os.rmdir(folder)
            except OSError:
                pass  # a file this Blender reads (a file saved by that Blender), or another file


def _new_material(name):
    """A new material whose node tree holds its Material Output alone."""
    material = bpy.data.materials.new(name)
    nodes = material.node_tree.nodes
    for node in list(nodes):
        if node.bl_idname != "ShaderNodeOutputMaterial":
            nodes.remove(node)
    return material


def _volume_material():
    """Density read from the "density" grid, for volumes that come without a material."""
    material = bpy.data.materials.get("Houdini Volume")
    if material is None:
        material = _new_material("Houdini Volume")
        nodes = material.node_tree.nodes
        shader = nodes.new("ShaderNodeVolumePrincipled")
        shader.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1.0)  # like Houdini's white smoke
        shader.location = (-300, 0)
        material.node_tree.links.new(shader.outputs[0], nodes["Material Output"].inputs["Volume"])
    return material


def _level_set_material(grid):
    """A level set (signed distance) rendered solid: dense where the distance is negative,
    inside the surface, empty outside. The grid itself stays a distance field, for the SDF
    nodes of Geometry Nodes."""
    name = "Houdini Level Set · %s" % grid
    material = bpy.data.materials.get(name)
    if material is None:
        material = _new_material(name)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        distance = nodes.new("ShaderNodeAttribute")
        distance.attribute_name = grid
        distance.location = (-700, 0)
        inside = nodes.new("ShaderNodeMath")
        inside.operation = "LESS_THAN"
        inside.inputs[1].default_value = 0.0
        inside.location = (-500, 0)
        links.new(distance.outputs["Fac"], inside.inputs[0])
        dense = nodes.new("ShaderNodeMath")
        dense.operation = "MULTIPLY"
        dense.inputs[1].default_value = LEVEL_SET_DENSITY
        dense.location = (-320, 0)
        links.new(inside.outputs[0], dense.inputs[0])
        shader = nodes.new("ShaderNodeVolumeScatter")
        shader.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1.0)
        shader.location = (-140, 0)
        links.new(dense.outputs[0], shader.inputs["Density"])
        links.new(shader.outputs[0], nodes["Material Output"].inputs["Volume"])
    return material


def new_data(name):
    return bpy.data.volumes.new(name)


def write(settings, holder, info, output):
    volume = holder.data
    if bpy.path.abspath(volume.filepath) != info["path"]:
        volume.filepath = info["path"]
        volume.grids.load()
    if info.get("materials"):
        common.materials(volume, info["materials"])
    elif not volume.materials or volume.materials[0] is None or volume.materials[0].name.startswith(OWN_MATERIALS):
        # Our default follows the grids: a fog reads density, a level set its inside.
        level_sets = [grid for grid, kind in zip(info["grids"], info["classes"], strict=True) if kind == "level set"]
        default = _level_set_material(level_sets[0]) if level_sets else _volume_material()
        if not volume.materials or volume.materials[0] != default:
            volume.materials.clear()
            volume.materials.append(default)
    if volume.display.density < MIN_DISPLAY_DENSITY:
        volume.display.density = 1.0
    volume.update_tag()
    return True


def found(info):
    return {contract.VOLUME: info["grids"]}


def text(summary):
    return "volume (%s)" % ", ".join(summary[contract.VOLUME]) if summary.get(contract.VOLUME) else ""


def empty(settings):
    holder = settings.volume_holder
    if holder is not None and holder.data.filepath:
        holder.data.filepath = ""
        holder.data.update_tag()


def volume_file_gone(settings):
    """Whether the .vdb file the volume holder reads is gone (an undo brought back the path
    of a file a later cook replaced, or the file was removed), so the modifier must cook again."""
    holder = settings.volume_holder
    return bool(holder is not None and holder.data.filepath
                and not os.path.exists(bpy.path.abspath(holder.data.filepath)))


def show(tree, info, row):
    socket = common.as_geometry(tree, info, row)
    # The grids are in Houdini space: Y up becomes Z up.
    turn = tree.nodes.new("GeometryNodeTransform")
    turn.name = turn.label = "Y up to Z up"
    turn.inputs["Rotation"].default_value = (math.radians(90.0), 0.0, 0.0)
    turn.location = (-200, -200 * row)
    tree.links.new(socket, turn.inputs["Geometry"])
    return turn.outputs["Geometry"]


def _kept_where():
    """The preference "Applied Volumes" as it acts: BESIDE or FOLDER, an empty FOLDER being BESIDE."""
    prefs = preferences.get()
    if prefs.applied_volumes == "FOLDER" and not prefs.applied_folder:
        return "BESIDE"
    return prefs.applied_volumes


def applied_volume_mode():
    """Where Apply keeps a volume (preference "Applied Volumes"): BESIDE the saved .blend,
    or a FOLDER (an empty one falls back to BESIDE). An unsaved file gives UNSAVED: the
    extension's folder until the first save, which moves the files next to the .blend (see
    on_save). Packing does not help: Blender 5.2 never reads a packed volume, it opens the
    file path."""
    mode = _kept_where()
    if mode == "BESIDE" and not bpy.data.filepath:
        mode = "UNSAVED"
    return mode


def _beside_folder(blend_path):
    name = preferences.get().applied_folder_name or preferences.DEFAULTS["applied_folder_name"]
    return os.path.join(os.path.dirname(blend_path), name)


@bpy.app.handlers.persistent
def on_save(filepath, *_):
    """save_pre: move the volumes applied before the file had a name next to the .blend
    being written, with a path relative to it, so the saved file holds no path into the
    extension's folder."""
    prefs = preferences.get()
    if not filepath or _kept_where() != "BESIDE":
        return
    folder = _beside_folder(filepath)
    for volume in bpy.data.volumes:
        path = bpy.path.abspath(volume.filepath) if volume.filepath else ""
        if not path or not is_waiting(path) or not os.path.exists(path):
            continue
        os.makedirs(folder, exist_ok=True)
        destination = _free_path(folder, *os.path.splitext(os.path.basename(path)))
        shutil.move(path, destination)
        relative = prefs.relative_volume_paths
        try:
            volume.filepath = "//" + os.path.relpath(destination, os.path.dirname(filepath)).replace("\\", "/") \
                if relative else destination
        except ValueError:  # another drive
            volume.filepath = destination


def register():
    bpy.app.handlers.save_pre.append(on_save)


def unregister():
    if on_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(on_save)


def _free_path(folder, stem, extension):
    """folder/<stem><extension>, numbered _2, _3... when that file exists."""
    path = os.path.join(folder, stem + extension)
    number = 1
    while os.path.exists(path):
        number += 1
        path = os.path.join(folder, "%s_%d%s" % (stem, number, extension))
    return path


def is_waiting(path):
    """Whether a volume was applied in an unsaved file and waits in the extension's folder
    for the first save."""
    folder = os.path.normcase(os.path.normpath(paths.applied_volumes_dir()))
    return os.path.normcase(os.path.dirname(os.path.normpath(bpy.path.abspath(path)))) == folder


def _relative(path):
    """`path` relative to the .blend when the preference asks and the file is saved (not
    across drives)."""
    if not bpy.data.filepath or not preferences.get().relative_volume_paths:
        return path
    try:
        return bpy.path.relpath(path)
    except ValueError:
        return path


def _kept_volume_path(source, name):
    """Copy a temporary .vdb the server wrote to where it lasts (see applied_volume_mode).
    Houdini writes the copy turned to Blender's axes (Z up); without a server, the file is
    copied as it is. Returns (path, turned)."""
    mode = applied_volume_mode()
    if mode == "FOLDER":
        folder = bpy.path.abspath(preferences.get().applied_folder)
    elif mode == "BESIDE":
        folder = _beside_folder(bpy.data.filepath)
    else:
        folder = paths.applied_volumes_dir()
    os.makedirs(folder, exist_ok=True)
    stem = re.sub(r"[^\w\-]+", "_", name).strip("_") or "volume"
    path = _free_path(folder, stem, ".vdb")
    turned = False
    if link.server.is_running():
        to_blender = mesh_io.TO_HOUDINI.inverted()
        rows = [value for row in to_blender.transposed() for value in row]
        try:
            header = contract.request(contract.WRITE_VOLUME, source=source, destination=path, matrix=rows)
            answer, _ = link.server.request_sync(header)
            turned = bool(answer.get("ok"))
        except hython.ServerError as error:  # the copy below, turned by the object instead
            feedback.log.warning("Houdini did not write the applied volume (%s): "
                                 "the file is copied as it is", error)
    if not turned:
        shutil.copyfile(source, path)
    return (path if mode == "UNSAVED" else _relative(path)), turned


def apply(settings, owner, place):
    holder = settings.volume_holder
    if holder is not None and holder.data.filepath:
        volume = holder.data.copy()
        volume.filepath, turned = _kept_volume_path(bpy.path.abspath(holder.data.filepath), owner.name)
        if volume.display.density < MIN_DISPLAY_DENSITY:
            volume.display.density = 1.0
        # Grids left in Houdini space (Y up) are turned by the object instead.
        place(None, contract.VOLUME, volume, None if turned else Matrix.Rotation(math.radians(90.0), 4, "X"))


STREAM = common.Stream(name=contract.VOLUME, holder="volume_holder", new_data=new_data, write=write, found=found,
                       text=text, empty=empty, show=show, apply=apply)
