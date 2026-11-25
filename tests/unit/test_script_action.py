"""Reading the script_action tag of a group field (houdini/script_action.py), without
Houdini. The scripts are the tags Houdini 22.0 and SideFX Labs 22.0 carry, copied as they
are (read in hython on groupcreate, polyextrude::2.0, blast, groupdelete, fuse::2.0 and
the Labs SOPs)."""

import unittest

from tests import support

script_action = support.source("script_action")
contract = support.source("contract")

GROUP_CREATE = ("import soputils\nkwargs['geometrytype'] = kwargs['node'].parmTuple('grouptype')\n"
                "kwargs['inputindex'] = 0\nkwargs['ordered'] = kwargs['node'].parm('ordered').eval()\n"
                "soputils.selectGroupParm(kwargs)")
POLY_EXTRUDE = ("import soputils\nkwargs['geometrytype'] = (hou.geometryType.Primitives,hou.geometryType.Edges,)\n"
                "kwargs['inputindex'] = 0\nsoputils.selectGroupParm(kwargs)")
GROUP_DELETE = ("import soputils\nkwargs['geometrytype'] = kwargs['node'].parmTuple('selectiongrouptype')\n"
                "kwargs['inputindex'] = 0\nsoputils.selectGroupParm(kwargs)")
FUSE_TARGET = ("import soputils\nkwargs['geometrytype'] = (hou.geometryType.Points,)\nkwargs['inputindex'] = -1\n"
               "soputils.selectGroupParm(kwargs)")
LABS_AUTOUV = ("import soputils\nkwargs['geometrytype'] = hou.geometryType.Primitives\nkwargs['inputindex'] = 0\n"
               "soputils.selectGroupParm(kwargs)")
LABS_TRIM = ("import soputils\nkwargs['geometrytype'] = kwargs['node'].node(\"group1\").parmTuple('grouptype')\n"
             "kwargs['inputindex'] = 1\nkwargs['ordered'] = kwargs['node'].node(\"group1\").parm('ordered').eval()\n"
             "soputils.selectGroupParm(kwargs)")
LABS_CLUSTER = ("import soputils\n\nif kwargs['node'].parm(\"clustertype\").evalAsInt() == 0:\n"
                "    kwargs['geometrytype'] = hou.geometryType.Primitives\nelse:\n"
                "    kwargs['geometrytype'] = hou.geometryType.Points\n    \nkwargs['inputindex'] = 0\n"
                "soputils.selectGroupParm(kwargs)")
LABS_POLYWIRE = ("import soputils\nkwargs['geometrytype'] = (hou.geometryType.Points)\nkwargs['inputindex'] = 0\n"
                 "soputils.selectGroupParm(kwargs)")
LABS_ONE_LINE = ("import soputils; kwargs['geometrytype'] = hou.geometryType.Points; kwargs['inputindex'] = 0; "
                 "soputils.selectGroupParm(kwargs)")
LABS_COMMENTED_NODE = ("import soputils\nkwargs['geometrytype'] = hou.geometryType.Primitives\n"
                       "kwargs['inputindex'] = 0\n## I am using an internal node\n"
                       "kwargs['node'] = kwargs['node']#.node(\"SELECTOR\")\nsoputils.selectGroupParm(kwargs)")
LABS_INTERNAL_NODE = ("import soputils\nkwargs['geometrytype'] = hou.geometryType.Points\nkwargs['inputindex'] = 0\n"
                      "kwargs['node'] = kwargs['node'].node(\"SELECTOR\")\nsoputils.selectGroupParm(kwargs)")
TREE_PRUNE = ("import soputils\npath = kwargs['node'].path()+'/manual_prune_grp'\nkwargs['node'] = hou.node(path)\n"
              "kwargs['geometrytype'] = kwargs['node'].parmTuple('grouptype')\nkwargs['inputindex'] = 0\n"
              "soputils.selectGroupParm(kwargs)")
MATERIAL = "import soputils\nsoputils.selectMaterialGroupParm(kwargs)\n"
WRANGLE_SNIPPET = ("import vexpressionmenu\n\nnode = kwargs['node']\nparmname = 'snippet'\n\n"
                   "vexpressionmenu.createSpareParmsFromChCalls(node, parmname)")


class ScriptAction(unittest.TestCase):
    def read(self, script):
        return script_action.read(script)

    def test_a_type_given_by_a_parameter(self):
        found = self.read(GROUP_CREATE)
        self.assertEqual((found.types, found.type_parm, found.input_index), ((), ("", "grouptype"), 0))
        self.assertEqual(self.read(GROUP_DELETE).type_parm, ("", "selectiongrouptype"))

    def test_fixed_types(self):
        found = self.read(POLY_EXTRUDE)
        self.assertEqual(found.types, (contract.GROUP_PRIMITIVES, contract.GROUP_EDGES))
        self.assertIsNone(found.type_parm)
        self.assertEqual(self.read(LABS_AUTOUV).types, (contract.GROUP_PRIMITIVES,))
        self.assertEqual(self.read(LABS_POLYWIRE).types, (contract.GROUP_POINTS,))
        self.assertEqual(self.read(LABS_ONE_LINE).types, (contract.GROUP_POINTS,))

    def test_the_input(self):
        self.assertEqual(self.read(FUSE_TARGET).input_index, -1)  # input 2 when connected, else the first
        self.assertEqual(self.read(LABS_TRIM).input_index, 1)

    def test_a_type_given_by_a_node_inside(self):
        self.assertEqual(self.read(LABS_TRIM).type_parm, ("group1", "grouptype"))

    def test_a_type_chosen_in_an_if_is_unknown(self):
        found = self.read(LABS_CLUSTER)
        self.assertEqual((found.types, found.type_parm, found.input_index), ((), None, 0))

    def test_another_node_selects_nothing_of_the_inputs(self):
        self.assertIsNone(self.read(LABS_INTERNAL_NODE))
        self.assertIsNone(self.read(TREE_PRUNE))
        self.assertEqual(self.read(LABS_COMMENTED_NODE).types, (contract.GROUP_PRIMITIVES,))

    def test_other_actions_select_nothing(self):
        for script in (MATERIAL, WRANGLE_SNIPPET, "", "not python (", None):
            self.assertIsNone(self.read(script), script)

    def test_an_input_beyond_the_inputs_selects_nothing(self):
        self.assertIsNone(self.read(LABS_AUTOUV.replace("= 0", "= %d" % contract.MAX_INPUTS)))


if __name__ == "__main__":
    unittest.main()
