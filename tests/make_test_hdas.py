"""Build the RG Houdini Bridge test HDAs (run with hython).

    HOUDINI_AUTHOR=bridge@localhost hython tests/make_test_hdas.py

    $env:HOUDINI_AUTHOR = "bridge@localhost"; hython tests/make_test_hdas.py    # PowerShell

HOUDINI_AUTHOR replaces the login and machine names that Houdini stores in every
HDA. It is only read when Houdini starts, so it cannot be set from this script.

Writes tests/hda/bridge_test.hda with these definitions:
- bridge::rock: deforms the input mesh (floats, int, menu, toggle, color,
  folder, normals, two materials);
- bridge::rings: generator without input, packed tori copied onto points
  (checks unpacking and UVs);
- bridge::groups: reads a group of its input, writes groups and attributes;
- bridge::animated: generator reading the time ($T);
- bridge::scatter: packed shapes and quadric spheres on the input (instances);
- bridge::grass: open curves on the input, with Cd and width;
- bridge::fireflies: loose points rising from the input (points, animated);
- bridge::fog: a fog VDB filling the input (volume);
- bridge::shatter: Voronoi pieces packed as fragments (instances sharing one geometry);
- bridge::ramps: a float and a color ramp, a multiparm (another one nested in
  each instance) and nested folders.
"""

import os
import sys

import hou

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hda")
HDA_FILE = os.path.join(FOLDER, "bridge_test.hda")
AUTHOR = "bridge@localhost"


def create_asset(subnet, name, description, max_inputs):
    asset = subnet.createDigitalAsset(
        name=name,
        hda_file_name=HDA_FILE,
        description=description,
        min_num_inputs=0,
        max_num_inputs=max_inputs,
        ignore_external_references=True,
    )
    return asset, asset.type().definition()


def build_rock(geo):
    subnet = geo.createNode("subnet", "rock")
    source = subnet.indirectInputs()[0]

    subdivide = subnet.createNode("subdivide")
    subdivide.setInput(0, source)
    subdivide.parm("iterations").setExpression('ch("../subdivisions")')

    mountain = subnet.createNode("mountain::2.0")
    mountain.setInput(0, subdivide)
    mountain.parm("height").setExpression('ch("../height")')
    mountain.parm("elementsize").setExpression('ch("../noise_size")')

    inflate = subnet.createNode("peak")
    inflate.setInput(0, subdivide)
    inflate.parm("dist").setExpression('ch("../height")')

    effect = subnet.createNode("switch", "effect")
    effect.setInput(0, mountain)
    effect.setInput(1, inflate)
    effect.parm("input").setExpression('ch("../effect")')

    color = subnet.createNode("color")
    color.setInput(0, effect)
    for component in "rgb":
        color.parm("color" + component).setExpression('ch("../color%s")' % component)

    materials = subnet.createNode("attribwrangle", "materials")
    materials.setInput(0, color)
    materials.parm("class").set(1)
    materials.parm("snippet").set(
        's@shop_materialpath = @primnum % 2 == 0 ? "/mat/rock_light" : "/mat/rock_dark";'
    )

    normals = subnet.createNode("normal")
    normals.setInput(0, materials)
    with_normals = subnet.createNode("switch", "with_normals")
    with_normals.setInput(0, materials)
    with_normals.setInput(1, normals)
    with_normals.parm("input").setExpression('ch("../smooth_normals")')

    output = subnet.createNode("output")
    output.setInput(0, with_normals)
    output.setDisplayFlag(True)

    asset, definition = create_asset(subnet, "bridge::rock", "Bridge Rock", 1)
    group = definition.parmTemplateGroup()
    group.append(hou.FloatParmTemplate("height", "Height", 1, default_value=(0.3,), min=0.0, max=2.0))
    group.append(hou.FloatParmTemplate("noise_size", "Noise Size", 1, default_value=(0.5,), min=0.01, max=5.0))
    group.append(hou.IntParmTemplate("subdivisions", "Subdivisions", 1, default_value=(1,), min=0, max=4))
    group.append(hou.MenuParmTemplate("effect", "Effect", ("mountain", "inflate"), ("Mountain", "Inflate")))
    look = hou.FolderParmTemplate("look", "Look")
    look.addParmTemplate(hou.FloatParmTemplate(
        "color", "Color", 3, default_value=(0.6, 0.5, 0.4), look=hou.parmLook.ColorSquare,
        naming_scheme=hou.parmNamingScheme.RGBA,
    ))
    look.addParmTemplate(hou.ToggleParmTemplate("smooth_normals", "Smooth Normals", default_value=True))
    group.append(look)
    definition.setParmTemplateGroup(group)
    definition.updateFromNode(asset)
    definition.save(HDA_FILE, asset)


