"""
Kinora UI Panels
User interface panels for the Kinora addon.
"""

import os

import bpy
from bpy.types import Context, Panel

from .install_utils import is_fdsreader_installed, is_pedpy_installed

ADDON_DIR = os.path.dirname(os.path.realpath(__file__))


class KINORA_PT_main_panel(Panel):
    """Main panel for Kinora in the 3D Viewport sidebar."""

    bl_label = "Kinora"
    bl_idname = "KINORA_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kinora"

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.kinora_props

        # Check dependencies
        if not is_pedpy_installed(ADDON_DIR):
            box = layout.box()
            box.alert = True
            box.label(text="Dependencies not installed!", icon="ERROR")
            box.label(text="Go to Edit > Preferences > Add-ons")
            box.label(text="Find 'Kinora' and install dependencies")
            box.separator()
            box.operator("kinora.install_dependencies", text="Install Dependencies", icon="IMPORT")
            return

        # File selection section
        box = layout.box()
        box.label(text="Trajectory File", icon="FILE")

        # Display selected file or prompt
        if props.sqlite_file:
            import os

            filename = os.path.basename(props.sqlite_file)
            box.label(text=filename, icon="CHECKMARK")
        else:
            box.label(text="No file selected", icon="QUESTION")

        box.operator("kinora.select_file", text="Browse...", icon="FILEBROWSER")

        layout.separator()

        # Import options
        box = layout.box()
        box.label(text="Import Options", icon="SETTINGS")
        row = box.row()
        row.prop(props, "frame_step", text="Load Every Nth Frame")
        row = box.row()
        row.prop(props, "big_data_mode", text="Big Data Mode (load agent data as particles)")
        row = box.row()
        row.prop(props, "load_full_paths", text="Load Full Paths (slow)")
        if props.load_full_paths:
            box.label(text="Warning: may take a long time on large files", icon="ERROR")

        layout.separator()

        # Load button
        row = layout.row()
        row.scale_y = 1.5
        row.operator("kinora.load_simulation", text="Load Simulation", icon="IMPORT")

        if props.loading_in_progress:
            box = layout.box()
            box.label(text=props.loading_message or "Loading...", icon="TIME")
            box.prop(props, "loading_progress", text="Progress", slider=True)
            box.label(text="Press Esc to cancel", icon="CANCEL")

        layout.separator()

        # Display options (always visible)
        box = layout.box()
        box.label(text="Display Options", icon="HIDE_OFF")
        row = box.row()
        row.prop(props, "agent_scale", text="Agent Scale (m)")
        row = box.row()
        row.prop(props, "geometry_thickness", text="Geometry Thickness (m)")
        row = box.row()
        fps_label = f"Frame Rate: {context.scene.render.fps} fps"
        row.menu("RENDER_MT_framerate_presets", text=fps_label)
        row = box.row()
        row.prop(props, "show_paths", text="Show Agent Paths")
        has_paths = False
        if "Kinora_Agents" in bpy.data.collections:
            agents_collection = bpy.data.collections["Kinora_Agents"]
            path_objects = [
                obj for obj in agents_collection.objects if obj.name.startswith("Path_Agent_")
            ]
            has_paths = bool(path_objects)
            if has_paths:
                box.label(text=f"({len(path_objects)} path curves)", icon="CURVE_DATA")
        row.enabled = props.load_full_paths and has_paths

        # Info section
        layout.separator()
        box = layout.box()
        box.label(text="Info", icon="INFO")
        box.label(text="Agents → Animated cylinders")
        box.label(text="Geometry → Curve boundaries")


