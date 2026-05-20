"""
BlenderJPS Operators
Operators for loading JuPedSim trajectory and geometry data.
"""

import os
import pathlib
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

import bpy
from bpy.props import StringProperty
from bpy.types import Context, Operator
from bpy_extras.io_utils import ImportHelper

from . import install_utils
from .core import geometry as geo
from .core import navmesh as nav
from .core.streaming import clear_stream_state, start_streaming
from .io.hdf5_reader import read_simulation_data as read_hdf5_data
from .io.sqlite_reader import read_simulation_data as read_sqlite_data

ADDON_DIR = os.path.dirname(os.path.realpath(__file__))


def check_dependencies() -> tuple[bool, str | None]:
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

    _timer: bpy.types.Timer | None = None
    _worker_thread: threading.Thread | None = None
    _worker_done: bool = False
    _worker_error: str | None = None
    _worker_data: dict[str, Any] | None = None
    _worker_timings: dict[str, float] | None = None
    _worker_traceback: str | None = None
    _cancel_event: threading.Event | None = None
    _cancelled: bool = False
    _timings: dict[str, float] | None = None
    _agent_groups: list[int] | None = None
    _agent_index: int = 0
    _path_index: int = 0
    _frame_step: int = 1
    _min_frame: int = 0
    _max_frame: int = 0
    _sampled_frames: set[int] | None = None
    _agents_collection: bpy.types.Collection | None = None
    _geometry_collection: bpy.types.Collection | None = None
    _total_agents: int = 0
    _stage: str | None = None
    _big_data_mode: bool = False
    _load_full_paths: bool = False
    _path_groups: list[tuple[int, list[tuple[float, float, float]]]] | None = None
    _materials: dict[str, Any] | None = None

    def execute(self, context: Context) -> set[str]:
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

        # Dispatch to appropriate reader based on file extension
        ext = path.suffix.lower()
        reader_fn = read_hdf5_data if ext in (".h5", ".hdf5") else read_sqlite_data

        self._worker_thread = threading.Thread(
            target=self._run_reader_worker,
            args=(reader_fn, path, self._frame_step, self._load_full_paths, self._cancel_event),
            daemon=True,
        )
        self._worker_thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: bpy.types.Event) -> set[str]:
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
                if self._worker_data is None:
                    self.report({"WARNING"}, "No data returned from worker")
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
            assert self._worker_data is not None
            self._timed_start("create_geometry")
            # Explicit None-check: an empty levels list (legitimate v3
            # "no floors" case) is different from the legacy single-
            # floor geometry path. Don't let a truthy-or-fallback hide it.
            if self._worker_data.get("levels") is not None:
                geometry_arg = self._worker_data["levels"]
            else:
                geometry_arg = self._worker_data["geometry"]
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
            self._timed_start("build_navmesh")
            nav_levels = nav.normalize_levels_arg(geometry_arg)
            engines = nav.build_routing_engines(nav_levels)
            if engines:
                navmesh_collection = _get_or_link_collection(context, nav.NAVMESH_COLLECTION)
                nav.clear_navmesh_objects(navmesh_collection)
                nav.create_navmesh_objects(nav_levels, navmesh_collection, self._materials)
            self._timed_end("build_navmesh")
            if engines:
                self.report({"INFO"}, f"Built routing engines for {len(engines)} level(s)")
            elif not install_utils.is_jupedsim_installed(ADDON_DIR):
                self.report({"INFO"}, "jupedsim not installed — navmesh / route picker disabled")
            self._stage = "create_big_data" if self._big_data_mode else "create_agents"

        if self._stage == "create_agents":
            props.loading_message = "Creating agents..."
            if self._step_create_agents(context):
                self._timed_end("create_agents")
                self._stage = "create_paths" if self._load_full_paths else "finalize"

        if self._stage == "create_big_data":
            assert self._worker_data is not None
            assert self._agent_groups is not None
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
            assert self._worker_data is not None
            assert self._agent_groups is not None
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
            context.scene.frame_set(context.scene.frame_start)
            _switch_to_top_view(context)
            self._timed_end("finalize")
            props.loading_progress = 100.0
            props.loading_message = "Load complete"
            self._log_timings()
            self.report({"INFO"}, "Simulation loaded successfully!")
            return self._finish_success(context)

        return {"RUNNING_MODAL"}

    def _reset_state(self) -> None:
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
        nav.clear_engines()
        # Stale routes from a prior load reference coords that may no
        # longer match the new geometry.
        if nav.ROUTE_COLLECTION in bpy.data.collections:
            nav.clear_routes(bpy.data.collections[nav.ROUTE_COLLECTION])

    def _finish_success(self, context: Context) -> set[str]:
        """Finalize a successful load."""
        self._cleanup_timer(context)
        props = context.scene.jupedsim_props
        props.loading_in_progress = False
        return {"FINISHED"}

    def _finish_cancel(self, context: Context) -> set[str]:
        """Finalize a cancelled load while keeping partial data."""
        self._cleanup_timer(context)
        self._finalize_timings_on_cancel()
        self._log_timings()
        props = context.scene.jupedsim_props
        props.loading_in_progress = False
        props.loading_message = "Load cancelled (partial data kept)"
        return {"CANCELLED"}

    def _cleanup_timer(self, context: Context) -> None:
        """Remove the modal timer."""
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _finalize_timings_on_cancel(self) -> None:
        assert self._timings is not None
        for key, value in (self._worker_timings or {}).items():
            if key not in self._timings:
                self._timings[key] = value
        for key, value in list(self._timings.items()):
            if value < 0:
                self._timings[key] = time.perf_counter() + value
                print(f"[BlenderJPS] {key} took {self._timings[key]:.3f}s (cancelled)")

    def _run_reader_worker(
        self,
        reader_fn: Callable[
            [pathlib.Path, int, bool, threading.Event],
            tuple[dict[str, Any] | None, dict[str, float]],
        ],
        path: pathlib.Path,
        frame_step: int,
        load_full_paths: bool,
        cancel_event: threading.Event,
    ) -> None:
        """Run a reader function in a background thread."""
        try:
            data, timings = reader_fn(path, frame_step, load_full_paths, cancel_event)
            self._worker_data = data
            self._worker_timings = timings
            self._worker_done = True
        except Exception as e:
            self._worker_error = str(e)
            self._worker_traceback = traceback.format_exc()
            self._worker_done = True

    def _apply_worker_data(self, context: Context) -> None:
        """Apply worker results to the modal state."""
        assert self._worker_data is not None
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
            context.scene.frame_end = max(1, (self._max_frame - self._min_frame) // step + 1)
        else:
            context.scene.frame_start = self._min_frame
            context.scene.frame_end = self._max_frame
        assert self._timings is not None
        for key, value in (self._worker_timings or {}).items():
            self._timings[key] = value
        if not self._big_data_mode:
            self._timed_start("create_agents")

    def _step_create_agents(self, context: Context) -> bool:
        """Create a batch of agent objects per tick."""
        if self._agent_groups is None:
            return True
        if self._agent_index == 0:
            self.report(
                {"INFO"},
                f"Creating {self._total_agents} agents (every {self._frame_step} frame(s))...",
            )
        chunk_size = 100
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

    def _step_create_paths(self, context: Context) -> bool:
        """Create a batch of agent path curves per tick."""
        if not self._path_groups:
            return True
        if self._path_index == 0:
            self._timed_start("create_paths")
        chunk_size = 100
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

    def _timed_start(self, name: str) -> None:
        """Start a named timing section."""
        assert self._timings is not None
        self._timings[name] = -time.perf_counter()

    def _timed_end(self, name: str) -> None:
        """Stop a named timing section and log it."""
        assert self._timings is not None
        if name in self._timings:
            self._timings[name] = time.perf_counter() + self._timings[name]
            print(f"[BlenderJPS] {name} took {self._timings[name]:.3f}s")

    def _log_timings(self) -> None:
        """Print the timing summary."""
        if self._timings is None:
            return
        print("[BlenderJPS] Timing summary:")
        for key, value in self._timings.items():
            print(f"  - {key}: {value:.3f}s")

    def _print_worker_traceback(self) -> None:
        """Print worker thread traceback if available."""
        trace = getattr(self, "_worker_traceback", None)
        if trace:
            print(trace)


class JUPEDSIM_OT_clear_routes(Operator):
    """Remove all previously computed route curves."""

    bl_idname = "jupedsim.clear_routes"
    bl_label = "Clear Routes"
    bl_description = "Remove all Route_* objects"

    def execute(self, context: Context) -> set[str]:
        if nav.ROUTE_COLLECTION not in bpy.data.collections:
            return {"FINISHED"}
        collection = bpy.data.collections[nav.ROUTE_COLLECTION]
        n = nav.clear_routes(collection)
        self.report({"INFO"}, f"Cleared {n} route object(s)")
        return {"FINISHED"}


class JUPEDSIM_OT_pick_route(Operator):
    """Click-and-drag in the viewport to interactively query the router."""

    bl_idname = "jupedsim.pick_route"
    bl_label = "Pick Route"
    bl_description = (
        "Press to start, then LMB-drag in the viewport to query the routing engine "
        "live. Release to finalize, ESC/RMB to cancel."
    )

    _level_id: int = 0
    _z: float = 0.0
    _from_xy: tuple[float, float] | None = None
    _from_routable: bool = False
    _last_length: float | None = None
    _collection: bpy.types.Collection | None = None
    _materials: dict | None = None

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context: Context, event: bpy.types.Event) -> set[str]:
        if not nav.available_levels():
            self.report({"ERROR"}, "No routing engines built. Load a simulation first.")
            return {"CANCELLED"}
        props = context.scene.jupedsim_props
        level_id = int(props.route_level)
        if level_id not in nav.available_levels():
            level_id = nav.available_levels()[0]
        self._level_id = level_id
        self._z = _level_z_lookup(level_id)
        self._from_xy = None
        self._from_routable = False
        self._last_length = None
        self._materials = {}
        self._collection = _get_or_link_collection(context, nav.ROUTE_COLLECTION)
        nav.remove_live_route()
        context.workspace.status_text_set(
            "Route picker: LMB to set From, drag to query, release to finalize, ESC/RMB to cancel"
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: bpy.types.Event) -> set[str]:
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            return self._end(context, cancel=True)

        # Only act on mouse events that originated in a 3D viewport.
        # Without this guard, an LMB press in the outliner / properties
        # panel would feed a non-VIEW_3D RegionView3D into the screen-to-
        # world projection and yield a junk pick.
        in_view3d = context.area is not None and context.area.type == "VIEW_3D"

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if not in_view3d:
                return {"PASS_THROUGH"}
            xy = self._screen_to_world(context, event)
            if xy is None:
                return {"RUNNING_MODAL"}
            self._from_xy = xy
            self._from_routable = nav.is_routable(self._level_id, xy)
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._from_xy is not None:
            if not in_view3d:
                return {"PASS_THROUGH"}
            xy = self._screen_to_world(context, event)
            if xy is None:
                return {"RUNNING_MODAL"}
            length, err = nav.update_live_route(
                self._level_id,
                self._from_xy,
                xy,
                self._z,
                self._collection,
                self._materials,
                from_routable=self._from_routable,
            )
            self._last_length = length
            msg = (
                f"Route length: {length:.2f} m" if length is not None else "(out of walkable area)"
            )
            if err:
                msg = err
            context.workspace.status_text_set(f"Route picker — {msg}")
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if not in_view3d:
                return {"PASS_THROUGH"}
            return self._end(context, cancel=False)

        return {"PASS_THROUGH"}

    def _end(self, context: Context, cancel: bool) -> set[str]:
        context.workspace.status_text_set(None)
        if cancel:
            nav.remove_live_route()
            return {"CANCELLED"}
        if self._collection is not None:
            nav.commit_live_route(self._level_id, self._collection)
        return {"FINISHED"}

    def _screen_to_world(self, context: Context, event: bpy.types.Event):
        from bpy_extras import view3d_utils
        from mathutils import Vector
        from mathutils.geometry import intersect_line_plane

        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return None
        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        hit = intersect_line_plane(
            origin,
            origin + direction * 10000.0,
            Vector((0.0, 0.0, self._z)),
            Vector((0.0, 0.0, 1.0)),
        )
        if hit is None:
            return None
        return (float(hit.x), float(hit.y))