def build_rings(geo):
    subnet = geo.createNode("subnet", "rings")

    ground = subnet.createNode("grid")
    ground.parm("sizex").setExpression('ch("../extent")')
    ground.parm("sizey").setExpression('ch("../extent")')

    points = subnet.createNode("scatter::2.0")
    points.setInput(0, ground)
    points.parm("npts").setExpression('ch("../count")')
    points.parm("seed").setExpression('ch("../seed")')

    torus = subnet.createNode("torus")
    torus.parm("type").set("poly")
    torus.parm("radx").setExpression('ch("../radius")')
    torus.parm("rady").setExpression('ch("../radius") * 0.3')
    try:
        uv = subnet.createNode("texture")
        uv.setInput(0, torus)
        source = uv
    except hou.OperationFailed:
        source = torus

    copy = subnet.createNode("copytopoints::2.0")
    copy.setInput(0, source)
    copy.setInput(1, points)
    copy.parm("pack").set(True)

    output = subnet.createNode("output")
    output.setInput(0, copy)
    output.setDisplayFlag(True)

    asset, definition = create_asset(subnet, "bridge::rings", "Bridge Rings", 0)
    group = definition.parmTemplateGroup()
    group.append(hou.IntParmTemplate("count", "Count", 1, default_value=(12,), min=1, max=500))
    group.append(hou.FloatParmTemplate("radius", "Radius", 1, default_value=(0.3,), min=0.01, max=2.0))
    group.append(hou.FloatParmTemplate("extent", "Extent", 1, default_value=(4.0,), min=0.1, max=20.0))
    group.append(hou.IntParmTemplate("seed", "Seed", 1, default_value=(1,), min=0, max=100))
    definition.setParmTemplateGroup(group)
    definition.updateFromNode(asset)
    definition.save(HDA_FILE, asset)


VEX_POINTS = """\
f@height = @P.y;
i@id = @ptnum;
if (@P.y > 0.05)
    setpointgroup(0, "high", @ptnum, 1);
"""

VEX_PRIMITIVES = """\
v@facing = prim_normal(0, @primnum, {0.5, 0.5});
if (@P.x > 0) {
    setprimgroup(0, "right", @primnum, 1);
    // The edges of the right faces form an edge group.
    int points[] = primpoints(0, @primnum);
    for (int i = 0; i < len(points); i++)
        setedgegroup(0, "right_edges", points[i], points[(i + 1) % len(points)], 1);
}
"""


def build_groups(geo):
    """Reads a primitive group of its input, writes point, primitive and edge groups
    and float, int and vector attributes."""
    subnet = geo.createNode("subnet", "groups")
    source = subnet.indirectInputs()[0]

    peak = subnet.createNode("peak")
    peak.setInput(0, source)
    peak.parm("group").setExpression('chs("../group")', language=hou.exprLanguage.Hscript)
    peak.parm("dist").setExpression('ch("../distance")')

    points = subnet.createNode("attribwrangle", "point_results")
    points.setInput(0, peak)
    points.parm("snippet").set(VEX_POINTS)

    primitives = subnet.createNode("attribwrangle", "primitive_results")
    primitives.setInput(0, points)
    primitives.parm("class").set(1)
    primitives.parm("snippet").set(VEX_PRIMITIVES)

    output = subnet.createNode("output")
    output.setInput(0, primitives)
    output.setDisplayFlag(True)

    asset, definition = create_asset(subnet, "bridge::groups", "Bridge Groups", 1)
    group = definition.parmTemplateGroup()
    group.append(hou.StringParmTemplate(
        "group", "Group", 1, default_value=("",),
        menu_type=hou.menuType.StringToggle,
        item_generator_script="kwargs['node'].generateInputGroupMenu(0, hou.geometryType.Primitives)",
        item_generator_script_language=hou.scriptLanguage.Python,
    ))
    group.append(hou.FloatParmTemplate("distance", "Distance", 1, default_value=(0.5,), min=0.0, max=2.0))
    definition.setParmTemplateGroup(group)
    definition.updateFromNode(asset)
    definition.save(HDA_FILE, asset)


