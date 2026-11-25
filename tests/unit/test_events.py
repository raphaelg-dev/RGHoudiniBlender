"""blender/core/events.py: the signals the core emits for the optional features."""

import unittest

from tests import support

events = support.source("events")


class SignalTests(unittest.TestCase):
    def test_listeners_are_called_in_the_order_they_connected(self):
        signal = events.Signal("test")
        calls = []
        signal.connect(lambda *arguments: calls.append(("first", arguments)))
        signal.connect(lambda *arguments: calls.append(("second", arguments)))
        self.assertIsNone(signal.emit(1, "two"))
        self.assertEqual(calls, [("first", (1, "two")), ("second", (1, "two"))])

    def test_disconnect(self):
        signal = events.Signal("test")
        calls = []

        def first():
            calls.append("first")

        def second():
            calls.append("second")

        signal.connect(first)
        signal.connect(second)
        signal.disconnect(first)
        signal.emit()
        self.assertEqual(calls, ["second"])
        self.assertEqual(signal.listeners, (second,))

    def test_disconnecting_an_unknown_function_does_nothing(self):
        signal = events.Signal("test")
        signal.disconnect(print)
        signal.connect(len)
        signal.disconnect(print)
        self.assertEqual(signal.listeners, (len,))

    def test_an_exception_goes_up_to_the_emitter(self):
        signal = events.Signal("test")
        calls = []

        def failing(value):
            raise ValueError("listener %s" % value)

        signal.connect(failing)
        signal.connect(calls.append)
        with self.assertRaisesRegex(ValueError, "listener 3"):
            signal.emit(3)
        self.assertEqual(calls, [])  # the listeners after it are not called

    def test_a_listener_may_disconnect_itself_while_called(self):
        signal = events.Signal("test")
        calls = []

        def once():
            calls.append("once")
            signal.disconnect(once)

        signal.connect(once)
        signal.connect(lambda: calls.append("always"))
        signal.emit()
        signal.emit()
        self.assertEqual(calls, ["once", "always", "always"])

    def test_the_five_signals_of_the_core(self):
        names = ("server_started", "result_applied", "modifier_released", "setting_changed", "modifier_applied")
        for name in names:
            signal = getattr(events, name)
            self.assertIsInstance(signal, events.Signal)
            self.assertEqual(signal.name, name)
            self.assertTrue(signal.__doc__.startswith(name + "("), name)


if __name__ == "__main__":
    unittest.main()
