"""Kinora - Pedestrian Data Trajectory Visualiser for Blender.

A Blender addon for visualising pedestrian data trajectory files (JuPedSim SQLite and HDF5).
"""

import json
import os

from . import install_utils

ADDON_DIR = os.path.dirname(os.path.realpath(__file__))

install_utils.ensure_deps_in_path(ADDON_DIR)

bl_info = {
    "name": "Kinora - Pedestrian Data Visualiser",
    "author": "Fabian Plum & Mohcine Chraibi",
    "version": (0, 2, 1),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Kinora",
    "description": "Visualise Pedestrian Data trajectory files (SQLite and HDF5) with agent animations and geometry",
    "doc_url": "https://github.com/FabianPlum/Kinora",
    "tracker_url": "https://github.com/FabianPlum/Kinora/issues",
    "category": "Import-Export",
    "support": "COMMUNITY",
}

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

# Import submodules
from . import operators, panels, preferences
from .core import colormaps


def update_path_visibility(self, context):
    """Update visibility of all agent path curves when property changes."""
    if "Kinora_Agents" not in bpy.data.collections:
        return

    collection = bpy.data.collections["Kinora_Agents"]
    for obj in collection.objects:
        if obj.name.startswith("Path_Agent_"):
            obj.hide_viewport = not self.show_paths
            obj.hide_render = not self.show_paths


def update_agent_scale(self, context):
    """Update scale of all agent meshes when property changes."""
    if "Kinora_Agents" not in bpy.data.collections:
        return
    collection = bpy.data.collections["Kinora_Agents"]
    for obj in collection.objects:
        if obj.name.startswith("Agent_") and obj.type == "MESH":
            obj.scale = (self.agent_scale, self.agent_scale, self.agent_scale)
        if obj.name == "Kinora_ParticleInstance" and obj.type == "MESH":
            obj.scale = (self.agent_scale, self.agent_scale, self.agent_scale)


def update_geometry_thickness(self, context):
    """Update bevel thickness for all geometry curves when property changes."""
    if "Kinora_Geometry" not in bpy.data.collections:
        return
    collection = bpy.data.collections["Kinora_Geometry"]
    for obj in collection.objects:
        if obj.type == "CURVE":
            obj.data.bevel_depth = self.geometry_thickness


def update_image_overlay(self, context):
    """Apply, update, or remove the background overlay when settings change."""
    from .core import overlay

    overlay.refresh(context)


def update_image_overlay_appearance(self, context):
    """Update overlay colour map / interpolation without re-reading the file."""
    from .core import overlay

    overlay.update_appearance(context)


def parse_advanced_vis_manifest(props):
    """Return the parsed advanced-visualisation manifest, or an empty one.

    The manifest is stored as a JSON string on the scene so it persists with the
    .blend and can be read cheaply from poll()/draw() and enum callbacks.
    """
    raw = props.adv_vis_manifest if props else ""
    if not raw:
        return {"backgrounds": []}
    try:
        manifest = json.loads(raw)
    except (ValueError, TypeError):
        return {"backgrounds": []}
    if not isinstance(manifest, dict):
        return {"backgrounds": []}
    manifest.setdefault("backgrounds", [])
    return manifest


# Blender garbage-collects strings returned by an EnumProperty items callback
# unless we keep our own reference, which can crash the UI.  Cache the last
# returned list here to keep the identifier/name strings alive.
_image_overlay_enum_cache = [("NONE", "None", "")]


def _image_overlay_source_items(self, context):
    """Build the background-image dropdown from the loaded file's manifest.

    Each available background source becomes one selectable item; only one can
    be active at a time.  Falls back to a single placeholder when nothing is
    available so the property always has a valid value.
    """
    global _image_overlay_enum_cache
    items = [
        (bg["id"], bg.get("label", bg["id"]), bg.get("data_path", ""))
        for bg in parse_advanced_vis_manifest(self).get("backgrounds", [])
        if bg.get("id")
    ]
    _image_overlay_enum_cache = items or [("NONE", "None", "")]
    return _image_overlay_enum_cache