class KINORA_PT_advanced_vis_panel(Panel):
    """Advanced visualisation options, shown only when the file provides them."""

    bl_label = "Advanced Visualisations"
    bl_idname = "KINORA_PT_advanced_vis_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kinora"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        from . import parse_advanced_vis_manifest

        props = getattr(context.scene, "kinora_props", None)
        if not props:
            return False
        manifest = parse_advanced_vis_manifest(props)
        return bool(
            manifest.get("backgrounds") or manifest.get("agent_colors") or manifest.get("polygons")
        )

    def draw(self, context: Context) -> None:
        from . import parse_advanced_vis_manifest
        from .core.streaming import STREAM_STATE

        layout = self.layout
        props = context.scene.kinora_props
        manifest = parse_advanced_vis_manifest(props)

        if manifest.get("backgrounds"):
            box = layout.box()
            box.label(text="Background Image", icon="IMAGE_DATA")
            box.prop(props, "show_image_overlay", text="Show Overlay")
            col = box.column()
            col.enabled = props.show_image_overlay
            col.prop(props, "image_overlay_source", text="Source")
            col.prop(props, "image_overlay_colormap", text="Colour")
            col.prop(props, "image_overlay_interpolation", text="Interp")

        if manifest.get("agent_colors"):
            agents = bpy.data.collections.get("Kinora_Agents")
            has_paths = bool(
                agents and any(o.name.startswith("Path_Agent_") for o in agents.objects)
            )
            box = layout.box()
            box.label(text="Agent Colour", icon="COLOR")
            box.prop(props, "show_agent_colors", text="Colour Agents")
            path_row = box.row()
            path_row.enabled = has_paths
            path_row.prop(props, "show_path_colors", text="Colour Paths")
            col = box.column()
            col.enabled = props.show_agent_colors or (props.show_path_colors and has_paths)
            col.prop(props, "agent_color_colormap", text="Colour")
            if STREAM_STATE.get("mode") == "big":
                box.label(text="Agent colour needs default (non-big-data) mode", icon="INFO")

        if manifest.get("polygons"):
            box = layout.box()
            box.label(text="Voronoi Cells", icon="MOD_TRIANGULATE")
            box.prop(props, "show_voronoi", text="Show Cells")
            col = box.column()
            col.enabled = props.show_voronoi
            col.prop(props, "voronoi_colormap", text="Colour")


