"""
BlenderJPS Operators
Operators for loading JuPedSim trajectory and geometry data.
"""

import os
import pathlib
import threading
import time
import traceback

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from . import install_utils
from .core import geometry as geo
from .core.streaming import clear_stream_state, start_streaming
from .io.hdf5_reader import read_simulation_data as read_hdf5_data
from .io.sqlite_reader import read_simulation_data as read_sqlite_data

ADDON_DIR = os.path.dirname(os.path.realpath(__file__))


def check_dependencies():
    """Check if required dependencies are installed."""
    import importlib.util

    install_utils.ensure_deps_in_path(ADDON_DIR)
    missing = []
    for pkg in ("shapely", "pedpy"):
        if importlib.util.find_spec(pkg) is None:
            missing.append(pkg)
    if missing:
        return False, ", ".join(missing) + " not found"
    return True, None


class JUPEDSIM_OT_select_file(Operator, ImportHelper):
    """Select a JuPedSim trajectory file (SQLite or HDF5)."""

    bl_idname = "jupedsim.select_file"
    bl_label = "Select Trajectory File"
    bl_description = "Browse for a JuPedSim trajectory file (SQLite or HDF5)"

    filter_glob: StringProperty(
        default="*.sqlite;*.db;*.h5;*.hdf5",
        options={"HIDDEN"},
    )

    def execute(self, context):
        """Store the selected trajectory file path in the scene properties."""
        context.scene.jupedsim_props.sqlite_file = self.filepath
        self.report({"INFO"}, f"Selected file: {self.filepath}")
        return {"FINISHED"}


