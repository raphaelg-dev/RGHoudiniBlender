"""Editing a code parameter (a wrangle's VEX, a Python SOP's script) in Blender's Text Editor,
in a window of its own, like Houdini's floating code editor.

The modifier input holds the code, which is sent and cooked like any string. The text in the
editor only mirrors it:
- made from the parameter each time the editor opens, as a runtime Text (ID.is_runtime_data),
  which a save never writes but undo keeps;
- followed by a timer while a mirror is open: what is typed reaches the parameter once it has
  rested DELAY seconds, and a parameter changed elsewhere (an undo, a script) is copied into
  the text;
- closed when its window is closed, when its modifier or parameter is gone, when an undo goes
  back to before it was opened, before a file is loaded and when the extension is disabled.
  Before a save, what was typed is written into the parameter. A file saved with an editor open
  keeps its window but not its text: the empty window is closed once the file is loaded.

Blender 5.2 crashes undoing a typing step of a Text that is gone (txt_from_buf_for_undo), so
close() pushes an undo step right after removing the text. In background mode a mirror has no
window, and follow() is called by hand.
"""

import time
from dataclasses import dataclass

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty

from ..core import feedback, modifiers, parameters, targets

# Blender picks the highlighting of a Text by the extension of its name. Of the highlighters of
# Blender 5.2 (Python, OSL, GLSL, POV-Ray), OSL colours VEX best: its types are VEX's (float,
# int, vector, matrix, string), with C's keywords, strings, numbers, comments and #include;
# GLSL leaves vector and string plain. Any other language is plain text.
EXTENSIONS = {"vex": ".osl", "python": ".py"}
PLAIN = ".txt"
TICK = 0.15  # seconds between two looks at the open mirrors
DELAY = 0.4  # what is typed reaches the parameter once it has rested this long, so a cook per pause


@dataclass
class Mirror:
    """One code parameter open in the Text Editor. Names only: an undo replaces the Blender data."""

    tree: str  # the name of the modifier's node group
    parm: str  # the Houdini name of the parameter
    text: str  # the name of the Text
    window: object  # the window opened for it, compared with the open ones; None without windows
    synced: str  # the code the text and the parameter last agreed on
    typed: str = ""  # the text as last seen while it differs from `synced`
    typed_at: float = 0.0  # when it was last seen changing


_mirrors = []


def text_name(tree, parm):
    """Unique per node group and parameter; the extension picks the highlighting."""
    return "%s · %s%s" % (tree.name, parm.name, EXTENSIONS.get(parm.editor, PLAIN))


def mirrors():
    """(node group name, parameter name, Text name) of each open mirror."""
    return [(mirror.tree, mirror.parm, mirror.text) for mirror in _mirrors]


def _code_parm(tree_name, parm_name):
    """(modifier, parameter) of a code parameter whose Houdini modifier is still there, else None."""
    tree = bpy.data.node_groups.get(tree_name)
    if not targets.is_houdini_tree(tree):
        return None
    parm = tree.houdini_bridge.parms.get(parm_name)
    users = targets.modifier_users(tree)
    if parm is None or not parm.editor or not users:
        return None
    return users[0][1], parm


def _value(found):
    modifier, parm = found
    return parameters.read_value(modifier, parm) or ""


def _find(tree_name, parm_name):
    return next((m for m in _mirrors if (m.tree, m.parm) == (tree_name, parm_name)), None)


def _window_of(mirror):
    """The mirror's window while it is open, else None (compared, never read once closed)."""
    if mirror.window is None:
        return None
    return next((window for window in bpy.context.window_manager.windows if window == mirror.window), None)


def _open_window(context, text):
    """A new window showing the text, or None without windows. Blender's Preferences window
    (temporary, reused when one is open) is opened and its only area becomes a Text Editor."""
    if bpy.app.background or not context.window_manager.windows:
        return None
    with context.temp_override(window=context.window or context.window_manager.windows[0]):
        bpy.ops.screen.userpref_show()
    window = next((w for w in reversed(context.window_manager.windows)
                   if len(w.screen.areas) == 1 and w.screen.areas[0].type == "PREFERENCES"), None)
    if window is None:
        return None
    area = window.screen.areas[0]
    area.type = "TEXT_EDITOR"
    space = area.spaces.active
    space.text = text
    space.show_line_numbers = True
    space.show_syntax_highlight = True
    space.show_word_wrap = False
    space.top = 0
    return window


def is_open(tree_name, parm_name):
    """Its text exists, and so does its window when it has one."""
    mirror = _find(tree_name, parm_name)
    return (mirror is not None and bpy.data.texts.get(mirror.text) is not None
            and (mirror.window is None or _window_of(mirror) is not None))


def open_editor(context, tree, parm_name):
    """Open a code parameter in the Text Editor, or keep the mirror already open for it. Returns
    the Text, or None when the parameter is not a code parameter of a Houdini modifier."""
    found = _code_parm(tree.name, parm_name)
    if found is None:
        return None
    mirror = _find(tree.name, parm_name)
    if mirror is not None:
        if is_open(tree.name, parm_name):
            return bpy.data.texts[mirror.text]
        flush()  # its window closed since the timer last looked: what was typed stays,
        _mirrors.remove(mirror)  # and its text is used again
    code = _value(found)
    name = text_name(tree, found[1])
    # A runtime Text of that name is a mirror an undo or a redo brought back: it is used again.
    text = bpy.data.texts.get(name)
    if text is None or not text.is_runtime_data:
        text = bpy.data.texts.new(name)
        text.is_runtime_data = True  # never written in a .blend file
    text.from_string(code)
    text.cursor_set(0)
    _mirrors.append(Mirror(tree.name, parm_name, text.name, _open_window(context, text), code))
    if not bpy.app.background and not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=TICK)
    return text


