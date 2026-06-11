"""Colour each agent from its per-frame ``position_data`` scalar.

In the default playback mode every agent is its own ``Agent_<id>`` object sharing
one mesh and one material.  Colouring drives each object's ``obj.color`` with the
agent's scalar for the current frame; a shared shadeless material reads that value
through an Object-Info node into a ColorRamp (the colour scheme) and out to an
emission shader.  Because the value lives on ``obj.color`` and the colour map lives
in the shader, switching colour scheme never re-reads the file and the per-frame
update only writes one float per visible agent (see ``core.streaming``).

The scalar is clipped to [0, 1] upstream of here (see ``io.hdf5_reader`` and the
project-wide value→colour contract); this module assumes it is already in range.

Big-data (particle) mode shares a single instance object, so per-particle colour
needs a different mechanism and is intentionally not handled here yet — colouring
is a no-op unless the loaded simulation is in default mode.
"""

import bpy

from . import colormaps
from .geometry import AGENT_DEFAULT_RGBA, set_material_color

AGENT_MATERIAL_NAME = "Kinora_Agent_Material"
AGENT_COLOR_RAMP_NODE = "Kinora_Agent_Ramp"


def _build_color_material(colormap):
    """Convert the shared agent material to an Object-Info → ColorRamp emission.

    The agent's ``obj.color`` (set per frame to ``(v, v, v, 1)``) feeds the
    ColorRamp's Fac via the Object Info node; the resulting colour drives a
    shadeless emission so the agents show exact colour-map colours under a
    Standard view transform.  The material is shared across all agents (the agent
    mesh is shared), but Object Info reads each object's own colour, so agents
    colour independently.
    """
    material = bpy.data.materials.get(AGENT_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(AGENT_MATERIAL_NAME)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)

    emit = tree.nodes.new("ShaderNodeEmission")
    emit.location = (200, 0)

    ramp = tree.nodes.new("ShaderNodeValToRGB")  # ColorRamp = value -> colour
    ramp.name = AGENT_COLOR_RAMP_NODE
    ramp.location = (-100, 0)
    colormaps.apply_colormap(ramp.color_ramp, colormap)

    obj_info = tree.nodes.new("ShaderNodeObjectInfo")
    obj_info.location = (-400, 0)

    links = tree.links
    links.new(obj_info.outputs["Color"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], emit.inputs["Color"])
    links.new(emit.outputs["Emission"], output.inputs["Surface"])
    return material


def _restore_flat_material():
    """Revert the shared agent material to its plain flat colour.

    Rebuilds a Principled node tree (Blender 5.x ignores ``use_nodes = False``, so
    the data-colour emission tree is replaced rather than bypassed).
    """
    material = bpy.data.materials.get(AGENT_MATERIAL_NAME)
    if material is None:
        return
    set_material_color(material, AGENT_DEFAULT_RGBA)


def _reset_object_colors(objects):
    """Clear per-agent ``obj.color`` back to neutral white."""
    for obj in objects:
        try:
            obj.color = (1.0, 1.0, 1.0, 1.0)
        except (ReferenceError, AttributeError):
            continue


def refresh(context):
    """Enable or disable agent colouring according to the current properties.

    Safe to call any time.  Colouring is only enabled when the loaded file
    provides per-agent colours, playback is in default mode, and agent objects
    exist; otherwise the shared material is restored to its flat colour.  Does not
    touch viewport shading — callers switch to Material Preview (see the property
    update callbacks and the load finalizer).
    """
    from .streaming import STREAM_STATE, apply_agent_colors_now

    props = context.scene.kinora_props
    objects = STREAM_STATE.get("objects") or []
    available = (
        STREAM_STATE.get("color_frame_data") is not None
        and STREAM_STATE.get("mode") == "default"
        and bool(objects)
    )

    if props.show_agent_colors and available:
        _build_color_material(props.agent_color_colormap)
        STREAM_STATE["agent_color_enabled"] = True
        apply_agent_colors_now(context.scene)
    else:
        STREAM_STATE["agent_color_enabled"] = False
        _restore_flat_material()
        _reset_object_colors(objects)


def update_appearance(context):
    """Update the colour map on the existing agent material, live.

    Companion to :func:`refresh`: changes the ColorRamp only, with no data read,
    so the colour-scheme enum updates immediately.  No-op when agent colouring is
    not currently active.
    """
    material = bpy.data.materials.get(AGENT_MATERIAL_NAME)
    if material is None or not material.use_nodes:
        return
    ramp = material.node_tree.nodes.get(AGENT_COLOR_RAMP_NODE)
    if ramp is not None:
        colormaps.apply_colormap(ramp.color_ramp, context.scene.kinora_props.agent_color_colormap)
