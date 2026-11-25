"""JSON text kept in a string property of the .blend file (no bpy)."""

import json


def parse(text, default):
    """The value a stored JSON text holds, or `default` when the text is empty or not JSON."""
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default
