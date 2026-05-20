"""
BlenderJPS Addon Preferences
Handles addon preferences and dependency installation.
"""

import os
import shutil
import stat
import sys
import time

import bpy
from bpy.types import AddonPreferences

from . import install_utils

ADDON_DIR = os.path.dirname(os.path.realpath(__file__))


def _handle_rmtree_error(func, path, exc_info):
    """rmtree onerror handler that clears the read-only bit on Windows.

    Re-raises non-permission errors so the real blocker surfaces instead of a
    misleading chmod failure.
    """
    if sys.platform != "win32" or not isinstance(exc_info[1], PermissionError):
        raise exc_info[1]
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise exc_info[1] from None


class JUPEDSIM_OT_install_dependencies(bpy.types.Operator):
    """Install required Python packages for BlenderJPS addon."""

    bl_idname = "jupedsim.install_dependencies"
    bl_label = "Install Dependencies"
    bl_description = "Install pedpy and required packages to addon's local directory"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            success, message = install_utils.install_dependencies(ADDON_DIR)

            if success:
                install_utils.ensure_deps_in_path(ADDON_DIR)

                if install_utils.is_pedpy_installed(ADDON_DIR):
                    self.report({"INFO"}, message)
                    self.report({"INFO"}, "You may need to restart Blender if imports still fail.")
                else:
                    self.report(
                        {"WARNING"},
                        "Installation completed but import still failing. Please restart Blender.",
                    )
                return {"FINISHED"}
            else:
                self.report({"ERROR"}, message)
                return {"CANCELLED"}

        except Exception as e:
            self.report({"ERROR"}, f"Unexpected error: {e}")
            return {"CANCELLED"}


class JUPEDSIM_OT_uninstall_dependencies(bpy.types.Operator):
    """Remove installed dependencies."""

    bl_idname = "jupedsim.uninstall_dependencies"
    bl_label = "Uninstall Dependencies"
    bl_description = "Remove the local deps folder"
    bl_options = {"REGISTER"}

    def execute(self, context):
        deps_dir = install_utils.get_deps_dir(ADDON_DIR)

        if not os.path.exists(deps_dir):
            self.report({"INFO"}, "No dependencies to uninstall.")
            return {"FINISHED"}

        if deps_dir in sys.path:
            sys.path.remove(deps_dir)

        # Drop pure-Python modules loaded from deps so their source files can
        # be removed. Compiled extensions (.pyd/.dll) generally stay mapped
        # for the lifetime of the Blender process even after sys.modules
        # eviction, so a Blender restart may still be needed before those
        # file handles are released.
        deps_prefix = os.path.normcase(deps_dir + os.sep)
        to_remove = [
            name
            for name, mod in sys.modules.items()
            if getattr(mod, "__file__", None)
            and os.path.normcase(mod.__file__).startswith(deps_prefix)
        ]
        for name in to_remove:
            del sys.modules[name]

        try:
            shutil.rmtree(deps_dir, onerror=_handle_rmtree_error)
        except Exception as e:
            if sys.platform != "win32":
                self.report({"ERROR"}, f"Failed to remove deps folder: {e}")
                return {"CANCELLED"}
            # Windows fallback: a still-loaded .pyd/.dll likely holds a lock.
            # Move the deps dir aside so a fresh install can proceed; the
            # leftover can be deleted after restarting Blender.
            leftover = f"{deps_dir}.deleted-{int(time.time())}"
            try:
                os.rename(deps_dir, leftover)
            except OSError:
                self.report({"ERROR"}, f"Failed to remove deps folder: {e}")
                return {"CANCELLED"}
            self.report(
                {"WARNING"},
                (
                    f"Some files were locked and could not be deleted. "
                    f"Moved deps to '{leftover}'. Restart Blender and "
                    f"delete that folder to reclaim the disk space."
                ),
            )
            return {"FINISHED"}

        self.report({"INFO"}, "Dependencies uninstalled successfully.")
        return {"FINISHED"}


class JuPedSimAddonPreferences(AddonPreferences):
    """Addon preferences for BlenderJPS."""

    bl_idname = __package__

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Dependencies", icon="PACKAGE")

        deps_dir = install_utils.get_deps_dir(ADDON_DIR)
        col = box.column(align=True)
        col.label(text=f"Install location: {deps_dir}", icon="FILE_FOLDER")
        col.separator()

        if install_utils.is_pedpy_installed(ADDON_DIR):
            row = box.row()
            row.label(text="pedpy: Installed", icon="CHECKMARK")

            try:
                install_utils.ensure_deps_in_path(ADDON_DIR)
                import pedpy

                version = getattr(pedpy, "__version__", "unknown")
                row = box.row()
                row.label(text=f"Version: {version}")
            except (ImportError, AttributeError):
                pass

            box.separator()
            row = box.row()
            row.operator("jupedsim.uninstall_dependencies", icon="TRASH")
        else:
            row = box.row()
            row.label(text="pedpy: Not Installed", icon="ERROR")

            box.separator()
            box.label(text="Click below to install dependencies:", icon="INFO")
            box.label(text="(No admin privileges required)")
            box.separator()

            row = box.row()
            row.scale_y = 1.5
            row.operator("jupedsim.install_dependencies", icon="IMPORT")


classes = [
    JUPEDSIM_OT_install_dependencies,
    JUPEDSIM_OT_uninstall_dependencies,
    JuPedSimAddonPreferences,
]


def register():
    install_utils.ensure_deps_in_path(ADDON_DIR)

    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