def build_animated(geo):
    """Generator that reads the time: a grid whose mountain noise slides with $T."""
    subnet = geo.createNode("subnet", "animated")

    ground = subnet.createNode("grid")
    ground.parm("rows").set(20)
    ground.parm("cols").set(20)

    mountain = subnet.createNode("mountain::2.0")
    mountain.setInput(0, ground)
    mountain.parm("height").set(0.5)
    mountain.parm("offsetx").setExpression('$T * ch("../speed")')

    output = subnet.createNode("output")
    output.setInput(0, mountain)
    output.setDisplayFlag(True)

    asset, definition = create_asset(subnet, "bridge::animated", "Bridge Animated", 0)
    group = definition.parmTemplateGroup()
    group.append(hou.FloatParmTemplate("speed", "Speed", 1, default_value=(1.0,), min=0.0, max=10.0))
    definition.setParmTemplateGroup(group)
    definition.updateFromNode(asset)
    definition.save(HDA_FILE, asset)


def save_asset(subnet, name, description, max_inputs, templates):
    asset, definition = create_asset(subnet, name, description, max_inputs)
    group = definition.parmTemplateGroup()
    for template in templates:
        group.append(template)
    definition.setParmTemplateGroup(group)
    definition.updateFromNode(asset)
    definition.save(HDA_FILE, asset)


def surface_points(subnet, count_parm, seed_parm):
    """Points scattered on the input, with its normals."""
    normals = subnet.createNode("normal")
    normals.setInput(0, subnet.indirectInputs()[0])
    scatter = subnet.createNode("scatter::2.0")
    scatter.setInput(0, normals)
    scatter.parm("npts").setExpression('ch("../%s")' % count_parm)
    scatter.parm("seed").setExpression('ch("../%s")' % seed_parm)
    scatter.parm("relaxpoints").set(False)
    return scatter


def material(subnet, source, snippet, run_over=1):
    """shop_materialpath set by VEX (primitives by default): Blender takes the
    material of that name."""
    node = subnet.createNode("attribwrangle", "material")
    node.setInput(0, source)
    node.parm("class").set(run_over)
    node.parm("snippet").set(snippet)
    return node


def timed_wrangle(subnet, source, name, snippet, toggle):
    """A wrangle that reads the time only when the `toggle` parameter is on: a
    node reading @Time makes the whole HDA time dependent (it recooks at every
    frame), even when the animation amount is zero."""
    still = subnet.createNode("attribwrangle", name + "_still")
    still.setInput(0, source)
    still.parm("snippet").set(snippet.replace("@Time", "0.0"))
    moving = subnet.createNode("attribwrangle", name + "_animated")
    moving.setInput(0, source)
    moving.parm("snippet").set(snippet)
    switch = subnet.createNode("switch", name)
    switch.setInput(0, still)
    switch.setInput(1, moving)
    switch.parm("input").setExpression('ch("../%s")' % toggle)
    return switch


VEX_SCATTER = """\
float size = ch("../size") * fit01(rand(@ptnum + 11), 1 - ch("../size_variation"), 1);
f@pscale = size;
vector up = normalize(v@N);
float turn = rand(@ptnum + 3) * 2 * PI + @Time * ch("../spin");
p@orient = qmultiply(dihedral({0, 1, 0}, up), quaternion(turn, {0, 1, 0}));
v@scale = set(1, fit01(rand(@ptnum + 9), 0.6, 1.6), 1);
i@variant = int(rand(@ptnum + 7) * (chi("../spheres") ? 3 : 2));
float mixAmount = rand(@ptnum + 5);
v@Cd = lerp(chv("../color_a"), chv("../color_b"), mixAmount);
"""


