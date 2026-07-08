"""
Kinora FDS Operators
Operators for loading FDS Smoke3D fire & smoke data.

Kept separate from operators.py (the JuPedSim trajectory loader): its own file
picker, its own modal load operator, its own unload action, entirely
independent of kinora_props.sqlite_file / KINORA_OT_load_simulation, so fire &
smoke can be loaded standalone or alongside an already-loaded trajectory.

One load reads both the smoke quantity (soot density) and the optional flame
quantity (HRRPUV) and writes a single combined multi-grid VDB sequence (see
io.fds_reader.build_fire_smoke_sequence / core.smoke).
"""

import json
import os
import pathlib
import threading
import traceback

import bpy
from bpy.props import StringProperty
from bpy.types import Context, Operator
from bpy_extras.io_utils import ImportHelper

from . import install_utils
from .core import smoke as smoke_core
from .io.fds_reader import build_fire_smoke_sequence, probe_fds_simulation, read_smoke_quantity

ADDON_DIR = os.path.dirname(os.path.realpath(__file__))

# Preferred default quantities, by FDS/Smokeview convention: SOOT DENSITY is
# the smoke visualisation quantity, HRRPUV the flame envelope.
_SMOKE_QUANTITY_DEFAULT = "SOOT DENSITY"
_FLAME_QUANTITY_DEFAULT = "HRRPUV"


def check_fds_dependencies() -> tuple[bool, str | None]:
    """Check if fdsreader is installed."""
    import importlib.util

    install_utils.ensure_deps_in_path(ADDON_DIR)
    if importlib.util.find_spec("fdsreader") is None:
        return False, "fdsreader not found"
    return True, None


class KINORA_OT_select_fds_file(Operator, ImportHelper):
    """Select an FDS .smv file and probe its available meshes/quantities."""

    bl_idname = "kinora.select_fds_file"
    bl_label = "Select FDS Simulation (.smv)"
    bl_description = "Browse for an FDS .smv file"

    filter_glob: StringProperty(default="*.smv", options={"HIDDEN"})

    def execute(self, context: Context) -> set[str]:
        deps_ok, error = check_fds_dependencies()
        if not deps_ok:
            self.report({"ERROR"}, f"Missing dependency: {error}")
            self.report({"ERROR"}, "Please install dependencies in addon preferences.")
            return {"CANCELLED"}

        path = pathlib.Path(bpy.path.abspath(self.filepath))
        if not path.exists():
            self.report({"ERROR"}, f"File not found: {path}")
            return {"CANCELLED"}

        # Cheap (.smv-metadata only, no smoke data touched) - safe to run synchronously.
        try:
            manifest, _ = probe_fds_simulation(path, threading.Event())
        except Exception as e:
            self.report({"ERROR"}, f"Failed to read FDS simulation: {e}")
            return {"CANCELLED"}
        if manifest is None:
            self.report({"ERROR"}, "Failed to read FDS simulation")
            return {"CANCELLED"}

        props = context.scene.kinora_props
        props.fds_smv_file = self.filepath
        props.fds_smoke_manifest = json.dumps(manifest)
        names = [q["name"] for q in manifest.get("quantities", [])]
        if names:
            props.fds_smoke_quantity = (
                _SMOKE_QUANTITY_DEFAULT if _SMOKE_QUANTITY_DEFAULT in names else names[0]
            )
            props.fds_fire_quantity = (
                _FLAME_QUANTITY_DEFAULT if _FLAME_QUANTITY_DEFAULT in names else "NONE"
            )
        self.report({"INFO"}, f"Found {len(names)} Smoke3D quantities in {path.name}")
        return {"FINISHED"}


