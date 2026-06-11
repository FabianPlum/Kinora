"""HDF5 reading and parsing for trajectory files via pedpy."""

import pathlib
import threading
import time
from typing import Any


def probe_advanced_visualisations(path: pathlib.Path) -> dict[str, Any]:
    """Scan an HDF5 file for optional Kinora "advanced visualisation" datasets.

    Returns a manifest describing the extra, non-trajectory data a file provides
    (currently background image bitmaps).  Datasets that are absent or have an
    unexpected layout are simply skipped, so an ordinary trajectory file yields
    an empty manifest and the Advanced Visualisations UI stays hidden.

    The manifest is intentionally open-ended: ``backgrounds`` is a list of
    self-describing option dicts so future source types slot in without changing
    the consumers.
    """
    import h5py

    backgrounds: list[dict[str, Any]] = []
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
    except Exception:
        # A malformed or unreadable file must never block trajectory loading;
        # advanced visualisations are strictly optional.
        return {"backgrounds": []}

    return {"backgrounds": backgrounds}


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
    }
    return data, timings
