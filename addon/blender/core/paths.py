"""The name of the extension's package and the folders it keeps in the user's profile."""

import os

import bpy

# The root package of the extension as Blender knows it (bl_ext.user_default.rg_houdini_bridge
# once installed): what the preferences' bl_idname, the lookup of the add-on and its user folder
# expect. This file's own __package__ is <root>.blender.core.
PACKAGE = __package__.removesuffix(".blender.core")


def user_dir():
    """The extension's folder in the user's profile (catalog, favorites, icons, library…)."""
    return bpy.utils.extension_path_user(PACKAGE, path="", create=True)


def user_file(name):
    """A file or folder at the top of the user folder (catalog.json, favorites.json…)."""
    return os.path.join(user_dir(), name)


def icons_dir():
    """The tool icons rendered by hython, and the Houdini logo."""
    return user_file("icons")


def houdini_logo():
    return os.path.join(icons_dir(), "houdini_logo.png")


def status_icons_dir():
    """The state icons of the header button."""
    return user_file("status_icons")


def library_dir():
    """The asset library written from the catalog."""
    return user_file("library")


def applied_volumes_dir():
    """Where Apply keeps a volume while its .blend file is not saved yet."""
    return user_file("applied_volumes")