def build_scatter(geo):
    """Packed shapes (a rock, a box, quadric spheres) copied onto the input surface:
    instances with a color each, the case of Copy to Points with Pack and Instance."""
    subnet = geo.createNode("subnet", "scatter")
    points = timed_wrangle(subnet, surface_points(subnet, "count", "seed"), "placement", VEX_SCATTER, "animate")

    rock = subnet.createNode("sphere")
    rock.parm("type").set("poly")
    rock.parm("freq").set(3)
    bumps = subnet.createNode("mountain::2.0")
    bumps.setInput(0, rock)
    bumps.parm("height").set(0.4)
    box = subnet.createNode("box")
    box.parm("scale").set(0.8)
    ball = subnet.createNode("sphere")  # a quadric: one primitive per copy
    shapes = []
    for variant, shape in enumerate((bumps, box, ball)):
        chosen = subnet.createNode("blast")
        chosen.setInput(0, points)
        chosen.parm("group").set("@variant==%d" % variant)
        chosen.parm("grouptype").set(3)
        chosen.parm("negate").set(True)
        copy = subnet.createNode("copytopoints::2.0")
        # Inside the rock and the box; on each sphere primitive (quadrics are not packed).
        copy.setInput(0, material(subnet, shape, 's@shop_materialpath = "/mat/bridge_instance";'))
        copy.setInput(1, chosen)
        copy.parm("pack").set(variant != 2)
        copy.parm("targetattribs").set(1)
        copy.parm("applyattribs1").set("Cd")
        shapes.append(copy)
    merge = subnet.createNode("merge")
    for index, copy in enumerate(shapes):
        merge.setInput(index, copy)
    output = subnet.createNode("output")
    output.setInput(0, merge)
    output.setDisplayFlag(True)
    save_asset(subnet, "bridge::scatter", "Bridge Scatter", 1, [
        # Strict minimums: no negative or zero count, no negative size (the slider maximum stays free).
        hou.IntParmTemplate("count", "Count", 1, default_value=(500,), min=1, max=100000, min_is_strict=True),
        hou.IntParmTemplate("seed", "Seed", 1, default_value=(1,), min=0, max=100),
        hou.FloatParmTemplate("size", "Size", 1, default_value=(0.1,), min=0.001, max=2.0, min_is_strict=True),
        hou.FloatParmTemplate("size_variation", "Size Variation", 1, default_value=(0.5,), min=0.0, max=1.0),
        hou.FloatParmTemplate("color_a", "Color A", 3, default_value=(0.8, 0.3, 0.1), look=hou.parmLook.ColorSquare,
                              naming_scheme=hou.parmNamingScheme.RGBA),
        hou.FloatParmTemplate("color_b", "Color B", 3, default_value=(0.2, 0.5, 0.9), look=hou.parmLook.ColorSquare,
                              naming_scheme=hou.parmNamingScheme.RGBA),
        hou.ToggleParmTemplate("spheres", "Quadric Spheres", default_value=True),
        hou.ToggleParmTemplate("animate", "Animate", default_value=False),
        hou.FloatParmTemplate("spin", "Spin", 1, default_value=(2.0,), min=0.0, max=10.0),
    ])


VEX_GRASS = """\
int segments = chi("../segments");
float height = ch("../height") * fit01(rand(@ptnum + 1), 0.5, 1.0);
vector up = normalize(v@N);
vector side = normalize(cross(up, set(rand(@ptnum + 2) - 0.5, 0.123, rand(@ptnum + 4) - 0.5)));
float sway = ch("../bend") + ch("../wind") * sin(@Time * 3 + @P.x * 2);
int blade = addprim(0, "polyline");
for (int segment = 0; segment <= segments; segment++) {
    float along = float(segment) / segments;
    vector position = @P + up * along * height + side * sway * along * along * height;
    int point = addpoint(0, position);
    setpointattrib(0, "Cd", point, lerp(chv("../root_color"), chv("../tip_color"), along));
    setpointattrib(0, "width", point, ch("../width") * (1 - along * 0.8));
    addvertex(0, blade, point);
}
setprimattrib(0, "blade", blade, @ptnum);
removepoint(0, @ptnum);
"""


