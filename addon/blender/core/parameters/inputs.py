"""The items of a modifier's inputs: finding one, copying its value."""


def modifier_input(modifier, identifier):
    """The item of a modifier input (`value`, keyframes), or None when the node group has no
    such input."""
    return getattr(modifier.properties.inputs, identifier, None)


def copy_input(source, destination, identifier):
    """Copy one input value from a modifier to another using the same node group."""
    item = modifier_input(source, identifier)
    other = modifier_input(destination, identifier)
    try:
        if item is not None and other is not None:
            other.value = item.value
    except (TypeError, ValueError, AttributeError):
        # A geometry, matrix, bundle or closure input has no value, and a menu the modifier has
        # not evaluated yet has no item to take one (TypeError): the copy keeps its default.
        pass