class JUPEDSIM_OT_load_simulation(Operator):
    """Load simulation data from the selected trajectory file (SQLite or HDF5)."""

    bl_idname = "jupedsim.load_simulation"
    bl_label = "Load Simulation"
    bl_description = "Load agent trajectories and geometry from a SQLite or HDF5 file"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None
    _worker_thread = None
    _worker_done = False
    _worker_error = None
    _worker_data = None
    _worker_timings = None
    _worker_traceback = None
    _cancel_event = None
    _cancelled = False
    _timings = None
    _agent_groups = None
    _agent_index = 0
    _path_index = 0
    _frame_step = 1
    _min_frame = 0
    _max_frame = 0
    _sampled_frames = None
    _agents_collection = None
    _geometry_collection = None
    _total_agents = 0
    _stage = None
    _big_data_mode = False
    _load_full_paths = False
    _path_groups = None
    _materials = None

    def execute(self, context):
        """Start a modal load that keeps the UI responsive."""
        # Check dependencies first
        deps_ok, error = check_dependencies()
        if not deps_ok:
            self.report({"ERROR"}, f"Missing dependencies: {error}")
            self.report({"ERROR"}, "Please install dependencies in addon preferences.")
            return {"CANCELLED"}

        props = context.scene.jupedsim_props
        if props.loading_in_progress:
            self.report({"WARNING"}, "A load is already in progress")
            return {"CANCELLED"}

        # Get file path
        filepath = props.sqlite_file
        if not filepath:
            self.report({"ERROR"}, "No trajectory file selected")
            return {"CANCELLED"}

        filepath = bpy.path.abspath(filepath)
        path = pathlib.Path(filepath)

        if not path.exists():
            self.report({"ERROR"}, f"File not found: {filepath}")
            return {"CANCELLED"}

        self._reset_state()
        self._frame_step = props.frame_step
        self._big_data_mode = props.big_data_mode
        self._load_full_paths = props.load_full_paths
        self._cancel_event = threading.Event()
        props.loading_in_progress = True
        props.loading_progress = 0.0
        props.loading_message = "Starting load..."
        self._stage = "loading_data"

        # Dispatch to appropriate worker based on file extension
        ext = path.suffix.lower()
        if ext in (".h5", ".hdf5"):
            worker = self._load_hdf5_worker
        else:
            worker = self._load_sqlite_worker

        self._worker_thread = threading.Thread(
            target=worker,
            args=(path, self._frame_step, self._load_full_paths, self._cancel_event),
            daemon=True,
        )
        self._worker_thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        """Advance loading stages and update progress."""
        props = context.scene.jupedsim_props

        if event.type == "ESC":
            self._cancelled = True
            if self._cancel_event:
                self._cancel_event.set()
            props.loading_message = "Cancelling..."
            return self._finish_cancel(context)

        if event.type != "TIMER":
            return {"RUNNING_MODAL"}

        if self._cancelled:
            return self._finish_cancel(context)

        # Update progress while waiting for worker
        if self._stage == "loading_data":
            props.loading_message = "Loading trajectory data..."
            props.loading_progress = 5.0
            if self._worker_done:
                if self._worker_error:
                    self.report({"ERROR"}, f"Failed to load simulation: {self._worker_error}")
                    self._print_worker_traceback()
                    return self._finish_cancel(context)
                self._apply_worker_data(context)
                props.loading_message = "Trajectory loaded"
                props.loading_progress = 20.0
                self._stage = "create_collections"

        if self._stage == "create_collections":
            self._timed_start("create_collections")
            self._agents_collection = geo.get_or_create_collection("JuPedSim_Agents")
            self._geometry_collection = geo.get_or_create_collection("JuPedSim_Geometry")
            self._timed_end("create_collections")
            props.loading_message = "Preparing scene..."
            props.loading_progress = 10.0
            self._stage = "create_geometry"

        if self._stage == "create_geometry":
            self._timed_start("create_geometry")
            num_curves = geo.create_geometry(
                context,
                self._worker_data["geometry"],
                self._geometry_collection,
                self._materials,
            )
            self._timed_end("create_geometry")
            self.report({"INFO"}, f"Created geometry with {num_curves} boundary curves")
            props.loading_message = "Creating geometry..."
            props.loading_progress = 25.0
            self._stage = "create_big_data" if self._big_data_mode else "create_agents"

        if self._stage == "create_agents":
            props.loading_message = "Creating agents..."
            if self._step_create_agents(context):
                self._timed_end("create_agents")
                self._stage = "create_paths" if self._load_full_paths else "finalize"

        if self._stage == "create_big_data":
            props.loading_message = "Building frame buffers..."
            props.loading_progress = 60.0
            self._timed_start("create_big_data")
            obj_name = geo.create_big_data_points(
                context,
                self._agent_groups,
                self._agents_collection,
                self._materials,
            )
            self._timed_end("create_big_data")
            start_streaming(
                db_path=self._worker_data["db_path"],
                agent_ids=self._agent_groups,
                min_frame=self._min_frame,
                max_frame=self._max_frame,
                frame_step=self._frame_step,
                mode="big",
                object_name=obj_name,
                frame_data=self._worker_data.get("frame_data"),
            )
            props.loading_message = "Creating particle points..."
            props.loading_progress = 90.0
            self._stage = "finalize"

        if self._stage == "create_paths":
            props.loading_message = "Creating agent paths..."
            if self._step_create_paths(context):
                self._timed_end("create_paths")
                self._stage = "finalize"

        if self._stage == "finalize":
            self._timed_start("finalize")
            show_paths = props.show_paths
            geo.update_path_visibility(self._agents_collection, show_paths)
            if not self._big_data_mode:
                objects = []
                for agent_id in self._agent_groups:
                    obj = bpy.data.objects.get(f"Agent_{agent_id}")
                    if obj:
                        objects.append(obj)
                start_streaming(
                    db_path=self._worker_data["db_path"],
                    agent_ids=self._agent_groups,
                    min_frame=self._min_frame,
                    max_frame=self._max_frame,
                    frame_step=self._frame_step,
                    mode="default",
                    objects=objects,
                    frame_data=self._worker_data.get("frame_data"),
                )
            context.scene.frame_set(1)
            self._timed_end("finalize")
            props.loading_progress = 100.0
            props.loading_message = "Load complete"
            self._log_timings()
            self.report({"INFO"}, "Simulation loaded successfully!")
            return self._finish_success(context)

        return {"RUNNING_MODAL"}

    def _reset_state(self):
        """Reset modal state for a new load."""
        self._worker_thread = None
        self._worker_done = False
        self._worker_error = None
        self._worker_data = None
        self._worker_timings = None
        self._worker_traceback = None
        self._cancel_event = None
        self._cancelled = False
        self._timings = {}
        self._agent_groups = None
        self._agent_index = 0
        self._path_index = 0
        self._frame_step = 1
        self._min_frame = 0
        self._max_frame = 0
        self._sampled_frames = None
        self._agents_collection = None
        self._geometry_collection = None
        self._total_agents = 0
        self._stage = None
        self._big_data_mode = False
        self._load_full_paths = False
        self._path_groups = None
        self._materials = {}
        clear_stream_state()

    def _finish_success(self, context):
        """Finalize a successful load."""
        self._cleanup_timer(context)
        props = context.scene.jupedsim_props
        props.loading_in_progress = False
        return {"FINISHED"}

    def _finish_cancel(self, context):
        """Finalize a cancelled load while keeping partial data."""
        self._cleanup_timer(context)
        self._finalize_timings_on_cancel()
        self._log_timings()
        props = context.scene.jupedsim_props
        props.loading_in_progress = False
        props.loading_message = "Load cancelled (partial data kept)"
        return {"CANCELLED"}

    def _cleanup_timer(self, context):
        """Remove the modal timer."""
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _finalize_timings_on_cancel(self):
        for key, value in (self._worker_timings or {}).items():
            if key not in self._timings:
                self._timings[key] = value
        for key, value in list(self._timings.items()):
            if value < 0:
                self._timings[key] = time.perf_counter() + value
                print(f"[BlenderJPS] {key} took {self._timings[key]:.3f}s (cancelled)")

    def _load_sqlite_worker(self, path, frame_step, load_full_paths, cancel_event):
        """Run SQLite reader in a background thread."""
        try:
            data, timings = read_sqlite_data(path, frame_step, load_full_paths, cancel_event)
            self._worker_data = data
            self._worker_timings = timings
            self._worker_done = True
        except Exception as e:
            self._worker_error = str(e)
            self._worker_traceback = traceback.format_exc()
            self._worker_done = True

    def _load_hdf5_worker(self, path, frame_step, load_full_paths, cancel_event):
        """Run HDF5 reader in a background thread."""
        try:
            data, timings = read_hdf5_data(path, frame_step, load_full_paths, cancel_event)
            self._worker_data = data
            self._worker_timings = timings
            self._worker_done = True
        except Exception as e:
            self._worker_error = str(e)
            self._worker_traceback = traceback.format_exc()
            self._worker_done = True

    def _apply_worker_data(self, context):
        """Apply worker results to the modal state."""
        self._agent_groups = self._worker_data["agent_ids"]
        self._total_agents = len(self._agent_groups)
        self._min_frame = self._worker_data["min_frame"]
        self._max_frame = self._worker_data["max_frame"]
        self._sampled_frames = set()
        self._path_groups = self._worker_data.get("path_groups")
        context.scene.jupedsim_props.loaded_agent_count = self._total_agents
        step = self._frame_step
        if step > 1:
            context.scene.frame_start = 1
            context.scene.frame_end = max(1, self._max_frame // step)
        else:
            context.scene.frame_start = self._min_frame
            context.scene.frame_end = self._max_frame
        for key, value in (self._worker_timings or {}).items():
            self._timings[key] = value
        if not self._big_data_mode:
            self._timed_start("create_agents")

    def _step_create_agents(self, context):
        """Create a small batch of agent objects per tick."""
        if self._agent_groups is None:
            return True
        if self._agent_index == 0:
            self.report(
                {"INFO"},
                f"Creating {self._total_agents} agents (every {self._frame_step} frame(s))...",
            )
        chunk_size = 10
        start = self._agent_index
        end = min(self._total_agents, start + chunk_size)
        for idx in range(start, end):
            agent_id = self._agent_groups[idx]
            geo.create_agent(context, agent_id, self._agents_collection, self._materials)
        self._agent_index = end
        progress = 25.0 + (self._agent_index / max(1, self._total_agents)) * 45.0
        context.scene.jupedsim_props.loading_progress = min(progress, 70.0)
        if self._agent_index >= self._total_agents:
            return True
        return False

    def _step_create_paths(self, context):
        """Create a small batch of agent path curves per tick."""
        if not self._path_groups:
            return True
        if self._path_index == 0:
            self._timed_start("create_paths")
        chunk_size = 10
        start = self._path_index
        end = min(len(self._path_groups), start + chunk_size)
        for idx in range(start, end):
            agent_id, coords = self._path_groups[idx]
            geo.create_agent_path(context, agent_id, coords, self._agents_collection)
        self._path_index = end
        progress = 70.0 + (self._path_index / max(1, len(self._path_groups))) * 25.0
        context.scene.jupedsim_props.loading_progress = min(progress, 95.0)
        if self._path_index >= len(self._path_groups):
            return True
        return False

    def _timed_start(self, name):
        """Start a named timing section."""
        self._timings[name] = -time.perf_counter()

    def _timed_end(self, name):
        """Stop a named timing section and log it."""
        if name in self._timings:
            self._timings[name] = time.perf_counter() + self._timings[name]
            print(f"[BlenderJPS] {name} took {self._timings[name]:.3f}s")

    def _log_timings(self):
        """Print the timing summary."""
        print("[BlenderJPS] Timing summary:")
        for key, value in self._timings.items():
            print(f"  - {key}: {value:.3f}s")

    def _print_worker_traceback(self):
        """Print worker thread traceback if available."""
        trace = getattr(self, "_worker_traceback", None)
        if trace:
            print(trace)


classes = [
    JUPEDSIM_OT_select_file,
    JUPEDSIM_OT_load_simulation,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    clear_stream_state()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
