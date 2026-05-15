"""
BlenderJPS UI Panels
User interface panels for the JuPedSim importer.
"""

import os

import bpy
from bpy.types import Context, Panel

from .core import navmesh as nav
from .install_utils import is_jupedsim_installed, is_pedpy_installed

ADDON_DIR = os.path.dirname(os.path.realpath(__file__))


class JUPEDSIM_PT_main_panel(Panel):
    """Main panel for JuPedSim importer in the 3D Viewport sidebar."""

    bl_label = "JuPedSim Importer"
    bl_idname = "JUPEDSIM_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "JuPedSim"

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.jupedsim_props

        # Check dependencies
        if not is_pedpy_installed(ADDON_DIR):
            box = layout.box()
            box.alert = True
            box.label(text="Dependencies not installed!", icon="ERROR")
            box.label(text="Go to Edit > Preferences > Add-ons")
            box.label(text="Find 'BlenderJPS' and install dependencies")
            box.separator()
            box.operator(
                "jupedsim.install_dependencies", text="Install Dependencies", icon="IMPORT"
            )
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

        box.operator("jupedsim.select_file", text="Browse...", icon="FILEBROWSER")

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
        row.operator("jupedsim.load_simulation", text="Load Simulation", icon="IMPORT")

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
        if "JuPedSim_Agents" in bpy.data.collections:
            agents_collection = bpy.data.collections["JuPedSim_Agents"]
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
        box.label(text="Agents → Animated spheres")
        box.label(text="Geometry → Curve boundaries")


class JUPEDSIM_PT_info_panel(Panel):
    """Info panel showing loaded simulation statistics."""

    bl_label = "Trajectory Info"
    bl_idname = "JUPEDSIM_PT_info_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "JuPedSim"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Context) -> None:
        layout = self.layout

        # Count agents and geometry
        agents_count = 0
        geometry_count = 0

        if context.scene.jupedsim_props.loaded_agent_count:
            agents_count = context.scene.jupedsim_props.loaded_agent_count
        elif "JuPedSim_Agents" in bpy.data.collections:
            agents_count = len(bpy.data.collections["JuPedSim_Agents"].objects)

        if "JuPedSim_Geometry" in bpy.data.collections:
            geometry_count = len(bpy.data.collections["JuPedSim_Geometry"].objects)

        box = layout.box()
        box.label(text=f"Agents loaded: {agents_count}")
        box.label(text=f"Geometry curves: {geometry_count}")
        box.label(text=f"Frame range: {context.scene.frame_start} - {context.scene.frame_end}")


class JUPEDSIM_PT_navmesh_panel(Panel):
    """Routing-engine debug visualization (triangulation + path queries)."""

    bl_label = "Navigation Debug"
    bl_idname = "JUPEDSIM_PT_navmesh_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "JuPedSim"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.jupedsim_props

        if not is_jupedsim_installed(ADDON_DIR):
            box = layout.box()
            box.alert = True
            box.label(text="jupedsim not installed", icon="ERROR")
            box.label(text="Re-run 'Install Dependencies' in")
            box.label(text="addon preferences to enable this.")
            return

        levels = nav.available_levels()
        if not levels:
            layout.label(text="Load a simulation first.", icon="INFO")
            return

        box = layout.box()
        box.label(text="Triangulation", icon="MESH_DATA")
        row = box.row(align=True)
        row.operator("jupedsim.show_navmesh", text="Show", icon="HIDE_OFF")
        row.operator("jupedsim.hide_navmesh", text="Hide", icon="HIDE_ON")

        box = layout.box()
        box.label(text="Router Query", icon="DRIVER_DISTANCE")
        if len(levels) > 1:
            box.prop(props, "route_level", text="Level")
        row = box.row()
        row.scale_y = 1.3
        row.operator(
            "jupedsim.pick_route", text="Pick Route (LMB-drag)", icon="RESTRICT_SELECT_OFF"
        )
        box.separator()
        box.label(text="Or enter coordinates:")
        row = box.row(align=True)
        row.prop(props, "route_from", text="From")
        op = row.operator("jupedsim.route_endpoint_from_cursor", text="", icon="CURSOR")
        op.target = "from"
        row = box.row(align=True)
        row.prop(props, "route_to", text="To")
        op = row.operator("jupedsim.route_endpoint_from_cursor", text="", icon="CURSOR")
        op.target = "to"
        row = box.row(align=True)
        row.operator("jupedsim.compute_route", text="Compute Route", icon="PLAY")
        row.operator("jupedsim.clear_routes", text="Clear", icon="X")


classes = [
    JUPEDSIM_PT_main_panel,
    JUPEDSIM_PT_info_panel,
    JUPEDSIM_PT_navmesh_panel,
]


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