def _switch_to_top_view(context: Context) -> None:
    """Set the *active* 3D viewport to top-orthographic and frame it.

    Matches jupedsim_visualizer's default — when you load a trajectory
    you almost always want the floor plan, not the user's prior camera.
    Only touches the area the load was invoked from so multi-viewport
    workspaces (e.g. perspective camera + floor plan) survive intact.
    """
    area = context.area
    if area is None or area.type != "VIEW_3D":
        area = next(
            (a for a in context.screen.areas if a.type == "VIEW_3D"),
            None,
        )
    if area is None:
        return
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None:
        return
    with context.temp_override(window=context.window, area=area, region=region):
        bpy.ops.view3d.view_axis(type="TOP")
        bpy.ops.view3d.view_all()


def _get_or_link_collection(context: Context, name: str) -> bpy.types.Collection:
    """Return an existing collection (don't clear it) or link a new one.

    Unlike :func:`geometry.get_or_create_collection`, this preserves the
    contents — important when the navmesh/routes share the same
    collection as the floor slabs and obstacles.
    """
    coll = bpy.data.collections.get(name)
    if coll is not None:
        return coll
    coll = bpy.data.collections.new(name)
    context.scene.collection.children.link(coll)
    return coll


def _level_z_lookup(level_id: int) -> float:
    """Return the z the navmesh was built at for ``level_id``.

    Reading from the engine cache (rather than re-deriving from slab
    object locations) keeps the picking plane and the navmesh wireframe
    aligned even if someone moves a slab object after load.
    """
    return nav.level_z(level_id)


classes = [
    JUPEDSIM_OT_select_file,
    JUPEDSIM_OT_load_simulation,
    JUPEDSIM_OT_clear_routes,
    JUPEDSIM_OT_pick_route,
]


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    clear_stream_state()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
