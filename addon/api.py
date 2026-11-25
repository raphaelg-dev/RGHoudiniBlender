"""The scripting interface of RG Houdini Bridge. Its names and arguments are kept from one
version to the next; the rest of the package may change at any time.

    from bl_ext.user_default.rg_houdini_bridge import api

The extension must be enabled. A Houdini modifier is a Geometry Nodes modifier whose node
group carries the settings (`node_group.houdini_bridge`); the functions that act on one take
that node group, `modifier.node_group`. Every function runs on the main thread and waits for
Houdini, so they also work with `blender --background`, where Blender runs no timer and the
extension never cooks by itself.
"""

from .blender.core import cook, modifiers, parameters
from .blender.features import cache
from .blender.features.shelf import catalog

__all__ = [
    "start_server", "stop_server",
    "place_hda", "houdini_modifiers", "set_parameter", "cook_now",
    "cache_now", "apply_modifier", "remove_modifier", "refresh_shelf",
]


def start_server():
    """Start Houdini (hython) and wait until it answers; True once it does. When it cannot
    start, raises the error hython gave (a RuntimeError). Starting a server that already
    runs does nothing. The functions below that need Houdini start it themselves."""
    return cook.ensure_server(blocking=True)


def stop_server():
    """Stop Houdini. What it knew is forgotten: the next cook starts it again and sends
    every input in full. Call it at the end of a script run with `--background`."""
    return cook.stop_server()


def place_hda(context, path, definition, target=None):
    """Use an HDA, or a Houdini node, as the shelf does.

    - `context`: `bpy.context`; the active object and the selected objects decide where
      the tool goes and what feeds its inputs.
    - `path`: the HDA file, or "" for a node shipped with Houdini.
    - `definition`: a dict with "type" (the node type, "labs::edge_damage::2.1"), "label"
      (the name the modifier and a new object get) and "max_inputs" (its input count,
      0 for a generator).
    - `target`: "MODIFIER" puts it on the active mesh or Volume object, with the other
      selected objects as its other inputs (a new object when there is no such active
      object); "NEW" makes a new object, with the selected objects as its inputs. None
      follows the *Apply to* preference. A generator always makes a new object, at the
      3D cursor unless the *Generators at the 3D Cursor* preference is off.

    Returns the new modifier when it went on the active object, else the new object
    (its Houdini modifier is `houdini_modifiers(obj)[0]`). Nothing is cooked yet: call
    cook_now(), or let the timer do it in a session with a window."""
    return modifiers.place_hda(context, path, definition, target)


def houdini_modifiers(obj):
    """The Houdini modifiers of an object, in stack order: [(modifier, node group)]."""
    return modifiers.houdini_modifiers(obj)


def set_parameter(modifier, name, value):
    """Set a parameter of a Houdini modifier, by its Houdini name ("height"), in the format
    the server uses: a list of numbers for a float or int parameter ([0.6], or one number
    per component, [1.0, 0.5, 0.2]), True or False for a toggle, a string for a string,
    the token of a menu item for a menu. Only the parameters the last cook described exist.
    A value outside the limits of the parameter is brought within them when it is sent.
    Nothing is cooked."""
    return parameters.write_value(modifier, modifier.node_group.houdini_bridge.parms[name], value)


def cook_now(node_group, force=False):
    """Cook a Houdini modifier now and write its result, waiting for Houdini (started when
    needed, see start_server). Without `force`, nothing is sent when no input and no
    parameter changed since the last cook; with it, every input is sent again. Returns
    the answer of the server, {} when nothing was sent. A failed cook leaves its message
    in `node_group.houdini_bridge.errors`, and the last result stays."""
    return cook.cook_now(node_group, force=force)


def cache_now(node_group, first, last):
    """Cache the frames `first` to `last` of a Houdini modifier now, as the *Cache* section
    does, and use the cache. Blender is set to each frame in turn. Returns the status
    line of the modifier."""
    return cache.cache_now(node_group, first, last)


def apply_modifier(context, node_group, with_above=False):
    """Apply a Houdini modifier as the *Apply* button of its panel does, and with `with_above`
    every modifier above it too. The mesh becomes the object's mesh; instances, curves,
    points and a volume become objects of their own. Returns (object, new objects, number
    of groups that joined the Groups panel); the object may be a new one that took the
    place of an emptied original. Raises ValueError, before anything changes, for a bypassed
    modifier, for one the Display flag of a modifier above cuts from the viewport (lift the
    flag first), and for a mesh Blender's Apply refuses (shared, with shape keys, linked)."""
    return modifiers.apply_modifier(context, node_group, with_above)


def remove_modifier(node_group):
    """Remove a Houdini modifier from every object using it, and release at once what it
    holds (its holder, its streams, its Houdini instance, its cache). The objects' own
    meshes are untouched. In a session with a window, removing the modifier from the stack
    (`obj.modifiers.remove`, Blender's own X) releases the same within a second; in
    `blender --background`, where no timer runs, this does it now."""
    return modifiers.remove_modifier(node_group)


def refresh_shelf():
    """Read the Houdini tools again now, as the refresh button of the shelf does, and wait
    for Houdini. The catalog, its icons and the asset library are written again."""
    return catalog.refresh_now()