def _fds_sequence_estimate(manifest, quantity_name, decimation, frame_stride):
    """Estimate voxels/frame and total sequence file count for the given settings.

    Cheap: only reads counts already stored in the manifest, no file I/O.
    """
    quantity = next((q for q in manifest.get("quantities", []) if q["name"] == quantity_name), None)
    if quantity is None:
        return 0, 0
    mesh_ids = set(quantity.get("mesh_ids", []))
    voxels_per_frame = 0
    for mesh in manifest.get("meshes", []):
        if mesh["id"] not in mesh_ids:
            continue
        count = 1
        for dim in mesh["dims"]:
            count *= max(1, (dim + decimation - 1) // decimation)
        voxels_per_frame += count
    n_t = quantity.get("n_t", 0)
    frame_count = (n_t + frame_stride - 1) // frame_stride if n_t else 0
    file_count = frame_count * max(1, len(mesh_ids))
    return voxels_per_frame, file_count


class KINORA_PT_fds_smoke_panel(Panel):
    """FDS fire & smoke panel, independent of the trajectory loader.

    One load reads the smoke quantity (soot density) and the optional flame
    quantity (HRRPUV) and builds a single combined volume per FDS mesh (see
    core.smoke). Smoke opacity is physically based (Smokeview's Beer-Lambert
    extinction), so the defaults look right without tuning.
    """

    bl_label = "FDS Fire & Smoke (Smoke3D)"
    bl_idname = "KINORA_PT_fds_smoke_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kinora"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Context) -> None:
        from . import parse_fds_smoke_manifest

        layout = self.layout
        props = context.scene.kinora_props

        if not is_fdsreader_installed(ADDON_DIR):
            box = layout.box()
            box.alert = True
            box.label(text="fdsreader not installed!", icon="ERROR")
            box.label(text="Go to Edit > Preferences > Add-ons")
            box.label(text="Find 'Kinora' and install dependencies")
            box.separator()
            box.operator("kinora.install_dependencies", text="Install Dependencies", icon="IMPORT")
            return

        box = layout.box()
        box.label(text="FDS Simulation", icon="FILE")
        if props.fds_smv_file:
            box.label(text=os.path.basename(props.fds_smv_file), icon="CHECKMARK")
        else:
            box.label(text="No file selected", icon="QUESTION")
        box.operator("kinora.select_fds_file", text="Browse...", icon="FILEBROWSER")

        manifest = parse_fds_smoke_manifest(props)
        quantities = manifest.get("quantities", [])
        if not quantities:
            return
        names = [q["name"] for q in quantities]

        layout.separator()
        box = layout.box()
        box.label(text="Data", icon="SETTINGS")
        smoke_state = "found" if "SOOT DENSITY" in names else "MISSING"
        flame_state = "found" if "HRRPUV" in names else "not in file"
        box.label(text=f"Smoke (SOOT DENSITY): {smoke_state}")
        box.label(text=f"Flame (HRRPUV): {flame_state}")
        box.prop(props, "fds_smoke_decimation")
        box.prop(props, "fds_smoke_frame_stride")
        box.prop(props, "fds_refinement", text="Refine")

        voxels_per_frame, file_count = _fds_sequence_estimate(
            manifest,
            "SOOT DENSITY",
            props.fds_smoke_decimation,
            props.fds_smoke_frame_stride,
        )
        if props.fds_refinement == "UPSAMPLE":
            voxels_per_frame *= 8
        box.label(text=f"~{voxels_per_frame:,} voxels/frame, {file_count} files to write")

        layout.separator()
        row = layout.row()
        row.scale_y = 1.5
        row.operator("kinora.load_fds_smoke", text="Load Fire & Smoke", icon="IMPORT")

        if props.fds_smoke_loading_in_progress:
            box = layout.box()
            box.label(text=props.fds_smoke_loading_message or "Loading...", icon="TIME")
            box.prop(props, "fds_smoke_loading_progress", text="Progress", slider=True)
            box.label(text="Press Esc to cancel", icon="CANCEL")

        if props.fds_smoke_loaded:
            layout.separator()
            box = layout.box()
            box.label(text="Smoke", icon="VOLUME_DATA")
            box.prop(props, "show_fds_smoke", text="Show Smoke")
            col = box.column()
            col.enabled = props.show_fds_smoke
            col.prop(props, "fds_smoke_density_multiplier", text="Density Multiplier")
            col.prop(props, "fds_mass_extinction")
            col.prop(props, "fds_detail_amount")

            box = layout.box()
            box.label(text="Flame", icon="LIGHT_SUN")
            box.prop(props, "show_fds_fire", text="Show Flame")
            col = box.column()
            col.enabled = props.show_fds_fire
            col.prop(props, "fds_fire_density_multiplier", text="Density Multiplier")
            col.prop(props, "fds_flame_temperature")

            layout.prop(props, "fds_smoke_frame_offset")
            layout.separator()
            layout.operator("kinora.unload_fds_smoke", text="Unload Fire & Smoke", icon="TRASH")


class KINORA_PT_info_panel(Panel):
    """Info panel showing loaded simulation statistics."""

    bl_label = "Trajectory Info"
    bl_idname = "KINORA_PT_info_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kinora"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Context) -> None:
        layout = self.layout

        # Count agents and geometry
        agents_count = 0
        geometry_count = 0

        if context.scene.kinora_props.loaded_agent_count:
            agents_count = context.scene.kinora_props.loaded_agent_count
        elif "Kinora_Agents" in bpy.data.collections:
            agents_count = len(bpy.data.collections["Kinora_Agents"].objects)

        if "Kinora_Geometry" in bpy.data.collections:
            geometry_count = len(bpy.data.collections["Kinora_Geometry"].objects)

        box = layout.box()
        box.label(text=f"Agents loaded: {agents_count}")
        box.label(text=f"Geometry curves: {geometry_count}")
        box.label(text=f"Frame range: {context.scene.frame_start} - {context.scene.frame_end}")


classes = [
    KINORA_PT_main_panel,
    KINORA_PT_advanced_vis_panel,
    KINORA_PT_fds_smoke_panel,
    KINORA_PT_info_panel,
]


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
