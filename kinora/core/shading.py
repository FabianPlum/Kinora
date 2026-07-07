"""Shared shader-node builders for the value→colour (ColorRamp → emission) materials.

The agent, path and Voronoi colourings all turn a scalar into colour the same way:
feed it into a ColorRamp (the colour scheme, see ``colormaps``) and out through a
shadeless emission shader.  They differ only in how the scalar reaches the ramp
(object colour for agents, a geometry attribute for paths and cells), so the common
tail and the live colour-map swap live here.
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
