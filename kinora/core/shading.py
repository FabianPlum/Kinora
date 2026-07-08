"""Shared shader-node builders for the value→colour (ColorRamp → emission) materials.

The agent, path and Voronoi colourings all turn a scalar into colour the same way:
feed it into a ColorRamp (the colour scheme, see ``colormaps``) and out through a
shadeless emission shader.  They differ only in how the scalar reaches the ramp
(object colour for agents, a geometry attribute for paths and cells), so the common
tail and the live colour-map swap live here.

The FDS fire & smoke volume (``core.smoke``) is the one Kinora material that
renders as a volume rather than a surface; :func:`build_fire_smoke_material`
builds it around the stock Principled Volume shader: physically-based
Beer-Lambert smoke opacity from the ``density`` grid, procedural sub-grid
detail noise, and blackbody fire emission from the ``temperature`` grid.
"""

import bpy

from . import colormaps


def build_emission_ramp(material, ramp_name, colormap):
    """Reset *material* to ``ColorRamp → Emission → Output`` and return the ramp.

    The ColorRamp (named *ramp_name*, configured for *colormap*) is left with its
    ``Fac`` input unconnected so the caller can drive it from a value source.
    """
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    emit = tree.nodes.new("ShaderNodeEmission")
    emit.location = (200, 0)
    ramp = tree.nodes.new("ShaderNodeValToRGB")  # ColorRamp = value -> colour
    ramp.name = ramp_name
    ramp.location = (-100, 0)
    colormaps.apply_colormap(ramp.color_ramp, colormap)
    tree.links.new(ramp.outputs["Color"], emit.inputs["Color"])
    tree.links.new(emit.outputs["Emission"], output.inputs["Surface"])
    return ramp


def build_attribute_emission(material, attribute_name, ramp_name, colormap):
    """Colour geometry by a named float attribute, shadeless.

    ``Attribute(attribute_name) → ColorRamp(colormap) → Emission``; shared by the
    path ribbons and the Voronoi cells, which store the scalar as a mesh attribute.
    """
    ramp = build_emission_ramp(material, ramp_name, colormap)
    tree = material.node_tree
    attr = tree.nodes.new("ShaderNodeAttribute")
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = attribute_name
    attr.location = (-400, 0)
    tree.links.new(attr.outputs["Fac"], ramp.inputs["Fac"])


# Soot has very low albedo (it absorbs almost all light rather than scattering
# it); real smoke's apparent brightness/colour comes mostly from lighting and
# multiple scattering, not a per-voxel colour scheme - so a fixed dark grey
# stands in for it, no ColorRamp/colour picker needed at all.
SMOKE_ALBEDO = (0.05, 0.05, 0.05, 1.0)
# A touch of forward scattering reads as softer/less flat than pure isotropic
# scattering (Anisotropy 0), closer to how light behaves passing through smoke.
SMOKE_ANISOTROPY = 0.2
# Detail-noise feature size in metres (Noise Texture scale is ~features per
# metre in object space): ~0.4 m wisps suit building-scale FDS domains.
SMOKE_NOISE_SCALE = 2.5