class KINORA_OT_load_fds_smoke(Operator):
    """Decode the selected quantities and build the combined fire & smoke volume sequence."""

    bl_idname = "kinora.load_fds_smoke"
    bl_label = "Load Fire & Smoke"
    bl_description = "Decode the selected quantities and build the volume sequence"
    bl_options = {"REGISTER", "UNDO"}

    _timer: bpy.types.Timer | None = None
    _worker_thread: threading.Thread | None = None
    _worker_done: bool = False
    _worker_error: str | None = None
    _worker_result: tuple | None = None
    _worker_traceback: str | None = None
    _cancel_event: threading.Event | None = None
    _cancelled: bool = False

    def execute(self, context: Context) -> set[str]:
        deps_ok, error = check_fds_dependencies()
        if not deps_ok:
            self.report({"ERROR"}, f"Missing dependency: {error}")
            self.report({"ERROR"}, "Please install dependencies in addon preferences.")
            return {"CANCELLED"}

        props = context.scene.kinora_props
        if props.fds_smoke_loading_in_progress:
            self.report({"WARNING"}, "A load is already in progress")
            return {"CANCELLED"}

        filepath = props.fds_smv_file
        if not filepath:
            self.report({"ERROR"}, "No FDS .smv file selected")
            return {"CANCELLED"}
        path = pathlib.Path(bpy.path.abspath(filepath))
        if not path.exists():
            self.report({"ERROR"}, f"File not found: {path}")
            return {"CANCELLED"}

        smoke_quantity = props.fds_smoke_quantity
        if not smoke_quantity or smoke_quantity == "NONE":
            self.report({"ERROR"}, "No smoke quantity selected")
            return {"CANCELLED"}
        flame_quantity = props.fds_fire_quantity
        if flame_quantity == "NONE":
            flame_quantity = None

        self._reset_state()
        self._cancel_event = threading.Event()
        props.fds_smoke_loading_in_progress = True
        props.fds_smoke_loading_progress = 0.0
        props.fds_smoke_loading_message = f"Decoding '{smoke_quantity}'..."

        self._worker_thread = threading.Thread(
            target=self._run_worker,
            args=(
                path,
                smoke_quantity,
                flame_quantity,
                int(props.fds_smoke_decimation),
                int(props.fds_smoke_frame_stride),
                props.fds_refinement,
                self._cancel_event,
            ),
            daemon=True,
        )
        self._worker_thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: bpy.types.Event) -> set[str]:
        props = context.scene.kinora_props

        if event.type == "ESC":
            self._cancelled = True
            if self._cancel_event:
                self._cancel_event.set()
            props.fds_smoke_loading_message = "Cancelling..."
            return self._finish_cancel(context)

        if event.type != "TIMER":
            return {"RUNNING_MODAL"}

        if self._cancelled:
            return self._finish_cancel(context)

        if not self._worker_done:
            props.fds_smoke_loading_progress = 50.0
            return {"RUNNING_MODAL"}

        if self._worker_error:
            self.report({"ERROR"}, f"Failed to load fire & smoke data: {self._worker_error}")
            if self._worker_traceback:
                print(self._worker_traceback)
            return self._finish_cancel(context)

        if self._worker_result is None:
            self.report({"WARNING"}, "No data returned from worker")
            return self._finish_cancel(context)

        props.fds_smoke_loading_message = "Configuring volume..."
        props.fds_smoke_loading_progress = 95.0

        sequence_dir, mesh_ids, frame_count = self._worker_result
        smoke_core.set_vdb_sequence(sequence_dir, mesh_ids, frame_count)
        props.show_fds_smoke = True
        smoke_core.refresh(context)

        props.fds_smoke_loaded = True
        props.fds_smoke_loading_progress = 100.0
        props.fds_smoke_loading_message = "Load complete"
        self.report({"INFO"}, f"Loaded fire & smoke ({frame_count} frames)")
        return self._finish_success(context)

    def _reset_state(self) -> None:
        self._worker_thread = None
        self._worker_done = False
        self._worker_error = None
        self._worker_result = None
        self._worker_traceback = None
        self._cancel_event = None
        self._cancelled = False

    def _finish_success(self, context: Context) -> set[str]:
        self._cleanup_timer(context)
        context.scene.kinora_props.fds_smoke_loading_in_progress = False
        return {"FINISHED"}

    def _finish_cancel(self, context: Context) -> set[str]:
        self._cleanup_timer(context)
        props = context.scene.kinora_props
        props.fds_smoke_loading_in_progress = False
        props.fds_smoke_loading_message = "Load cancelled"
        return {"CANCELLED"}

    def _cleanup_timer(self, context: Context) -> None:
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _run_worker(
        self,
        path: pathlib.Path,
        smoke_quantity: str,
        flame_quantity: str | None,
        decimation: int,
        frame_stride: int,
        refinement: str,
        cancel_event: threading.Event,
    ) -> None:
        """Decode smoke (and optionally flame), then write the combined VDB sequence."""
        try:
            smoke_data, _ = read_smoke_quantity(path, smoke_quantity, None, cancel_event)
            if smoke_data is None or cancel_event.is_set():
                self._worker_done = True
                return
            flame_data = None
            if flame_quantity is not None:
                flame_data, _ = read_smoke_quantity(path, flame_quantity, None, cancel_event)
                if cancel_event.is_set():
                    self._worker_done = True
                    return
            self._worker_result = build_fire_smoke_sequence(
                smoke_data, flame_data, decimation, frame_stride, refinement, cancel_event
            )
            self._worker_done = True
        except Exception as e:
            self._worker_error = str(e)
            self._worker_traceback = traceback.format_exc()
            self._worker_done = True


class KINORA_OT_unload_fds_smoke(Operator):
    """Remove the fire & smoke volume from the scene and free its decoded data."""

    bl_idname = "kinora.unload_fds_smoke"
    bl_label = "Unload Fire & Smoke"
    bl_description = "Remove the fire & smoke volume and free its decoded data"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: Context) -> set[str]:
        smoke_core.clear()
        props = context.scene.kinora_props
        props.fds_smoke_loaded = False
        props.fds_smoke_loading_message = ""
        props.fds_smoke_loading_progress = 0.0
        self.report({"INFO"}, "FDS fire & smoke unloaded")
        return {"FINISHED"}


classes = [
    KINORA_OT_select_fds_file,
    KINORA_OT_load_fds_smoke,
    KINORA_OT_unload_fds_smoke,
]


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    smoke_core.clear()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