class KinoraProperties(PropertyGroup):
    """Property group for Kinora addon settings."""

    sqlite_file: StringProperty(
        name="SQLite File",
        description="Path to the JuPedSim trajectory SQLite file",
        default="",
        subtype="FILE_PATH",
    )

    frame_step: IntProperty(
        name="Frame Step",
        description="Load every Nth frame (1 = all frames, 10 = every 10th frame, etc.). When >1, Blender frame F shows SQLite frame F×N.",
        default=10,
        min=1,
        max=99999,
        soft_max=1000,
    )

    big_data_mode: BoolProperty(
        name="Big Data Mode",
        description="Load trajectories into a single point cloud for fast playback (high RAM usage)",
        default=False,
    )

    load_full_paths: BoolProperty(
        name="Load Full Paths",
        description="Load full agent paths as curves (can be very slow for large files)",
        default=False,
    )

    show_paths: BoolProperty(
        name="Show Agent Paths",
        description=("Show/hide path curves (reload with 'Load Full Paths' enabled to use)"),
        default=False,
        update=update_path_visibility,
    )

    agent_scale: FloatProperty(
        name="Agent Scale (m)",
        description="Display scale for agents in meters",
        default=0.2,
        min=0.01,
        max=10.0,
        update=update_agent_scale,
    )

    geometry_thickness: FloatProperty(
        name="Geometry Thickness (m)",
        description="Curve thickness for geometry boundaries",
        default=0.05,
        min=0.0,
        max=10.0,
        update=update_geometry_thickness,
    )

    loading_in_progress: BoolProperty(
        name="Loading In Progress",
        description="Indicates whether a load operation is currently running",
        default=False,
        options={"HIDDEN"},
    )

    loading_progress: FloatProperty(
        name="Loading Progress",
        description="Progress of the current loading operation",
        default=0.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        options={"HIDDEN"},
    )

    loading_message: StringProperty(
        name="Loading Message",
        description="Status message for the current loading operation",
        default="",
        options={"HIDDEN"},
    )

    loaded_agent_count: IntProperty(
        name="Loaded Agent Count",
        description="Number of agents detected in the loaded simulation",
        default=0,
        min=0,
        options={"HIDDEN"},
    )

    # --- Advanced visualisations -------------------------------------------
    adv_vis_manifest: StringProperty(
        name="Advanced Visualisation Manifest",
        description="JSON describing optional visualisation data in the loaded file",
        default="",
        options={"HIDDEN"},
    )

    show_image_overlay: BoolProperty(
        name="Show Background Overlay",
        description="Display a pre-computed bitmap (e.g. density) on the ground plane",
        default=False,
        update=update_image_overlay,
    )

    image_overlay_source: EnumProperty(
        name="Source",
        description="Which background image from the loaded file to display",
        items=_image_overlay_source_items,
        update=update_image_overlay,
    )

    image_overlay_colormap: EnumProperty(
        name="Colour Scheme",
        description="Colour map applied to the background image values",
        items=colormaps.COLORMAP_ITEMS,
        default=colormaps.DEFAULT_COLORMAP,
        update=update_image_overlay_appearance,
    )

    image_overlay_interpolation: EnumProperty(
        name="Interpolation",
        description="How background image pixels are interpolated on the ground plane",
        items=colormaps.INTERPOLATION_ITEMS,
        default=colormaps.DEFAULT_INTERPOLATION,
        update=update_image_overlay_appearance,
    )


# List of classes to register
classes = [
    KinoraProperties,
]


def register():
    """Register the addon."""
    # Register classes from submodules first
    preferences.register()
    operators.register()
    panels.register()

    # Register main classes
    for cls in classes:
        bpy.utils.register_class(cls)

    # Add properties to scene
    bpy.types.Scene.kinora_props = PointerProperty(type=KinoraProperties)

    print("Kinora addon registered successfully")


def unregister():
    """Unregister the addon."""
    # Remove properties from scene
    del bpy.types.Scene.kinora_props

    # Unregister main classes
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    # Unregister submodule classes
    panels.unregister()
    operators.unregister()
    preferences.unregister()

    print("Kinora addon unregistered")


if __name__ == "__main__":
    register()
