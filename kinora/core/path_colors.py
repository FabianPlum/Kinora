"""Colour agent-path ribbons by their per-point ``position_data`` scalar.

Paths are flat ribbon meshes (see ``core.geometry.create_agent_path``) carrying a
per-vertex ``PATH_VALUE_ATTR`` float.  When colouring is on, the shared
``Kinora_Path_Material`` reads that attribute into a ColorRamp (the colour scheme,
shared with the agents) and out to a shadeless emission, so each segment shows the
data value at that point; when off, the material is a flat Principled colour.

Unlike the agents this needs no per-frame work — the whole path is coloured at
once from baked vertex data, so there is no frame handler here.  The scalar is
clipped to [0, 1] upstream (the project-wide value→colour contract).
"""

import bpy

from . import shading
from .geometry import PATH_DEFAULT_RGBA, PATH_VALUE_ATTR, set_material_color

PATH_MATERIAL_NAME = "Kinora_Path_Material"
PATH_RAMP_NODE = "Kinora_Path_Ramp"
AGENTS_COLLECTION = "Kinora_Agents"


def _paths_have_values() -> bool:
    """True if loaded path ribbons carry the per-vertex colour attribute."""
    collection = bpy.data.collections.get(AGENTS_COLLECTION)
    if collection is None:
        return False
    for obj in collection.objects:
        if obj.name.startswith("Path_Agent_") and obj.type == "MESH":
            return PATH_VALUE_ATTR in obj.data.attributes
    return False


def refresh(context):
    """Enable or disable path colouring according to the current properties.

    Safe to call any time.  Enabled only when paths are loaded with per-point
    colour data and the toggle is on; otherwise the shared path material is
    restored to its flat colour.  Viewport shading is handled by the caller.
    """
    material = bpy.data.materials.get(PATH_MATERIAL_NAME)
    if material is None:
        return  # no paths loaded
    props = context.scene.kinora_props
    if props.show_path_colors and _paths_have_values():
        # Attribute(PATH_VALUE_ATTR) -> ColorRamp -> emission; colours each segment
        # from the baked per-vertex scalar without touching the mesh.
        shading.build_attribute_emission(
            material, PATH_VALUE_ATTR, PATH_RAMP_NODE, props.agent_color_colormap
        )
    else:
        set_material_color(material, PATH_DEFAULT_RGBA)


def update_appearance(context):
    """Update the path colour map live (shared with the agent colour scheme).

    No-op when path colouring is not currently active.
    """
    shading.update_ramp(
        PATH_MATERIAL_NAME, PATH_RAMP_NODE, context.scene.kinora_props.agent_color_colormap
    )
