"""CookState, what the cook engine believes Houdini has: recording, forgetting, resetting."""

import unittest

from tests import support

state = support.source("cook_state")

TARGET, OTHER = 11, 22


def recorded(cook_state, uid=TARGET, cooked=True):
    """Record one cook of input 0 (mesh "a") with the modifiers ["Subdivision"] above."""
    sent = state.Sent({0: ("a", "topology", "content")}, ["Subdivision"],
                      {"height": 1.0} if cooked else None, (1.0, 24.0) if cooked else None)
    cook_state.record(uid, sent)
    return cook_state


class Record(unittest.TestCase):
    def test_a_cook_records_inputs_stack_parameters_and_frame(self):
        cook_state = recorded(state.CookState())
        self.assertEqual(cook_state.inputs(TARGET), {0: ("a", "topology", "content")})
        self.assertEqual(cook_state.stack(TARGET), ["Subdivision"])
        self.assertEqual(cook_state.parms(TARGET), {"height": 1.0})
        self.assertEqual(cook_state.frame(TARGET), (1.0, 24.0))

    def test_a_skipped_cook_records_inputs_and_stack_only(self):
        cook_state = recorded(state.CookState(), cooked=False)
        self.assertEqual(cook_state.stack(TARGET), ["Subdivision"])
        self.assertIsNone(cook_state.parms(TARGET))
        self.assertIsNone(cook_state.frame(TARGET))

    def test_recording_uses_up_the_input_marks(self):
        cook_state = state.CookState()
        cook_state.mark_dirty(TARGET, 0)
        cook_state.mark_dirty(OTHER, 1)
        recorded(cook_state)
        self.assertEqual(cook_state.dirty(TARGET), set())
        self.assertEqual(cook_state.dirty(OTHER), {1})

    def test_readings_are_copies(self):
        cook_state = recorded(state.CookState())
        cook_state.inputs(TARGET)[1] = "changed"
        cook_state.dirty(TARGET).add(3)
        self.assertEqual(set(cook_state.inputs(TARGET)), {0})
        self.assertEqual(cook_state.dirty(TARGET), set())

    def test_nothing_before_the_first_cook(self):
        cook_state = state.CookState()
        self.assertEqual(cook_state.inputs(TARGET), {})
        for reading in (cook_state.parms, cook_state.frame, cook_state.stack, cook_state.cook_count):
            self.assertIsNone(reading(TARGET))


class Counts(unittest.TestCase):
    def test_count_and_drop(self):
        cook_state = state.CookState()
        cook_state.count_result(TARGET)
        cook_state.count_result(TARGET)
        self.assertEqual(cook_state.cook_count(TARGET), 2)
        cook_state.drop_count(TARGET)
        self.assertIsNone(cook_state.cook_count(TARGET))
        self.assertIsNone(cook_state.stamp(TARGET))

    def test_a_stamp_is_never_given_twice(self):
        cook_state = state.CookState()
        seen = []
        for _ in range(2):  # the count starts again at 1 after a drop and after a reset
            cook_state.count_result(TARGET)
            seen.append(cook_state.stamp(TARGET))
            cook_state.drop_count(TARGET)
            cook_state.count_result(TARGET)
            seen.append(cook_state.stamp(TARGET))
            cook_state.reset()
        self.assertIsNone(cook_state.stamp(TARGET))
        other = state.CookState()
        other.count_result(TARGET)
        seen.append(other.stamp(TARGET))  # another session's
        self.assertEqual(len(set(seen)), len(seen))

    def test_released_once(self):
        cook_state = state.CookState()
        cook_state.release(TARGET)
        self.assertTrue(cook_state.take_released(TARGET))
        self.assertFalse(cook_state.take_released(TARGET))
        self.assertFalse(cook_state.take_released(OTHER))


class Forget(unittest.TestCase):
    def test_forget_one_target_keeps_its_count_and_marks(self):
        cook_state = recorded(recorded(state.CookState()), OTHER)
        cook_state.count_result(TARGET)
        cook_state.mark_dirty(TARGET, 2)
        cook_state.forget(TARGET)
        self.assertEqual(cook_state.inputs(TARGET), {})
        for reading in (cook_state.parms, cook_state.frame, cook_state.stack):
            self.assertIsNone(reading(TARGET))
        self.assertEqual(cook_state.cook_count(TARGET), 1)
        self.assertEqual(cook_state.dirty(TARGET), {2})
        self.assertEqual(cook_state.parms(OTHER), {"height": 1.0})

    def test_a_failed_cook_forgets_the_inputs_and_keeps_the_values(self):
        cook_state = recorded(state.CookState())
        cook_state.forget_inputs(TARGET)
        self.assertEqual(cook_state.inputs(TARGET), {})
        self.assertEqual(cook_state.parms(TARGET), {"height": 1.0})

    def test_reset_forgets_every_target_and_count_but_not_marks_or_releases(self):
        cook_state = recorded(recorded(state.CookState()), OTHER)
        cook_state.count_result(TARGET)
        cook_state.mark_dirty(TARGET, 0)
        cook_state.release(OTHER)
        cook_state.reset()
        for uid in (TARGET, OTHER):
            self.assertEqual(cook_state.inputs(uid), {})
            self.assertIsNone(cook_state.parms(uid))
            self.assertIsNone(cook_state.stack(uid))
            self.assertIsNone(cook_state.cook_count(uid))
        self.assertEqual(cook_state.dirty(TARGET), {0})
        self.assertTrue(cook_state.take_released(OTHER))

    def test_clear_leaves_it_as_new(self):
        cook_state = recorded(state.CookState())
        cook_state.mark_dirty(TARGET, 0)
        cook_state.release(TARGET)
        cook_state.clear_marks()
        self.assertEqual(cook_state.dirty(TARGET), set())
        cook_state.mark_dirty(TARGET, 0)
        cook_state.clear()
        self.assertEqual(cook_state.dirty(TARGET), set())
        self.assertFalse(cook_state.take_released(TARGET))
        self.assertIsNone(cook_state.parms(TARGET))


class Peek(unittest.TestCase):
    """A peek (cook.peek_request) builds a request as a cook does, from the readings, and
    does not call record(): the state it leaves is the state it found."""

    def test_building_from_the_readings_marks_nothing(self):
        cook_state = recorded(state.CookState())
        cook_state.mark_dirty(TARGET, 0)
        before = (cook_state.inputs(TARGET), cook_state.stack(TARGET), cook_state.parms(TARGET),
                  cook_state.frame(TARGET), cook_state.dirty(TARGET))
        inputs = cook_state.inputs(TARGET)  # what request.py changes while it reads the inputs
        inputs[0] = ("a", "topology", "new content")
        inputs.pop(0)
        inputs[1] = ("chain", OTHER, cook_state.cook_count(OTHER))
        cook_state.dirty(TARGET).clear()
        state.Sent(inputs, ["Mirror"], {"height": 2.0}, (2.0, 24.0))  # built, not recorded
        after = (cook_state.inputs(TARGET), cook_state.stack(TARGET), cook_state.parms(TARGET),
                 cook_state.frame(TARGET), cook_state.dirty(TARGET))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