def build_fire_smoke_material(
    material,
    volume_node_name,
    smoke_scale_node_name,
    detail_node_name,
    temp_node_name,
    intensity_node_name,
):
    """Build the combined FDS fire & smoke volume material.

    Returns ``(volume, smoke_scale, detail_range, temp_mult, intensity_mult)``
    nodes; the caller drives the live parameters through them.

    Smoke: the ``density`` grid holds the raw soot mass density (kg/m3, see
    ``io.fds_reader``); the *smoke_scale* Math node multiplies it by the
    user-editable mass extinction coefficient (Beer-Lambert, Smokeview's own
    convention) times the smoke density multiplier - the caller sets that
    product (and 0 to hide the smoke channel entirely). A procedural Noise
    Texture modulates the result by ``1 ± detail_amount`` (the pyro-workflow
    "sub-grid detail" trick): mean-preserving, so overall opacity stays put
    while edges break up into wisps the coarse CFD grid can't carry. Static
    3D noise in object space is enough - the data animates through it.

    Fire: an *explicit* emission branch, deliberately NOT the Principled
    Volume's built-in blackbody (whose emission is coupled to the smoke
    density - a flame buried in optically thick soot would be invisible and
    a thin-smoke region couldn't glow). ``Attribute("temperature")`` (flame,
    normalised 0..1) x flame temperature (K) -> Blackbody node -> Emission
    colour, with Emission strength = flame x the flame density multiplier
    (0 hides the flame channel); joined to the smoke via Add Shader. The
    Blackbody node outputs a normalised colour, so the multiplier is an
    ordinary emission strength (sane values ~1-50).
    """
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (800, 0)

    add = tree.nodes.new("ShaderNodeAddShader")
    add.location = (600, 0)

    volume = tree.nodes.new("ShaderNodeVolumePrincipled")
    volume.name = volume_node_name
    volume.location = (350, 200)
    volume.inputs["Color"].default_value = SMOKE_ALBEDO
    volume.inputs["Anisotropy"].default_value = SMOKE_ANISOTROPY

    # --- smoke density branch ---
    attr = tree.nodes.new("ShaderNodeAttribute")
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = "density"
    attr.location = (-600, 200)

    smoke_scale = tree.nodes.new("ShaderNodeMath")
    smoke_scale.name = smoke_scale_node_name
    smoke_scale.operation = "MULTIPLY"
    smoke_scale.inputs[1].default_value = 1.0
    smoke_scale.location = (-350, 200)

    texcoord = tree.nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-600, 0)

    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "3D"
    noise.inputs["Scale"].default_value = SMOKE_NOISE_SCALE
    noise.inputs["Detail"].default_value = 4.0
    noise.location = (-350, 0)

    # Noise Fac (mean ~0.5) -> modulation factor in [1-d, 1+d], mean ~1.
    detail_range = tree.nodes.new("ShaderNodeMapRange")
    detail_range.name = detail_node_name
    detail_range.clamp = False
    detail_range.inputs["From Min"].default_value = 0.0
    detail_range.inputs["From Max"].default_value = 1.0
    detail_range.inputs["To Min"].default_value = 1.0
    detail_range.inputs["To Max"].default_value = 1.0
    detail_range.location = (-100, 0)

    detail_mult = tree.nodes.new("ShaderNodeMath")
    detail_mult.operation = "MULTIPLY"
    detail_mult.location = (100, 150)

    tree.links.new(attr.outputs["Fac"], smoke_scale.inputs[0])
    tree.links.new(texcoord.outputs["Object"], noise.inputs["Vector"])
    tree.links.new(noise.outputs["Fac"], detail_range.inputs["Value"])
    tree.links.new(smoke_scale.outputs["Value"], detail_mult.inputs[0])
    tree.links.new(detail_range.outputs["Result"], detail_mult.inputs[1])
    tree.links.new(detail_mult.outputs["Value"], volume.inputs["Density"])

    # --- flame emission branch ---
    attr_flame = tree.nodes.new("ShaderNodeAttribute")
    attr_flame.attribute_type = "GEOMETRY"
    attr_flame.attribute_name = "temperature"
    attr_flame.location = (-600, -300)

    temp_mult = tree.nodes.new("ShaderNodeMath")
    temp_mult.name = temp_node_name
    temp_mult.operation = "MULTIPLY"
    temp_mult.inputs[1].default_value = 4200.0
    temp_mult.location = (-350, -250)

    blackbody = tree.nodes.new("ShaderNodeBlackbody")
    blackbody.location = (-100, -250)

    intensity_mult = tree.nodes.new("ShaderNodeMath")
    intensity_mult.name = intensity_node_name
    intensity_mult.operation = "MULTIPLY"
    intensity_mult.inputs[1].default_value = 0.0
    intensity_mult.location = (-100, -400)

    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (350, -300)

    tree.links.new(attr_flame.outputs["Fac"], temp_mult.inputs[0])
    tree.links.new(temp_mult.outputs["Value"], blackbody.inputs["Temperature"])
    tree.links.new(blackbody.outputs["Color"], emission.inputs["Color"])
    tree.links.new(attr_flame.outputs["Fac"], intensity_mult.inputs[0])
    tree.links.new(intensity_mult.outputs["Value"], emission.inputs["Strength"])

    tree.links.new(volume.outputs["Volume"], add.inputs[0])
    tree.links.new(emission.outputs["Emission"], add.inputs[1])
    tree.links.new(add.outputs["Shader"], output.inputs["Volume"])
    return volume, smoke_scale, detail_range, temp_mult, intensity_mult


def set_detail_amount(detail_range, amount):
    """Set the detail-noise modulation strength on the node returned by the builder.

    *amount* 0 disables the modulation (factor pinned to 1); 1 lets noise swing
    density between 0 and 2x. Mean-preserving around 1 for any amount.
    """
    detail_range.inputs["To Min"].default_value = 1.0 - amount
    detail_range.inputs["To Max"].default_value = 1.0 + amount


def update_ramp(material_name, ramp_name, colormap):
    """Re-apply *colormap* to the named ColorRamp on a material, if present.

    No-op when the material or ramp node does not exist (colouring not active), so a
    colour-scheme change is a cheap live update with no data read or mesh rebuild.
    """
    material = bpy.data.materials.get(material_name)
    if material is None or not material.use_nodes:
        return
    ramp = material.node_tree.nodes.get(ramp_name)
    if ramp is not None:
        colormaps.apply_colormap(ramp.color_ramp, colormap)
