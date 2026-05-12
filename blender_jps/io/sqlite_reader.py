"""SQLite reading and parsing for JuPedSim trajectory files.

Schema versions supported:
- v2 (legacy): a single ``geometry`` table; all agents implicitly on one
  flat floor at z=0.
- v3 (multi-floor): a ``levels(id, z, wkt)`` table, an optional
  ``landings(...)`` table, and a ``level`` column on
  ``trajectory_data``. Agents are placed at the elevation of their
  per-frame level.
"""

import sqlite3
import time


def _detect_version(cur):
    """Read metadata.version; returns 1 if the table is missing."""
    try:
        row = cur.execute("SELECT value FROM metadata WHERE key = 'version'").fetchone()
    except sqlite3.Error:
        return 1
    if row is None:
        return 1
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 1


def _has_column(cur, table, column):
    rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def read_simulation_data(path, frame_step, load_full_paths, cancel_event):
    """Read metadata, geometry and trajectory data from a JuPedSim SQLite file.

    Returns a dict carrying both legacy 2D fields and the new multi-floor
    fields:

    - ``geometry``: shapely union of all level polygons (back-compat path
      for the existing single-floor renderer).
    - ``levels``: list of ``{"id": int, "z": float, "polygon":
      shapely.Polygon}`` — one entry per level; on v2 files this is a
      single entry at z=0 derived from the ``geometry`` table.
    - ``landings``: list of ``{"from": int, "to": int, "polygon_from":
      shapely.Polygon, "polygon_to": shapely.Polygon}`` — empty on v2.
    - ``level_z``: ``{level_id: z}`` quick lookup.
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
        version = _detect_version(cur)
        has_level_col = version >= 3 and _has_column(cur, "trajectory_data", "level")

        start = time.perf_counter()
        levels, landings = _load_levels_and_landings(cur, version)
        geometry = shapely.union_all([lvl["polygon"] for lvl in levels])
        level_z = {lvl["id"]: lvl["z"] for lvl in levels}
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
            path_groups = _load_full_path_groups(cur, frame_step, min_frame, has_level_col, level_z)
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
            "levels": levels,
            "landings": landings,
            "level_z": level_z,
            "schema_version": version,
            "has_level_col": has_level_col,
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


def _load_levels_and_landings(cur, version):
    """Return (levels, landings). v2 files (or v3 files whose ``levels``
    table is missing/corrupt) synthesize one level at z=0 from the legacy
    ``geometry`` table when possible."""
    import shapely

    if version >= 3:
        levels = []
        try:
            for lvl_id, z, wkt in cur.execute("SELECT id, z, wkt FROM levels ORDER BY id ASC"):
                levels.append({"id": int(lvl_id), "z": float(z), "polygon": shapely.from_wkt(wkt)})
        except sqlite3.Error:
            # `levels` table missing despite version=3 — fall through to
            # the legacy path rather than aborting the whole load.
            levels = []
        landings = []
        try:
            for from_lvl, to_lvl, p_from, p_to in cur.execute(
                "SELECT from_level, to_level, polygon_from_wkt, polygon_to_wkt FROM landings"
            ):
                landings.append(
                    {
                        "from": int(from_lvl),
                        "to": int(to_lvl),
                        "polygon_from": shapely.from_wkt(p_from),
                        "polygon_to": shapely.from_wkt(p_to),
                    }
                )
        except sqlite3.Error:
            pass
        if levels:
            return levels, landings

    # v2 fallback or missing/empty v3 ``levels``: synthesize one level at
    # z=0 from the legacy ``geometry`` table. If that table is also
    # missing we hand back an empty result rather than crashing.
    try:
        res = cur.execute("SELECT wkt FROM geometry").fetchall()
    except sqlite3.Error:
        return [], []
    polygons = [shapely.from_wkt(s[0]) for s in res]
    if polygons:
        merged = shapely.union_all(polygons)
        return [{"id": 0, "z": 0.0, "polygon": merged}], []
    return [], []


def _load_full_path_groups(cursor, frame_step, min_frame, has_level_col, level_z):
    """Load full path coordinates per agent from an open cursor.

    Each emitted point is ``(x, y, z)`` — z derived from the agent's level
    for v3 files, fixed at 0 for v2.
    """
    if has_level_col:
        res = cursor.execute(
            "SELECT id, frame, pos_x, pos_y, level FROM trajectory_data ORDER BY id ASC, frame ASC"
        )
        rows = res.fetchall()
        paths = {}
        for agent_id, frame, x, y, level in rows:
            if frame_step > 1 and (frame - min_frame) % frame_step != 0:
                continue
            z = float(level_z.get(level, 0.0))
            paths.setdefault(agent_id, []).append((float(x), float(y), z))
    else:
        res = cursor.execute(
            "SELECT id, frame, pos_x, pos_y FROM trajectory_data ORDER BY id ASC, frame ASC"
        )
        paths = {}
        for agent_id, frame, x, y in res.fetchall():
            if frame_step > 1 and (frame - min_frame) % frame_step != 0:
                continue
            paths.setdefault(agent_id, []).append((float(x), float(y), 0.0))
    return [(agent_id, coords) for agent_id, coords in paths.items()]


def query_frame_positions(cursor, frame, has_level_col=False, level_z=None):
    """Query agent positions for a single frame.

    Returns a list of ``(id, pos_x, pos_y, z)`` tuples. On legacy v2 files
    z is always 0; on v3 files it is looked up from the agent's level.
    """
    if has_level_col:
        res = cursor.execute(
            "SELECT id, pos_x, pos_y, level FROM trajectory_data "
            "WHERE frame == (?) ORDER BY id ASC",
            (frame,),
        )
        lookup = level_z or {}
        return [
            (agent_id, x, y, float(lookup.get(level, 0.0)))
            for agent_id, x, y, level in res.fetchall()
        ]
    res = cursor.execute(
        "SELECT id, pos_x, pos_y FROM trajectory_data WHERE frame == (?) ORDER BY id ASC",
        (frame,),
    )
    return [(agent_id, x, y, 0.0) for agent_id, x, y in res.fetchall()]
