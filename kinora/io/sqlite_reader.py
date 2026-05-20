"""SQLite reading and parsing for pedestrian data trajectory files."""

import sqlite3
import time


def read_simulation_data(path, frame_step, load_full_paths, cancel_event):
    """Read metadata, geometry and trajectory data from a JuPedSim SQLite file.

    Designed to run in a worker thread.  Returns ``(data_dict, timings_dict)``
    on success.  Raises on failure.  Checks *cancel_event* between heavy
    queries so the caller can abort early.
    """
    import shapely

    timings = {}
    start_total = time.perf_counter()

    start = time.perf_counter()
    conn = sqlite3.connect(str(path), isolation_level=None)
    timings["open_sqlite"] = time.perf_counter() - start

    try:
        if cancel_event.is_set():
            return None, timings

        cur = conn.cursor()

        start = time.perf_counter()
        res = cur.execute("SELECT wkt FROM geometry")
        geometries = [shapely.from_wkt(s[0]) for s in res.fetchall()]
        geometry = shapely.union_all(geometries)
        timings["load_geometry_sqlite"] = time.perf_counter() - start
        if cancel_event.is_set():
            return None, timings

        start = time.perf_counter()
        res = cur.execute("SELECT MIN(frame), MAX(frame) FROM trajectory_data")
        min_frame, max_frame = res.fetchone()
        min_frame = int(min_frame) if min_frame is not None else 0
        max_frame = int(max_frame) if max_frame is not None else 0
        timings["read_frame_range"] = time.perf_counter() - start
        if cancel_event.is_set():
            return None, timings

        start = time.perf_counter()
        res = cur.execute("SELECT DISTINCT id FROM trajectory_data ORDER BY id ASC")
        agent_ids = [row[0] for row in res.fetchall()]
        timings["read_agent_ids"] = time.perf_counter() - start
        if cancel_event.is_set():
            return None, timings

        path_groups = None
        if load_full_paths:
            start = time.perf_counter()
            path_groups = _load_full_path_groups(cur, frame_step, min_frame)
            timings["load_full_paths"] = time.perf_counter() - start

        start = time.perf_counter()
        res = cur.execute("SELECT value FROM metadata WHERE key == 'fps'")
        fps = float(res.fetchone()[0])
        res = cur.execute("SELECT count(*) FROM frame_data")
        num_frames = int(res.fetchone()[0])
        timings["read_metadata"] = time.perf_counter() - start

        timings["load_sqlite_total"] = time.perf_counter() - start_total

        data = {
            "geometry": geometry,
            "agent_ids": agent_ids,
            "min_frame": min_frame,
            "max_frame": max_frame,
            "fps": fps,
            "num_frames": num_frames,
            "db_path": str(path),
            "path_groups": path_groups,
        }
        return data, timings
    finally:
        conn.close()


def _load_full_path_groups(cursor, frame_step, min_frame):
    """Load full path coordinates per agent from an open cursor."""
    res = cursor.execute(
        "SELECT id, frame, pos_x, pos_y FROM trajectory_data ORDER BY id ASC, frame ASC"
    )
    paths = {}
    for agent_id, frame, x, y in res.fetchall():
        if frame_step > 1 and (frame - min_frame) % frame_step != 0:
            continue
        paths.setdefault(agent_id, []).append((float(x), float(y), 0.0))
    return [(agent_id, coords) for agent_id, coords in paths.items()]


def query_frame_positions(cursor, frame):
    """Query agent positions for a single frame.

    *cursor* should be a reusable ``sqlite3.Cursor`` kept open for the
    lifetime of the streaming session.

    Returns a list of ``(id, pos_x, pos_y)`` tuples.
    """
    res = cursor.execute(
        "SELECT id, pos_x, pos_y FROM trajectory_data WHERE frame == (?) ORDER BY id ASC",
        (frame,),
    )
    return res.fetchall()