def build_grass(geo):
    """Open curves growing from the input surface: grass blades with a color and a width."""
    subnet = geo.createNode("subnet", "grass")
    blades = timed_wrangle(subnet, surface_points(subnet, "count", "seed"), "blades", VEX_GRASS, "animate")
    output = subnet.createNode("output")
    output.setInput(0, material(subnet, blades, 's@shop_materialpath = "/mat/bridge_grass";'))
    output.setDisplayFlag(True)
    save_asset(subnet, "bridge::grass", "Bridge Grass", 1, [
        hou.IntParmTemplate("count", "Count", 1, default_value=(2000,), min=1, max=200000),
        hou.IntParmTemplate("seed", "Seed", 1, default_value=(1,), min=0, max=100),
        hou.FloatParmTemplate("height", "Height", 1, default_value=(0.3,), min=0.01, max=5.0),
        hou.IntParmTemplate("segments", "Segments", 1, default_value=(5,), min=1, max=20),
        hou.FloatParmTemplate("bend", "Bend", 1, default_value=(0.3,), min=0.0, max=2.0),
        hou.ToggleParmTemplate("animate", "Wind", default_value=False),
        hou.FloatParmTemplate("wind", "Wind Strength", 1, default_value=(0.2,), min=0.0, max=1.0),
        hou.FloatParmTemplate("width", "Width", 1, default_value=(0.01,), min=0.0001, max=0.2),
        hou.FloatParmTemplate("root_color", "Root Color", 3, default_value=(0.05, 0.2, 0.02),
                              look=hou.parmLook.ColorSquare, naming_scheme=hou.parmNamingScheme.RGBA),
        hou.FloatParmTemplate("tip_color", "Tip Color", 3, default_value=(0.5, 0.8, 0.2),
                              look=hou.parmLook.ColorSquare, naming_scheme=hou.parmNamingScheme.RGBA),
    ])


VEX_FIREFLIES = """\
float cycle = frac(rand(@ptnum + 1) + @Time * ch("../speed"));
@P += normalize(v@N) * cycle * ch("../height");
@P.x += sin(@Time * 2 + @ptnum) * 0.05;
f@pscale = ch("../size") * sin(cycle * PI);
v@Cd = chv("../color") * (0.5 + rand(@ptnum + 2));
f@age = cycle;
"""


def build_fireflies(geo):
    """Loose points rising from the input surface, animated by the time."""
    subnet = geo.createNode("subnet", "fireflies")
    points = subnet.createNode("attribwrangle", "rise")
    points.setInput(0, surface_points(subnet, "count", "seed"))
    points.parm("snippet").set(VEX_FIREFLIES)
    clean = subnet.createNode("attribdelete")
    clean.setInput(0, points)
    clean.parm("ptdel").set("N")
    output = subnet.createNode("output")
    output.setInput(0, material(subnet, clean, 's@shop_materialpath = "/mat/bridge_fireflies";', run_over=2))
    output.setDisplayFlag(True)
    save_asset(subnet, "bridge::fireflies", "Bridge Fireflies", 1, [
        hou.IntParmTemplate("count", "Count", 1, default_value=(3000,), min=1, max=1000000),
        hou.IntParmTemplate("seed", "Seed", 1, default_value=(1,), min=0, max=100),
        hou.FloatParmTemplate("height", "Height", 1, default_value=(1.0,), min=0.0, max=10.0),
        hou.FloatParmTemplate("speed", "Speed", 1, default_value=(0.3,), min=0.0, max=5.0),
        hou.FloatParmTemplate("size", "Size", 1, default_value=(0.015,), min=0.001, max=0.5),
        hou.FloatParmTemplate("color", "Color", 3, default_value=(1.0, 0.7, 0.2), look=hou.parmLook.ColorSquare,
                              naming_scheme=hou.parmNamingScheme.RGBA),
    ])


