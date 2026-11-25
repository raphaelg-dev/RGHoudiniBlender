"""Houdini group strings of element numbers (blender/features/selection/group_string.py),
without Blender: reading what a field holds, writing a selection compact. The edge strings
are those Houdini 22 writes (hou.Selection.selectionString) and reads back
(hou.Geometry.globEdges), as checked in hython."""

import unittest

from tests import support

group_string = support.source("group_string")
contract = support.source("contract")


class Numbers(unittest.TestCase):
    def test_runs_become_ranges(self):
        self.assertEqual(group_string.write_numbers([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 15, 20, 21, 22]),
                         "0-11 15 20-22")

    def test_unsorted_and_repeated(self):
        self.assertEqual(group_string.write_numbers([9, 3, 4, 3, 10, 11, 0]), "0 3-4 9-11")

    def test_nothing(self):
        self.assertEqual(group_string.write_numbers([]), "")

    def test_houdini_strings(self):
        # Written by hou.Selection.selectionString on a 3 x 4 grid.
        self.assertEqual(group_string.write_numbers([0, 1, 2, 4]), "0-2 4")
        self.assertEqual(group_string.write_numbers([0, 1, 2, 3, 7, 9, 10, 11]), "0-3 7 9-11")

    def test_reading(self):
        self.assertEqual(group_string.read_numbers("0-11 15 20-22"), list(range(12)) + [15, 20, 21, 22])
        self.assertEqual(group_string.read_numbers("  4 2  "), [2, 4])
        self.assertEqual(group_string.read_numbers("3-1"), [1, 2, 3])  # a range may run backwards
        self.assertEqual(group_string.read_numbers("0-6:2"), [0, 2, 4, 6])  # a step, as globPrims reads it
        self.assertEqual(group_string.read_numbers(""), [])

    def test_round_trip(self):
        for numbers in ([0], [5, 6, 7], [1, 3, 5], list(range(100)) + [200]):
            self.assertEqual(group_string.read_numbers(group_string.write_numbers(numbers)), numbers)

    def test_what_is_not_numbers_is_not_read(self):
        for text in ("top", "0-3 top", "@P.y>0", "!3", "*", "0-2 ^1", "p0-1", "1.5", "0-3:0"):
            self.assertIsNone(group_string.read_numbers(text), text)


class Edges(unittest.TestCase):
    def test_paths_of_points(self):
        # hou.Selection.selectionString of the edges 0-1, 1-2, 5-6, 4-0, 9-5, 10-11 of a grid.
        self.assertEqual(group_string.read_edges("p0-1-2 p5-6 p4-0 p9-5 p10-11"),
                         [(0, 1), (0, 4), (1, 2), (5, 6), (5, 9), (10, 11)])

    def test_written_compact(self):
        self.assertEqual(group_string.write_edges([(1, 0), (1, 2), (5, 6), (4, 0), (9, 5), (10, 11)]),
                         "p0-1-2 p0-4 p5-6 p5-9 p10-11")
        self.assertEqual(group_string.write_edges([(0, 1), (1, 2), (2, 3)]), "p0-1-2-3")
        self.assertEqual(group_string.write_edges([(3, 2), (2, 1), (1, 0)]), "p0-1-2-3")
        self.assertEqual(group_string.write_edges([(0, 1), (5, 6)]), "p0-1 p5-6")
        self.assertEqual(group_string.write_edges([(0, 1), (0, 1), (1, 0)]), "p0-1")
        self.assertEqual(group_string.write_edges([]), "")

    def test_every_edge_once(self):
        edges = [(0, 1), (1, 2), (5, 6), (0, 4), (5, 9), (10, 11), (1, 5), (4, 5), (2, 6), (6, 7)]
        text = group_string.write_edges(edges)
        self.assertEqual(group_string.read_edges(text), sorted(edges))
        written = [pair for token in text.split() for pair in zip(token[1:].split("-"), token[1:].split("-")[1:],
                                                                  strict=False)]
        self.assertEqual(len(written), len(edges), text)

    def test_what_is_not_an_edge_path_is_not_read(self):
        for text in ("0-1", "p0", "p0-x", "0e1", "edges", "p0-1 top"):
            self.assertIsNone(group_string.read_edges(text), text)
        self.assertEqual(group_string.read_edges(""), [])


class Types(unittest.TestCase):
    def test_tokens_as_soputils_reads_them(self):
        self.assertEqual(group_string.type_of_token("prims"), contract.GROUP_PRIMITIVES)
        self.assertEqual(group_string.type_of_token("primitive"), contract.GROUP_PRIMITIVES)
        self.assertEqual(group_string.type_of_token("point"), contract.GROUP_POINTS)
        self.assertEqual(group_string.type_of_token("edge"), contract.GROUP_EDGES)
        self.assertEqual(group_string.type_of_token("vertex"), contract.GROUP_VERTICES)
        self.assertIsNone(group_string.type_of_token("guess"))

    def test_the_menu_token_of_a_type(self):
        group_create = ["primitive", "point", "edge", "vertex"]  # Group Create's grouptype
        blast = ["guess", "breakpoints", "edges", "points", "prims"]  # Blast's grouptype
        self.assertEqual(group_string.token_of_type(contract.GROUP_POINTS, group_create), "point")
        self.assertEqual(group_string.token_of_type(contract.GROUP_PRIMITIVES, blast), "prims")
        self.assertEqual(group_string.token_of_type(contract.GROUP_EDGES, blast), "edges")
        self.assertIsNone(group_string.token_of_type(contract.GROUP_VERTICES, blast))

    def test_initial_type_as_soputils_chooses_it(self):
        edges_or_prims = (contract.GROUP_PRIMITIVES, contract.GROUP_EDGES)  # Poly Extrude's Group
        self.assertEqual(group_string.initial_type(edges_or_prims, "", contract.GROUP_EDGES), contract.GROUP_EDGES)
        self.assertEqual(group_string.initial_type(edges_or_prims, "", contract.GROUP_POINTS),
                         contract.GROUP_PRIMITIVES)
        self.assertEqual(group_string.initial_type(edges_or_prims, "0-3", None), contract.GROUP_PRIMITIVES)
        self.assertEqual(group_string.initial_type(edges_or_prims, "p0-1", None), contract.GROUP_EDGES)
        self.assertEqual(group_string.initial_type((contract.GROUP_POINTS,), "", contract.GROUP_EDGES),
                         contract.GROUP_POINTS)
        self.assertEqual(group_string.initial_type((), "", contract.GROUP_EDGES), contract.GROUP_EDGES)

    def test_selection_by_type(self):
        self.assertEqual(group_string.write_selection([(1, 0)], contract.GROUP_EDGES), "p0-1")
        self.assertEqual(group_string.write_selection([3, 4], contract.GROUP_POINTS), "3-4")
        self.assertEqual(group_string.read_selection("p0-1", contract.GROUP_EDGES), [(0, 1)])
        self.assertIsNone(group_string.read_selection("p0-1", contract.GROUP_PRIMITIVES))

    def test_every_mode_has_a_type_and_a_word(self):
        self.assertEqual(set(group_string.TYPE_OF_MODE), set(group_string.ELEMENT_WORDS))
        self.assertLessEqual(set(group_string.MODE_OF_TYPE), set(contract.GROUP_TYPES))


if __name__ == "__main__":
    unittest.main()
