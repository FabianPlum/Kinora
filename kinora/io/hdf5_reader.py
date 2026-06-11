"""HDF5 reading and parsing for trajectory files via pedpy."""

import pathlib
import threading
import time
from typing import Any

# Structured fields ``position_data`` must carry to drive per-agent colouring.
_AGENT_COLOR_FIELDS = {"id", "frame", "color"}


def _is_agent_color_dataset(dataset) -> bool:
    """True if *dataset* is a structured ``position_data`` with the colour fields."""
    import h5py

    return (
        isinstance(dataset, h5py.Dataset)
        and dataset.dtype.names is not None
        and _AGENT_COLOR_FIELDS <= set(dataset.dtype.names)
    )


def probe_advanced_visualisations(path: pathlib.Path) -> dict[str, Any]:
    """Scan an HDF5 file for optional Kinora "advanced visualisation" datasets.

    Returns a manifest describing the extra, non-trajectory data a file provides:
    ``backgrounds`` (static/animated image bitmaps) and ``agent_colors`` (per-agent
    per-frame colour scalars).  Datasets that are absent or have an unexpected
    layout are simply skipped, so an ordinary trajectory file yields empty lists
    and the Advanced Visualisations UI stays hidden.

    The manifest is intentionally open-ended: each section is a list of
    self-describing option dicts so future source types slot in without changing
    the consumers.
    """
    import h5py

    backgrounds: list[dict[str, Any]] = []
    agent_colors: list[dict[str, Any]] = []
    try:
        with h5py.File(path, "r") as f:
            # Static, single-frame background bitmap.
            static = f.get("image_data")
            if isinstance(static, h5py.Dataset) and static.ndim == 2:
                backgrounds.append(
                    {
                        "id": "image_data",
                        "label": "Static image (image_data)",
                        "kind": "static",
                        "data_path": "image_data",
                        "shape": list(static.shape),
                    }
                )

            # Animated, per-frame background bitmap.
            group = f.get("image_frame_data")
            if isinstance(group, h5py.Group):
                frames = group.get("data")
                index = group.get("frame")
                if (
                    isinstance(frames, h5py.Dataset)
                    and frames.ndim == 3
                    and isinstance(index, h5py.Dataset)
                ):
                    backgrounds.append(
                        {
                            "id": "image_frame_data",
                            "label": "Animated image (image_frame_data)",
                            "kind": "animated",
                            "data_path": "image_frame_data/data",
                            "frames_path": "image_frame_data/frame",
                            "shape": list(frames.shape),
                        }
                    )

            # Per-agent per-frame scalar used to colour the agents.
            if _is_agent_color_dataset(f.get("position_data")):
                agent_colors.append(
                    {
                        "id": "position_data",
                        "label": "Agent colour (position_data)",
                    }
                )
    except Exception:
        # A malformed or unreadable file must never block trajectory loading;
        # advanced visualisations are strictly optional.
        return {"backgrounds": [], "agent_colors": []}

    return {"backgrounds": backgrounds, "agent_colors": agent_colors}


def read_agent_color_data(
    path: pathlib.Path, min_frame: int, frame_step: int
) -> dict[int, dict[int, float]] | None:
    """Read ``position_data`` into ``{data_frame: {agent_id: colour}}``.

    The scalar ``color`` field is clipped to [0, 1] (the project-wide value→colour
    contract; the addon never rescales) and the same ``frame_step`` sampling as
    the trajectory ``frame_data`` is applied, so colour lookups line up with the
    streamed positions.  Returns ``None`` when the file has no usable
    ``position_data`` so callers can simply skip agent colouring.
    """
    import h5py
    import numpy as np

    try:
        with h5py.File(path, "r") as f:
            dataset = f.get("position_data")
            if not _is_agent_color_dataset(dataset):
                return None
            rows = dataset[:]
    except Exception:
        return None

    ids = rows["id"].astype(np.int64)
    frames = rows["frame"].astype(np.int64)
    colors = np.clip(rows["color"].astype(np.float64), 0.0, 1.0)

    color_data: dict[int, dict[int, float]] = {}
    for agent_id, frame_num, color in zip(ids, frames, colors, strict=True):
        fn = int(frame_num)
        if frame_step > 1 and (fn - min_frame) % frame_step != 0:
            continue
        color_data.setdefault(fn, {})[int(agent_id)] = float(color)
    return color_data or None