VEX_FOG = """\
float shape = noise(@P * ch("../noise_frequency"));
@density *= ch("../density") * fit(shape, 0.35, 0.65, 0, 1);
"""


def build_fog(geo):
    """A fog volume (VDB) filling the input, broken up by noise."""
    subnet = geo.createNode("subnet", "fog")
    fog = subnet.createNode("vdbfrompolygons")
    fog.setInput(0, subnet.indirectInputs()[0])
    fog.parm("voxelsize").setExpression('ch("../voxel_size")')
    fog.parm("builddistance").set(False)
    fog.parm("buildfog").set(True)
    fog.parm("fillinterior").set(True)
    noise = subnet.createNode("volumewrangle")
    noise.setInput(0, fog)
    noise.parm("snippet").set(VEX_FOG)
    output = subnet.createNode("output")
    output.setInput(0, noise)
    output.setDisplayFlag(True)
    save_asset(subnet, "bridge::fog", "Bridge Fog", 1, [
        hou.FloatParmTemplate("voxel_size", "Voxel Size", 1, default_value=(0.05,), min=0.005, max=1.0),
        hou.FloatParmTemplate("density", "Density", 1, default_value=(4.0,), min=0.0, max=50.0),
        hou.FloatParmTemplate("noise_frequency", "Noise Frequency", 1, default_value=(2.0,), min=0.0, max=20.0),
    ])


# Runs over points: each packed piece is carried by its single point.
VEX_EXPLODE = """\
vector centre = getbbox_center(0);
vector away = @P - centre;
@P += normalize(away) * ch("../explode") * (0.5 + rand(@ptnum));
"""


def build_shatter(geo):
    """The input cut into Voronoi pieces, packed as fragments of one shared
    geometry (they differ by their name), pushed away from the centre."""
    subnet = geo.createNode("subnet", "shatter")
    source = subnet.indirectInputs()[0]
    cells = subnet.createNode("scatter::2.0")
    cells.setInput(0, source)
    cells.parm("npts").setExpression('ch("../pieces")')
    cells.parm("seed").setExpression('ch("../seed")')
    fracture = subnet.createNode("voronoifracture")
    fracture.setInput(0, source)
    fracture.setInput(1, cells)
    assemble = subnet.createNode("assemble")
    # The faces made by the cut get their own material, inside the shared geometry.
    assemble.setInput(0, material(subnet, fracture, 's@shop_materialpath = inprimgroup(0, "inside", @primnum) '
                                                    '? "/mat/bridge_inside" : "/mat/bridge_outside";'))
    assemble.parm("pack_geo").set(True)
    explode = subnet.createNode("attribwrangle", "explode")
    explode.setInput(0, assemble)
    explode.parm("class").set(2)
    explode.parm("snippet").set(VEX_EXPLODE)
    output = subnet.createNode("output")
    output.setInput(0, explode)
    output.setDisplayFlag(True)
    save_asset(subnet, "bridge::shatter", "Bridge Shatter", 1, [
        hou.IntParmTemplate("pieces", "Pieces", 1, default_value=(12,), min=2, max=500),
        hou.IntParmTemplate("seed", "Seed", 1, default_value=(1,), min=0, max=100),
        hou.FloatParmTemplate("explode", "Explode", 1, default_value=(0.2,), min=0.0, max=5.0),
    ])


VEX_RAMPS = """\
// The float ramp shapes a bump across the input (along x), the color ramp
// colors it by height, and each instance of the Bumps multiparm adds a dome.
vector low = getbbox_min(0);
vector high = getbbox_max(0);
float across = fit(@P.x, low.x, high.x, 0, 1);
float lift = chramp("../shape", across) * ch("../height");
for (int bump = 1; bump <= chi("../bumps"); bump++) {
    if (!chi(sprintf("../enable%d", bump)))
        continue;
    vector centre = chv(sprintf("../center%d", bump));
    float radius = ch(sprintf("../radius%d", bump));
    float distanceToCentre = length(set(@P.x, 0, @P.z) - set(centre.x, 0, centre.z));
    lift += ch(sprintf("../bump_height%d", bump)) * max(0, 1 - distanceToCentre / radius);
}
@P.y += lift;
f@lift = lift;
v@Cd = chramp("../tint", fit(lift, 0, ch("../height") + 1, 0, 1));
"""


