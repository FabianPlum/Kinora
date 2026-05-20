"""BlenderJPS - JuPedSim Trajectory Importer for Blender.

A Blender addon for importing JuPedSim simulation SQLite files.
"""

import os

from . import install_utils

ADDON_DIR = os.path.dirname(os.path.realpath(__file__))

install_utils.ensure_deps_in_path(ADDON_DIR)

bl_info = {
    "name": "BlenderJPS - JuPedSim Importer",
    "author": "Fabian Plum & Mohcine Chraibi",
    "version": (0, 1, 6),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > JuPedSim",
    "description": "Import JuPedSim trajectory SQLite files with agent animations and geometry",
    "doc_url": "https://github.com/FabianPlum/BlenderJPS",
    "tracker_url": "https://github.com/FabianPlum/BlenderJPS/issues",
    "category": "Import-Export",
    "support": "COMMUNITY",
}

import bpy
from bpy.props import (
    BoolProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

# Import submodules
from . import operators, panels, preferences


def update_path_visibility(self, context):
    """Update visibility of all agent path curves when property changes."""
    if "JuPedSim_Agents" not in bpy.data.collections:
        return

    collection = bpy.data.collections["JuPedSim_Agents"]
    for obj in collection.objects:
        if obj.name.startswith("Path_Agent_"):
            obj.hide_viewport = not self.show_paths
            obj.hide_render = not self.show_paths


def update_agent_scale(self, context):
    """Update scale of all agent meshes when property changes."""
    if "JuPedSim_Agents" not in bpy.data.collections:
        return
    collection = bpy.data.collections["JuPedSim_Agents"]
    for obj in collection.objects:
        if obj.name.startswith("Agent_") and obj.type == "MESH":
            obj.scale = (self.agent_scale, self.agent_scale, self.agent_scale)
        if obj.name == "JuPedSim_ParticleInstance" and obj.type == "MESH":
            obj.scale = (self.agent_scale, self.agent_scale, self.agent_scale)


def update_geometry_thickness(self, context):
    """Update bevel thickness for all geometry curves when property changes."""
    if "JuPedSim_Geometry" not in bpy.data.collections:
        return
    collection = bpy.data.collections["JuPedSim_Geometry"]
    for obj in collection.objects:
        if obj.type == "CURVE":
            obj.data.bevel_depth = self.geometry_thickness


class JuPedSimProperties(PropertyGroup):
    """Property group for JuPedSim addon settings."""

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


# List of classes to register
classes = [
    JuPedSimProperties,
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
    bpy.types.Scene.jupedsim_props = PointerProperty(type=JuPedSimProperties)

    print("BlenderJPS addon registered successfully")


def unregister():
    """Unregister the addon."""
    # Remove properties from scene
    del bpy.types.Scene.jupedsim_props

    # Unregister main classes
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    # Unregister submodule classes
    panels.unregister()
    operators.unregister()
    preferences.unregister()

    print("BlenderJPS addon unregistered")


if __name__ == "__main__":
    register()
