"""The cuts of a modifier stack (blender/core/stack_cuts.py), without Blender: an object and its
modifiers are stand-ins holding what the module reads and writes (the viewport and render
switches, a persistent_uid, the property of the cuts)."""

import unittest

from tests import support

stack_cuts = support.source("stack_cuts")


class Modifier:
    def __init__(self, uid, obj, viewport=True, render=True):
        self.persistent_uid = uid
        self.id_data = obj
        self.show_viewport = viewport
        self.show_render = render
        self.writes = 0  # how many times show_viewport was written

    def __setattr__(self, name, value):
        if name == "show_viewport" and "show_viewport" in self.__dict__:
            self.__dict__["writes"] = self.__dict__.get("writes", 0) + 1
        object.__setattr__(self, name, value)


class Object:
    def __init__(self, count):
        setattr(self, stack_cuts.PROPERTY, "")
        self.modifiers = [Modifier(index + 1, self) for index in range(count)]


def shown(obj):
    return [modifier.show_viewport for modifier in obj.modifiers]


class Cuts(unittest.TestCase):
    def test_below(self):
        obj = Object(4)
        a, b, c, d = obj.modifiers
        self.assertEqual(stack_cuts.below(obj, b), [c, d])
        self.assertEqual(stack_cuts.below(obj, b, including=True), [b, c, d])
        self.assertEqual(stack_cuts.below(obj, d), [])

    def test_hold_and_release_give_the_stack_back(self):
        obj = Object(4)
        a, b, c, d = obj.modifiers
        stack_cuts.hold(obj, "display", [b, c, d], anchor=a)
        self.assertEqual(shown(obj), [True, False, False, False])
        self.assertEqual(stack_cuts.anchor(obj, "display"), a)
        self.assertEqual(stack_cuts.held_by(c), ("display",))
        self.assertEqual(stack_cuts.objects_with([obj, Object(2)], "display"), [obj])
        stack_cuts.release(obj, "display")
        self.assertEqual(shown(obj), [True] * 4)
        self.assertEqual(getattr(obj, stack_cuts.PROPERTY), "", "nothing is left on the object")
        self.assertFalse(stack_cuts.has(obj, "display"))

    def test_a_modifier_the_user_turned_off_stays_off(self):
        obj = Object(3)
        a, b, c = obj.modifiers
        c.show_viewport = False
        stack_cuts.hold(obj, "display", [b, c], anchor=a)
        self.assertEqual(stack_cuts.held_by(c), ())
        stack_cuts.release(obj, "display")
        self.assertEqual(shown(obj), [True, True, False])

    def test_two_cuts_give_back_only_their_own(self):
        obj = Object(4)
        a, b, c, d = obj.modifiers
        stack_cuts.hold(obj, "display", [c, d], anchor=b)
        stack_cuts.hold(obj, "selection", [b, c, d], anchor=b)
        stack_cuts.release(obj, "display")
        self.assertEqual(shown(obj), [True, False, False, False], "the selection still holds them")
        stack_cuts.release(obj, "selection")
        self.assertEqual(shown(obj), [True] * 4)

    def test_what_the_user_changed_is_the_users(self):
        obj = Object(3)
        a, b, c = obj.modifiers
        stack_cuts.hold(obj, "display", [b, c], anchor=a)
        b.show_viewport = True  # shown again by hand
        c.show_render = False  # bypassed: its render switch changed
        stack_cuts.release(obj, "display")
        self.assertEqual((b.show_viewport, c.show_viewport, c.show_render), (True, False, False))

    def test_taken_back_tells_a_change_by_hand(self):
        for change in ("shown", "bypassed"):
            obj = Object(3)
            a, b, c = obj.modifiers
            stack_cuts.hold(obj, "display", [b, c], anchor=a)
            self.assertFalse(stack_cuts.changed_by_user(obj, "display"))
            if change == "shown":
                b.show_viewport = True
            else:
                c.show_render = False
            self.assertTrue(stack_cuts.changed_by_user(obj, "display"), change)
            self.assertFalse(stack_cuts.changed_by_user(obj, "selection"), "no such cut")
        obj = Object(3)
        a, b, c = obj.modifiers
        stack_cuts.hold(obj, "display", [b, c], anchor=a)
        obj.modifiers.remove(c)
        self.assertFalse(stack_cuts.changed_by_user(obj, "display"), "a modifier removed is no change by hand")

    def test_settle_follows_the_stack(self):
        obj = Object(3)
        a, b, c = obj.modifiers
        stack_cuts.hold(obj, "display", [b, c], anchor=a)
        added = Modifier(9, obj)
        obj.modifiers.append(added)
        stack_cuts.settle(obj, "display", [b, c, added])
        self.assertFalse(added.show_viewport, "a modifier added below is cut")
        obj.modifiers[:] = [b, a, c, added]  # b moved above the anchor
        stack_cuts.settle(obj, "display", [c, added])
        self.assertTrue(b.show_viewport, "a modifier moved above is given back")
        obj.modifiers.remove(c)  # removed: forgotten
        stack_cuts.release(obj, "display")
        self.assertEqual([m.show_viewport for m in obj.modifiers], [True, True, True])

    def test_writes_only_what_differs(self):
        obj = Object(3)
        a, b, c = obj.modifiers
        c.show_viewport = False
        writes = c.writes
        stack_cuts.hold(obj, "display", [b, c], anchor=a)
        stack_cuts.settle(obj, "display", [b, c])
        stack_cuts.release(obj, "display")
        self.assertEqual(c.writes, writes, "a modifier already off is never written")
        self.assertEqual(b.writes, 2, "one write to cut it, one to give it back")


if __name__ == "__main__":
    unittest.main()
