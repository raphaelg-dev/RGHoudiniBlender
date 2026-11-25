"""Cutting a slice of a modifier stack from the viewport for a while, and putting it back
exactly as it was.

Two features hide modifiers from the viewport and must restore them whatever happens
meanwhile (an undo, a redo, a file saved and opened again, the extension disabled): the
viewport group selection (features/selection/: the modifier and every one below it, while
a group is picked) and the Display flag (features/display.py: every modifier below the
flagged one).

A cut has a name ("selection", "display") and lives on its object, in the data
(Object.houdini_bridge_cuts, JSON), so an undo, a redo or a saved file always brings back
the cut with the switches it changed. It names modifiers by their persistent_uid, which
survives a rename, a move in the stack, a duplicated object and a saved file:

- "shown": each modifier a cut turned off, with its render switch at that time;
- "cuts": per name, the modifier it hangs from ("anchor"), the modifiers it holds off
  ("held"), and every modifier it has considered ("seen"), held or left as it was.

The rules:

- a cut turns off only a modifier shown in the viewport; one already off (the user's own
  choice) is left alone, and not restored;
- a modifier several cuts hold comes back when the last of them goes;
- a held modifier the user changed since (shown again, or its render switch changed: a
  bypass) is released: no cut holds it any more, and nothing restores it;
- a modifier gone from the stack is forgotten.

Only the viewport switch (show_viewport) is ever written, and only when it differs.
Main thread only.
"""

import json

from . import stored

PROPERTY = "houdini_bridge_cuts"  # the Object property holding the cuts (registered by properties.py)


def uid(modifier):
    """The key of a modifier in a cut: its persistent_uid, unique in its object."""
    return modifier.persistent_uid


def below(obj, modifier, including=False):
    """The modifiers of `obj` after `modifier` in its stack (and `modifier` too, with
    `including`), top down, whatever their type."""
    found, seen = [], False
    for other in obj.modifiers:
        if other == modifier:
            seen = True
            if not including:
                continue
        if seen:
            found.append(other)
    return found


def _read(obj):
    data = stored.parse(getattr(obj, PROPERTY, ""), {})
    data.setdefault("shown", {})
    data.setdefault("cuts", {})
    return data


def _write(obj, data):
    text = json.dumps(data, sort_keys=True) if data["cuts"] or data["shown"] else ""
    if getattr(obj, PROPERTY) != text:
        setattr(obj, PROPERTY, text)


def _by_uid(obj):
    return {str(uid(modifier)): modifier for modifier in obj.modifiers}


def _holders(data, key):
    return [name for name, cut in data["cuts"].items() if key in cut["held"]]


def _drop(data, key):
    """Release this modifier from every cut, without restoring it."""
    data["shown"].pop(key, None)
    for cut in data["cuts"].values():
        if key in cut["held"]:
            cut["held"].remove(key)


def _forget_changed(data, modifiers):
    """Forget the modifiers gone from the stack, and release those the user changed since a
    cut turned them off (shown again, or another render switch)."""
    for key, render in list(data["shown"].items()):
        modifier = modifiers.get(key)
        if modifier is None or modifier.show_viewport or modifier.show_render != render:
            _drop(data, key)
    for cut in data["cuts"].values():
        cut["seen"] = [key for key in cut["seen"] if key in modifiers]


def _take(data, cut, modifier):
    """Hold `modifier` off for `cut`: turned off when shown, kept off when another cut holds it,
    left alone when the user turned it off."""
    key = str(uid(modifier))
    if key not in cut["seen"]:
        cut["seen"].append(key)
    if key in cut["held"]:
        return
    if key in data["shown"]:
        cut["held"].append(key)
    elif modifier.show_viewport:
        data["shown"][key] = modifier.show_render
        modifier.show_viewport = False
        cut["held"].append(key)


def _restore(data, key, modifiers):
    """Show a modifier again when no cut holds it any more and it is as the cut left it."""
    if _holders(data, key) or key not in data["shown"]:
        return
    render = data["shown"].pop(key)
    modifier = modifiers.get(key)
    if modifier is not None and not modifier.show_viewport and modifier.show_render == render:
        modifier.show_viewport = True


def hold(obj, name, modifiers, anchor=None):
    """Start the cut `name` on `obj` (or add to it): every modifier of `modifiers` shown in the
    viewport is turned off. `anchor` is the modifier the cut hangs from, or None."""
    data = _read(obj)
    _forget_changed(data, _by_uid(obj))
    cut = data["cuts"].setdefault(name, {"anchor": None, "held": [], "seen": []})
    if anchor is not None:
        cut["anchor"] = str(uid(anchor))
    for modifier in modifiers:
        _take(data, cut, modifier)
    _write(obj, data)


def release(obj, name):
    """End the cut `name` on `obj`: each modifier it held and no other cut holds is shown again,
    unless the user changed it meanwhile. Nothing happens when there is no such cut."""
    data = _read(obj)
    if name not in data["cuts"]:
        return
    modifiers = _by_uid(obj)
    _forget_changed(data, modifiers)
    cut = data["cuts"].pop(name)
    for key in cut["held"]:
        _restore(data, key, modifiers)
    _write(obj, data)


def settle(obj, name, wanted):
    """Make the cut `name` hold `wanted` (the modifiers it must hold now, its rule applied to
    the stack as it is): a modifier it considered and no longer wants (moved above its anchor)
    is restored, and a modifier it never considered (added, or moved below) is taken. A
    modifier it considered and left alone, or that the user took back, stays as it is."""
    data = _read(obj)
    cut = data["cuts"].get(name)
    if cut is None:
        return
    modifiers = _by_uid(obj)
    _forget_changed(data, modifiers)
    keys = [str(uid(modifier)) for modifier in wanted]
    for key in [key for key in cut["seen"] if key not in keys]:
        cut["seen"].remove(key)
        if key in cut["held"]:
            cut["held"].remove(key)
            _restore(data, key, modifiers)
    for modifier in wanted:
        if str(uid(modifier)) not in cut["seen"]:
            _take(data, cut, modifier)
    _write(obj, data)


def changed_by_user(obj, name):
    """Whether the user changed a modifier the cut `name` holds since the cut turned it off:
    showed it again, or changed its render switch (a bypass). Ask it before anything else
    reads the cut, since every other function forgets such a modifier."""
    data = _read(obj)
    cut = data["cuts"].get(name)
    if cut is None:
        return False
    modifiers = _by_uid(obj)
    for key in cut["held"]:
        modifier = modifiers.get(key)
        if modifier is not None and key in data["shown"] and (
                modifier.show_viewport or modifier.show_render != data["shown"][key]):
            return True
    return False


def anchor(obj, name):
    """The modifier the cut `name` hangs from on `obj`, or None (no such cut, or gone)."""
    cut = _read(obj)["cuts"].get(name)
    if cut is None or cut["anchor"] is None:
        return None
    return _by_uid(obj).get(cut["anchor"])


def has(obj, name):
    return name in _read(obj)["cuts"]


def held_by(modifier):
    """The names of the cuts that hold this modifier off."""
    obj = modifier.id_data
    if not getattr(obj, PROPERTY, ""):
        return ()
    return tuple(_holders(_read(obj), str(uid(modifier))))


def objects_with(objects, name):
    """The objects of `objects` (bpy.data.objects) the cut `name` is on."""
    return [obj for obj in objects if getattr(obj, PROPERTY, "") and name in _read(obj)["cuts"]]
