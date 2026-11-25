"""Wire format: what goes into one end of a socket comes out of the other, unchanged."""

import socket
import threading
import unittest

import numpy as np

from tests import support

protocol = support.source("protocol")


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.near, self.far = socket.socketpair()

    def tearDown(self):
        self.near.close()
        self.far.close()

    def round_trip(self, header, arrays=None):
        # A large message would fill the socket buffer: send from another thread.
        sender = threading.Thread(target=protocol.send, args=(self.near, header, arrays))
        sender.start()
        try:
            return protocol.receive(self.far)
        finally:
            sender.join()

    def test_header_only(self):
        header, arrays = self.round_trip({"command": "ping", "nested": {"list": [1, 2.5, "three"]}})
        self.assertEqual(header, {"command": "ping", "nested": {"list": [1, 2.5, "three"]}})
        self.assertEqual(arrays, {})

    def test_blocks_of_several_dtypes(self):
        sent = {
            "P": np.arange(12, dtype=np.float32),
            "counts": np.array([3, 4, 5], np.int32),
            "ids": np.array([2**40, -1], np.int64),
            "mask": np.array([True, False, True]),
            "values": np.linspace(0, 1, 7),
        }
        header, received = self.round_trip({"command": "cook"}, sent)
        self.assertEqual(header, {"command": "cook"})
        self.assertEqual(list(received), list(sent))
        for name, array in sent.items():
            self.assertEqual(received[name].dtype, array.dtype, name)
            np.testing.assert_array_equal(received[name], array, name)

    def test_empty_block(self):
        _, received = self.round_trip({}, {"empty": np.zeros(0, np.float32), "after": np.array([7], np.int32)})
        self.assertEqual(received["empty"].size, 0)
        self.assertEqual(received["empty"].dtype, np.float32)
        np.testing.assert_array_equal(received["after"], [7])

    def test_large_block(self):
        large = np.random.default_rng(1).random(3_000_000, dtype=np.float32)  # 12 MB
        _, received = self.round_trip({"command": "cook"}, {"P": large})
        np.testing.assert_array_equal(received["P"], large)

    def test_non_contiguous_array(self):
        grid = np.arange(24, dtype=np.float32).reshape(4, 6)[:, ::2]
        _, received = self.round_trip({}, {"grid": grid})
        np.testing.assert_array_equal(received["grid"], grid.ravel())

    def test_received_arrays_are_writable(self):
        _, received = self.round_trip({}, {"P": np.ones(5, np.float32)})
        received["P"][0] = 3.0
        self.assertEqual(received["P"][0], 3.0)

    def test_several_messages_in_a_row(self):
        for index in range(3):
            header, received = self.round_trip({"index": index}, {"a": np.full(index + 1, index, np.int32)})
            self.assertEqual(header["index"], index)
            np.testing.assert_array_equal(received["a"], [index] * (index + 1))

    def test_an_answer_json_cannot_write_sends_nothing(self):
        # The server answers an error instead: nothing of the first answer may be on the wire.
        with self.assertRaises(TypeError):
            protocol.send(self.near, {"value": object()}, {"a": np.zeros(3)})
        header, _ = self.round_trip({"error": "next"})
        self.assertEqual(header, {"error": "next"})

    def test_an_array_of_objects_sends_nothing(self):
        with self.assertRaises(TypeError):  # the server answers an error instead, as above
            protocol.send(self.near, {"ok": True}, {"a": np.zeros(3), "b": np.array([object()])})
        header, _ = self.round_trip({"error": "next"})
        self.assertEqual(header, {"error": "next"})

    def test_closed_connection(self):
        self.near.close()
        with self.assertRaises(ConnectionError):
            protocol.receive(self.far)


if __name__ == "__main__":
    unittest.main()
