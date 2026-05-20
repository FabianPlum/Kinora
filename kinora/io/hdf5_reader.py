"""HDF5 reading and parsing for trajectory files via pedpy."""

import pathlib
import threading
import time
from typing import Any


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
    traj = load_trajectory_from_ped_data_archive_hdf5(path)
    timings["load_trajectory_hdf5"] = time.perf_counter() - start
    if cancel_event.is_set():
        return None, timings

    start = time.perf_counter()
    walkable = load_walkable_area_from_ped_data_archive_hdf5(path)
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
    }
    return data, timings