def read_simulation_data(
    path: pathlib.Path,
    frame_step: int,
    load_full_paths: bool,
    cancel_event: threading.Event,
) -> tuple[dict[str, Any] | None, dict[str, float]]:
    """Read trajectory and geometry from an HDF5 file via pedpy.

    Designed to run in a worker thread.  Returns ``(data_dict, timings_dict)``
    on success.  Raises on failure.  Checks *cancel_event* between heavy
    operations so the caller can abort early.
    """
    from pedpy import (
        load_trajectory_from_ped_data_archive_hdf5,
        load_walkable_area_from_ped_data_archive_hdf5,
    )

    timings = {}
    start_total = time.perf_counter()

    start = time.perf_counter()
    traj = load_trajectory_from_ped_data_archive_hdf5(trajectory_file=path)
    timings["load_trajectory_hdf5"] = time.perf_counter() - start
    if cancel_event.is_set():
        return None, timings

    start = time.perf_counter()
    walkable = load_walkable_area_from_ped_data_archive_hdf5(trajectory_file=path)
    timings["load_walkable_area_hdf5"] = time.perf_counter() - start
    if cancel_event.is_set():
        return None, timings

    df = traj.data
    fps = traj.frame_rate

    start = time.perf_counter()
    min_frame = int(df["frame"].min())
    max_frame = int(df["frame"].max())
    agent_ids = sorted(df["id"].unique().tolist())
    timings["parse_metadata_hdf5"] = time.perf_counter() - start
    if cancel_event.is_set():
        return None, timings

    # Pre-group by frame for fast playback (only materialize frames matching frame_step)
    start = time.perf_counter()
    frame_data = {}
    for frame_num, group in df.groupby("frame"):
        fn = int(frame_num)
        if frame_step > 1 and (fn - min_frame) % frame_step != 0:
            continue
        frame_data[fn] = list(zip(group["id"], group["x"], group["y"], strict=True))
    timings["pregroup_frames_hdf5"] = time.perf_counter() - start
    if cancel_event.is_set():
        return None, timings

    # Load full paths if requested
    path_groups = None
    if load_full_paths:
        start = time.perf_counter()
        # Apply frame_step filtering once for all agents to avoid per-row checks
        if frame_step > 1:
            df_paths = df[(df["frame"] - min_frame) % frame_step == 0]
        else:
            df_paths = df

        path_groups = []
        for agent_id, agent_df in df_paths.groupby("id"):
            # Build coordinate tuples from vectorized column access
            x_vals = agent_df["x"].to_numpy()
            y_vals = agent_df["y"].to_numpy()
            coords = [(float(x), float(y), 0.0) for x, y in zip(x_vals, y_vals, strict=True)]
            path_groups.append((agent_id, coords))
        timings["load_full_paths_hdf5"] = time.perf_counter() - start

    timings["load_hdf5_total"] = time.perf_counter() - start_total

    start = time.perf_counter()
    advanced_vis = probe_advanced_visualisations(path)
    timings["probe_advanced_vis_hdf5"] = time.perf_counter() - start

    start = time.perf_counter()
    color_frame_data = None
    if advanced_vis.get("agent_colors"):
        color_frame_data = read_agent_color_data(path, min_frame, frame_step)
    timings["read_agent_colors_hdf5"] = time.perf_counter() - start

    data = {
        "geometry": walkable.polygon,
        "agent_ids": agent_ids,
        "min_frame": min_frame,
        "max_frame": max_frame,
        "fps": fps,
        "num_frames": len(frame_data),
        "db_path": None,
        "frame_data": frame_data,
        "path_groups": path_groups,
        "advanced_vis": advanced_vis,
        "color_frame_data": color_frame_data,
    }
    return data, timings