def _push_undo(message):
    """Push an undo step (none in background mode)."""
    windows = bpy.context.window_manager.windows
    if bpy.app.background or not windows:
        return
    try:
        with bpy.context.temp_override(window=windows[0]):
            bpy.ops.ed.undo_push(message=message)
    except RuntimeError as error:
        feedback.log.info("no undo step for %s (%s)", message, error)


def close(mirror, undo=True):
    """Remove the mirror's text, push an undo step when `undo` (see the module docstring), and
    close its window."""
    if mirror in _mirrors:
        _mirrors.remove(mirror)
    text = bpy.data.texts.get(mirror.text)
    if text is not None and text.is_runtime_data:
        bpy.data.texts.remove(text)
        if undo:
            _push_undo("Close Code Editor")
    window = _window_of(mirror)
    if window is not None:
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.window_close()


def close_all(undo=True):
    for mirror in list(_mirrors):
        close(mirror, undo)


def _cursor_kept(text, code):
    """Replace the text, keeping its cursor where it was (as far as the new text goes)."""
    line, character = text.current_line_index, text.current_character
    text.from_string(code)
    line = min(line, len(text.lines) - 1)
    text.cursor_set(line, character=min(character, len(text.lines[line].body)))


def _follow(mirror, now):
    found = _code_parm(mirror.tree, mirror.parm)
    text = bpy.data.texts.get(mirror.text)
    if found is None or text is None or (mirror.window is not None and _window_of(mirror) is None):
        if found is not None and text is not None and text.as_string() != _value(found):
            parameters.write_value(*found, text.as_string())  # the window closed: what was typed stays
        close(mirror)
        return
    code, value = text.as_string(), _value(found)
    if code == value:
        mirror.synced = code
    elif code != mirror.synced:  # typed: written once it has rested
        if code != mirror.typed:
            mirror.typed, mirror.typed_at = code, now
        elif now - mirror.typed_at >= DELAY:
            parameters.write_value(*found, code)
            mirror.synced = code
    else:  # the parameter changed elsewhere: the text follows
        _cursor_kept(text, value)
        mirror.synced = value


def follow(now=None):
    """Sync each open mirror once (the timer calls it every TICK seconds), and close those whose
    window, text or parameter is gone."""
    now = time.monotonic() if now is None else now
    for mirror in list(_mirrors):
        _follow(mirror, now)


def _tick():
    follow()
    return TICK if _mirrors else None  # nothing open: the timer stops


def flush():
    """Write what was typed and not sent yet into the parameters."""
    for mirror in _mirrors:
        found = _code_parm(mirror.tree, mirror.parm)
        text = bpy.data.texts.get(mirror.text)
        if found is not None and text is not None and text.as_string() != _value(found):
            parameters.write_value(*found, text.as_string())
            mirror.synced = text.as_string()


@persistent
def _on_save_pre(*args):
    flush()


@persistent
def _on_load_pre(*args):
    close_all(undo=False)  # the undo history goes with the file


def _empty_editor_windows():
    """Temporary windows holding only an empty Text Editor: mirrors saved with the file, whose
    runtime Text was not written."""
    return [window for window in bpy.context.window_manager.windows
            if window.screen.is_temporary and len(window.screen.areas) == 1
            and window.screen.areas[0].type == "TEXT_EDITOR" and window.screen.areas[0].spaces.active.text is None]


@persistent
def _on_load_post(*args):
    for window in _empty_editor_windows():
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.window_close()


def draw_edit(layout, tree, parm):
    """The Edit button of a code parameter, drawn in the modifier panel's code block."""
    button = layout.operator(HOUDINI_BRIDGE_OT_edit_code.bl_idname, text="Edit", icon="TEXT")
    button.tree = tree.name
    button.parm = parm.name


class HOUDINI_BRIDGE_OT_edit_code(modifiers.ModifierOperator, bpy.types.Operator):
    """Edit this code in Blender's Text Editor, in a window of its own: the modifier cooks it as you type"""

    bl_idname = "houdini_bridge.edit_code"
    bl_label = "Edit Code"
    bl_options = {"UNDO"}  # the step holds the new text, which undoing typing steps needs

    parm: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        tree = self.node_group()
        if tree is None:
            return {"CANCELLED"}
        if is_open(tree.name, self.parm):
            return {"CANCELLED"}  # already open: no second text or window, and no undo step
        if open_editor(context, tree, self.parm) is None:
            self.report({"WARNING"}, "%s is no code parameter" % self.parm)
            return {"CANCELLED"}
        return {"FINISHED"}


def register():
    bpy.utils.register_class(HOUDINI_BRIDGE_OT_edit_code)
    bpy.app.handlers.save_pre.append(_on_save_pre)
    bpy.app.handlers.load_pre.append(_on_load_pre)
    bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if isinstance(bpy.data, bpy.types.BlendData):  # not restricted when an extension is disabled
        flush()
        close_all()
    _mirrors.clear()
    for handlers, handler in ((bpy.app.handlers.save_pre, _on_save_pre), (bpy.app.handlers.load_pre, _on_load_pre),
                              (bpy.app.handlers.load_post, _on_load_post)):
        if handler in handlers:
            handlers.remove(handler)
    bpy.utils.unregister_class(HOUDINI_BRIDGE_OT_edit_code)