def build_ramps(geo):
    """Ramps, multiparms (one nested in each instance) and nested folders."""
    subnet = geo.createNode("subnet", "ramps")
    wrangle = subnet.createNode("attribwrangle", "shape")
    wrangle.setInput(0, subnet.indirectInputs()[0])
    wrangle.parm("snippet").set(VEX_RAMPS)
    output = subnet.createNode("output")
    output.setInput(0, wrangle)
    output.setDisplayFlag(True)

    shape = hou.RampParmTemplate("shape", "Shape", hou.rampParmType.Float, default_value=3, tags={
        "rampfloatdefault": "1pos ( 0 ) 1value ( 0 ) 1interp ( linear ) "
                            "2pos ( 0.5 ) 2value ( 1 ) 2interp ( catmull-rom ) "
                            "3pos ( 1 ) 3value ( 0 ) 3interp ( linear )",
    })
    tint = hou.RampParmTemplate("tint", "Tint", hou.rampParmType.Color, default_value=2, tags={
        "rampcolordefault": "1pos ( 0 ) 1c ( 0.1 0.2 0.8 ) 1interp ( linear ) "
                            "2pos ( 1 ) 2c ( 0.9 0.3 0.1 ) 2interp ( linear )",
    })
    bumps = hou.FolderParmTemplate("bumps", "Number of Bumps", folder_type=hou.folderType.MultiparmBlock,
                                   default_value=1)
    bumps.addParmTemplate(hou.ToggleParmTemplate("enable#", "Enable", default_value=True))
    bumps.addParmTemplate(hou.FloatParmTemplate("center#", "Center", 3, default_value=(0.0, 0.0, 0.0)))
    bumps.addParmTemplate(hou.FloatParmTemplate("radius#", "Radius", 1, default_value=(0.5,), min=0.01, max=2.0,
                                                min_is_strict=True))
    bumps.addParmTemplate(hou.FloatParmTemplate("bump_height#", "Bump Height", 1, default_value=(0.5,),
                                                min=0.0, max=2.0))
    # Not used by the network: checks how a multiparm inside an instance is described.
    rings = hou.FolderParmTemplate("rings#", "Number of Rings", folder_type=hou.folderType.MultiparmBlock,
                                   default_value=0)
    rings.addParmTemplate(hou.FloatParmTemplate("ring_size#_#", "Ring Size", 1, default_value=(0.1,)))
    bumps.addParmTemplate(rings)
    look = hou.FolderParmTemplate("look", "Look")
    look.addParmTemplate(tint)
    advanced = hou.FolderParmTemplate("advanced", "Advanced")
    advanced.addParmTemplate(hou.FloatParmTemplate("height", "Height", 1, default_value=(1.0,), min=0.0, max=5.0))
    look.addParmTemplate(advanced)
    save_asset(subnet, "bridge::ramps", "Bridge Ramps", 1, [shape, bumps, look])


def main():
    if os.environ.get("HOUDINI_AUTHOR") != AUTHOR:
        sys.exit("Set HOUDINI_AUTHOR=%s before starting hython (see the top of this file)." % AUTHOR)
    os.makedirs(FOLDER, exist_ok=True)
    if os.path.exists(HDA_FILE):
        os.remove(HDA_FILE)
    geo = hou.node("/obj").createNode("geo")
    build_rock(geo)
    build_rings(geo)
    build_groups(geo)
    build_animated(geo)
    build_scatter(geo)
    build_grass(geo)
    build_fireflies(geo)
    build_fog(geo)
    build_shatter(geo)
    build_ramps(geo)
    for definition in hou.hda.definitionsInFile(HDA_FILE):
        print("written:", definition.nodeTypeName(), "-", definition.description())


if __name__ == "__main__":
    main()
