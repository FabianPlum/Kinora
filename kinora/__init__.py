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
    "description": (
        "Visualise Pedestrian Data trajectory files (SQLite and HDF5) with agent animations and "
        "geometry, and FDS Smoke3D fire simulation data"
    ),
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
from . import fds_operators, operators, panels, preferences
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


def _sync_advanced_vis_shading(props, context):
    """Keep viewports in Material Preview while any emission/volume-based advanced
    visualisation is on, reverting to Solid only when all are off.

    The overlay, agent colours, path colours, Voronoi cells and the FDS
    smoke/fire volumes are all materials Solid/Wireframe shading does not
    show, so they share one viewport-shading switch (otherwise an enabled
    effect looks like it failed).
    """
    from .core import overlay

    if (
        props.show_image_overlay
        or props.show_agent_colors
        or props.show_path_colors
        or props.show_voronoi
        or props.show_fds_smoke
        or props.show_fds_fire
    ):
        overlay.ensure_material_preview(context)
    else:
        overlay.restore_solid_shading(context)


def update_image_overlay_visibility(self, context):
    """Show/hide the overlay and sync viewport shading to its visibility."""
    from .core import overlay

    overlay.refresh(context)
    _sync_advanced_vis_shading(self, context)


def update_image_overlay_source(self, context):
    """Re-apply the overlay for a newly selected source (keeps it visible)."""
    from .core import overlay

    overlay.refresh(context)
    if self.show_image_overlay:
        overlay.ensure_material_preview(context)


def update_image_overlay_appearance(self, context):
    """Update overlay colour map / interpolation without re-reading the file."""
    from .core import overlay

    overlay.update_appearance(context)


def update_agent_colors_visibility(self, context):
    """Enable/disable per-agent colouring and sync viewport shading."""
    from .core import agent_colors

    agent_colors.refresh(context)
    _sync_advanced_vis_shading(self, context)


def update_agent_colors_appearance(self, context):
    """Update the shared agent/path colour map without re-reading the file."""
    from .core import agent_colors, path_colors

    agent_colors.update_appearance(context)
    path_colors.update_appearance(context)


def update_path_colors_visibility(self, context):
    """Enable/disable path-segment colouring and sync viewport shading."""
    from .core import path_colors

    path_colors.refresh(context)
    _sync_advanced_vis_shading(self, context)


def update_voronoi_visibility(self, context):
    """Show/hide the Voronoi cell overlay and sync viewport shading."""
    from .core import voronoi

    voronoi.refresh(context)
    _sync_advanced_vis_shading(self, context)


def update_voronoi_appearance(self, context):
    """Update the Voronoi colour map without re-reading the file."""
    from .core import voronoi

    voronoi.update_appearance(context)


def update_fds_smoke_visibility(self, context):
    """Show/hide the FDS fire & smoke volume and sync viewport shading."""
    from .core import smoke

    smoke.refresh(context)
    _sync_advanced_vis_shading(self, context)


def update_fds_smoke_appearance(self, context):
    """Live thickness/detail/flame-temperature/flame-intensity/frame-offset update."""
    from .core import smoke

    smoke.update_appearance(context)


def parse_advanced_vis_manifest(props):
    """Return the parsed advanced-visualisation manifest, or an empty one.

    The manifest is stored as a JSON string on the scene so it persists with the
    .blend and can be read cheaply from poll()/draw() and enum callbacks.
    """
    raw = props.adv_vis_manifest if props else ""
    empty = {"backgrounds": [], "agent_colors": [], "polygons": []}
    if not raw:
        return empty
    try:
        manifest = json.loads(raw)
    except (ValueError, TypeError):
        return empty
    if not isinstance(manifest, dict):
        return empty
    manifest.setdefault("backgrounds", [])
    manifest.setdefault("agent_colors", [])
    manifest.setdefault("polygons", [])
    return manifest


def parse_fds_smoke_manifest(props):
    """Return the parsed FDS Smoke3D manifest (meshes/quantities), or an empty one.

    Populated by ``KINORA_OT_select_fds_file``'s cheap probe (see
    ``io.fds_reader.probe_fds_simulation``) and stored as JSON so it persists
    with the .blend and can be read cheaply from the quantity dropdown and panel.
    """
    raw = props.fds_smoke_manifest if props else ""
    empty = {"meshes": [], "quantities": []}
    if not raw:
        return empty
    try:
        manifest = json.loads(raw)
    except (ValueError, TypeError):
        return empty
    if not isinstance(manifest, dict):
        return empty
    manifest.setdefault("meshes", [])
    manifest.setdefault("quantities", [])
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
        update=update_image_overlay_visibility,
    )

    image_overlay_source: EnumProperty(
        name="Source",
        description="Which background image from the loaded file to display",
        items=_image_overlay_source_items,
        update=update_image_overlay_source,
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

    show_agent_colors: BoolProperty(
        name="Colour Agents by Data",
        description=(
            "Colour each agent from its per-frame position_data scalar (default playback mode only)"
        ),
        default=False,
        update=update_agent_colors_visibility,
    )

    agent_color_colormap: EnumProperty(
        name="Colour Scheme",
        description="Colour map applied to the per-agent (and path) data values",
        items=colormaps.COLORMAP_ITEMS,
        default=colormaps.DEFAULT_COLORMAP,
        update=update_agent_colors_appearance,
    )

    show_path_colors: BoolProperty(
        name="Colour Paths by Data",
        description=(
            "Colour loaded path segments by the per-frame position_data scalar "
            "(requires Load Full Paths; shares the agent colour scheme)"
        ),
        default=False,
        update=update_path_colors_visibility,
    )

    show_voronoi: BoolProperty(
        name="Show Voronoi Cells",
        description=(
            "Display per-frame coloured polygons (polygon_data) on a separate "
            "Kinora_Voronoi collection"
        ),
        default=False,
        update=update_voronoi_visibility,
    )

    voronoi_colormap: EnumProperty(
        name="Colour Scheme",
        description="Colour map applied to the Voronoi cell values",
        items=colormaps.COLORMAP_ITEMS,
        default=colormaps.DEFAULT_COLORMAP,
        update=update_voronoi_appearance,
    )

    # --- FDS Smoke3D (independent of the trajectory load above) -----------
    fds_smv_file: StringProperty(
        name="FDS .smv File",
        description="Path to the FDS .smv file describing the simulation",
        default="",
        subtype="FILE_PATH",
    )

    fds_smoke_manifest: StringProperty(
        name="FDS Smoke Manifest",
        description="JSON describing meshes/quantities available in the selected .smv file",
        default="",
        options={"HIDDEN"},
    )

    fds_smoke_decimation: IntProperty(
        name="Spatial Decimation",
        description=(
            "Keep every Nth grid cell per axis when writing the volume sequence "
            "(higher = coarser voxels, faster, smaller files)"
        ),
        default=1,
        min=1,
        max=64,
        soft_max=16,
    )

    fds_smoke_frame_stride: IntProperty(
        name="Frame Stride",
        description=(
            "Write every Nth FDS timestep as one sequence frame (higher = fewer frames, "
            "faster load, less disk use, choppier playback)"
        ),
        default=5,
        min=1,
        max=1000,
        soft_max=50,
    )

    fds_refinement: EnumProperty(
        name="Refinement",
        description="Smoothing applied to the coarse CFD grid when writing the sequence",
        items=[
            (
                "SMOOTH",
                "Smooth",
                "Light gaussian smoothing; removes blocky voxel steps (recommended)",
            ),
            (
                "UPSAMPLE",
                "Smooth + Upsample 2x",
                "Smoother silhouettes at 8x the voxels (slower load, larger files)",
            ),
            ("OFF", "Off", "Raw simulation voxels"),
        ],
        default="SMOOTH",
    )

    show_fds_smoke: BoolProperty(
        name="Show Smoke",
        description="Display the smoke (soot density) channel",
        default=True,
        update=update_fds_smoke_visibility,
    )

    fds_smoke_density_multiplier: FloatProperty(
        name="Smoke Density Multiplier",
        description=(
            "Multiplier on the physically-based smoke opacity "
            "(1.0 = Smokeview-accurate Beer-Lambert extinction)"
        ),
        default=1.0,
        min=0.0,
        soft_max=5.0,
        update=update_fds_smoke_appearance,
    )

    fds_mass_extinction: FloatProperty(
        name="Mass Extinction (m²/kg)",
        description=(
            "Soot mass extinction coefficient for the Beer-Lambert smoke opacity; "
            "FDS's default MASS_EXTINCTION_COEFFICIENT is 8700 m²/kg"
        ),
        default=8700.0,
        min=1.0,
        soft_max=20000.0,
        update=update_fds_smoke_appearance,
    )

    fds_detail_amount: FloatProperty(
        name="Detail",
        description=(
            "Procedural sub-grid detail noise: breaks the smooth CFD blob into wisps (0 disables)"
        ),
        default=0.35,
        min=0.0,
        max=1.0,
        update=update_fds_smoke_appearance,
    )

    show_fds_fire: BoolProperty(
        name="Show Flame",
        description="Display the flame (HRRPUV) channel",
        default=True,
        update=update_fds_smoke_visibility,
    )

    fds_fire_density_multiplier: FloatProperty(
        name="Flame Density Multiplier",
        description="Multiplier on the flame's blackbody emission strength",
        default=5.0,
        min=0.0,
        soft_max=50.0,
        update=update_fds_smoke_appearance,
    )

    fds_flame_temperature: FloatProperty(
        name="Flame Temperature (K)",
        description="Blackbody temperature at full flame value (bright yellow-white core)",
        default=4200.0,
        min=300.0,
        max=6000.0,
        update=update_fds_smoke_appearance,
    )

    fds_smoke_frame_offset: IntProperty(
        name="Frame Offset",
        description=(
            "Shift the smoke sequence's start by this many Blender frames, for aligning "
            "with other loaded data"
        ),
        default=0,
        update=update_fds_smoke_appearance,
    )

    fds_smoke_loading_in_progress: BoolProperty(
        name="FDS Loading In Progress",
        default=False,
        options={"HIDDEN"},
    )

    fds_smoke_loading_progress: FloatProperty(
        name="FDS Loading Progress",
        default=0.0,
        min=0.0,
        max=100.0,
        subtype="PERCENTAGE",
        options={"HIDDEN"},
    )

    fds_smoke_loading_message: StringProperty(
        name="FDS Loading Message",
        default="",
        options={"HIDDEN"},
    )

    fds_smoke_loaded: BoolProperty(
        name="FDS Fire & Smoke Loaded",
        default=False,
        options={"HIDDEN"},
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
    fds_operators.register()
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
    fds_operators.unregister()
    operators.unregister()
    preferences.unregister()

    print("Kinora addon unregistered")


if __name__ == "__main__":
    register()
