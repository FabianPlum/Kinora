"""Streaming state management for real-time trajectory playback."""

import sqlite3
from array import array
from typing import Any

import bpy

from ..io.sqlite_reader import query_frame_positions

FrameData = dict[int, list[tuple[int, float, float]]]

STREAM_STATE: dict[str, Any] = {
    "db_path": None,
    "conn": None,
    "cursor": None,
    "min_frame": 0,
    "max_frame": 0,
    "frame_step": 1,
    "fps": None,  # trajectory frames per second (sim-time clock for cross-module sync)
    "agent_ids": [],
    "id_to_index": {},
    "mode": None,  # "default" or "big"
    "objects": [],
    "object_name": None,
    "handler_installed": False,
    "frame_data": None,  # dict: frame_number -> list of (id, x, y) tuples (HDF5 mode)
    "visible_indices": set(),  # indices currently shown (default mode); avoids per-frame hide churn
    "color_frame_data": None,  # dict: frame_number -> {agent_id: scalar in [0,1]} (agent colouring)
    "agent_color_enabled": False,  # write per-agent obj.color each frame (default mode only)
}


def _current_data_frame(scene: bpy.types.Scene) -> int | None:
    """Map the current Blender frame to a data frame, or None if out of range.

    Mirrors the Blender-frame -> data-frame mapping used for positions so colour
    and overlay lookups stay aligned with the streamed agents.
    """
    state = STREAM_STATE
    if not state["agent_ids"]:
        return None
    blender_frame = scene.frame_current
    step = state["frame_step"]
    if step <= 1:
        db_frame = blender_frame
    else:
        db_frame = state["min_frame"] + (blender_frame - scene.frame_start) * step
    if db_frame < state["min_frame"] or db_frame > state["max_frame"]:
        return None
    return db_frame


def current_sim_time(scene: bpy.types.Scene) -> float | None:
    """Trajectory simulation time (seconds) at the current Blender frame, or None.

    The loaded trajectory defines the timeline's clock; other modules (the FDS
    smoke sequence) sync to it. Unlike :func:`_current_data_frame` this clamps
    to the trajectory's time range instead of returning None outside it, so a
    consumer holds the first/last state rather than cutting out. Returns None
    only when no trajectory is loaded or its fps is unknown.
    """
    state = STREAM_STATE
    if not state["agent_ids"] or not state["fps"]:
        return None
    step = state["frame_step"]
    if step <= 1:
        db_frame = scene.frame_current
    else:
        db_frame = state["min_frame"] + (scene.frame_current - scene.frame_start) * step
    db_frame = max(state["min_frame"], min(db_frame, state["max_frame"]))
    return db_frame / state["fps"]


def stream_frame_handler(scene: bpy.types.Scene) -> None:
    """Stream positions from SQLite or in-memory HDF5 data for the current frame."""
    state = STREAM_STATE
    if not state["agent_ids"]:
        return
    if state["db_path"] is None and state["frame_data"] is None:
        return
    db_frame = _current_data_frame(scene)
    if db_frame is None:
        return

    if state["frame_data"] is not None:
        rows = state["frame_data"].get(db_frame, [])
    else:
        if state["conn"] is None:
            state["conn"] = sqlite3.connect(state["db_path"], isolation_level=None)
            state["cursor"] = state["conn"].cursor()
        rows = query_frame_positions(state["cursor"], db_frame)

    if state["mode"] == "big":
        obj = bpy.data.objects.get(state["object_name"])
        if not obj or obj.type != "MESH":
            return
        total = len(state["agent_ids"])
        hide_z = -1.0e6
        coords = array("f", [0.0] * (total * 3))
        for i in range(2, len(coords), 3):
            coords[i] = hide_z
        for agent_id, x, y in rows:
            idx = state["id_to_index"].get(agent_id)
            if idx is None:
                continue
            base = idx * 3
            coords[base] = float(x)
            coords[base + 1] = float(y)
            coords[base + 2] = 0.5
        obj.data.vertices.foreach_set("co", coords)
        obj.data.update()
        return

    # Default mode: update agent objects directly.  Writing hide_viewport/
    # hide_render on every agent each frame forces a full depsgraph rebuild and
    # viewport redraw, which dominates playback cost.  Only write visibility
    # flags for agents whose presence actually changed since the last frame.
    objects = state["objects"]
    visible = state["visible_indices"]
    new_visible = set()
    colors = None
    if state["agent_color_enabled"] and state["color_frame_data"] is not None:
        colors = state["color_frame_data"].get(db_frame)
    for agent_id, x, y in rows:
        idx = state["id_to_index"].get(agent_id)
        if idx is None:
            continue
        obj = objects[idx]
        obj.location = (float(x), float(y), 0.5)
        if colors is not None:
            value = colors.get(agent_id)
            if value is not None:
                obj.color = (value, value, value, 1.0)
        if idx not in visible:
            obj.hide_viewport = False
            obj.hide_render = False
        new_visible.add(idx)
    for idx in visible - new_visible:
        obj = objects[idx]
        obj.hide_viewport = True
        obj.hide_render = True
    state["visible_indices"] = new_visible


def apply_agent_colors_now(scene: bpy.types.Scene) -> None:
    """Colour all agents for the current frame without a frame change.

    Used when colouring is toggled on (or first enabled at load) so the agents
    pick up their colours immediately; ongoing playback is handled inside
    :func:`stream_frame_handler`.  No-op outside default mode or when colouring
    is disabled or unavailable.
    """
    state = STREAM_STATE
    if not state["agent_color_enabled"] or state["color_frame_data"] is None:
        return
    if state["mode"] != "default":
        return
    db_frame = _current_data_frame(scene)
    if db_frame is None:
        return
    colors = state["color_frame_data"].get(db_frame)
    if not colors:
        return
    objects = state["objects"]
    for idx, agent_id in enumerate(state["agent_ids"]):
        value = colors.get(agent_id)
        if value is None:
            continue
        objects[idx].color = (value, value, value, 1.0)


def start_streaming(
    db_path: str | None,
    agent_ids: list[int],
    min_frame: int,
    max_frame: int,
    frame_step: int,
    mode: str,
    objects: list[bpy.types.Object] | None = None,
    object_name: str | None = None,
    frame_data: FrameData | None = None,
    color_frame_data: dict[int, dict[int, float]] | None = None,
    fps: float | None = None,
) -> None:
    """Register the frame-change handler and populate streaming state."""
    STREAM_STATE["fps"] = fps
    STREAM_STATE["db_path"] = db_path
    STREAM_STATE["frame_data"] = frame_data
    STREAM_STATE["color_frame_data"] = color_frame_data
    STREAM_STATE["min_frame"] = min_frame
    STREAM_STATE["max_frame"] = max_frame
    STREAM_STATE["frame_step"] = frame_step
    STREAM_STATE["agent_ids"] = list(agent_ids)
    STREAM_STATE["id_to_index"] = {agent_id: idx for idx, agent_id in enumerate(agent_ids)}
    STREAM_STATE["mode"] = mode
    STREAM_STATE["objects"] = objects or []
    STREAM_STATE["object_name"] = object_name
    # Agents are created hidden, so no index is visible yet.
    STREAM_STATE["visible_indices"] = set()
    if not STREAM_STATE["handler_installed"]:
        bpy.app.handlers.frame_change_pre.append(stream_frame_handler)
        STREAM_STATE["handler_installed"] = True


def clear_stream_state() -> None:
    """Remove frame handlers and clear streaming buffers."""
    if STREAM_STATE["handler_installed"]:
        if stream_frame_handler in bpy.app.handlers.frame_change_pre:
            bpy.app.handlers.frame_change_pre.remove(stream_frame_handler)
    if STREAM_STATE["conn"] is not None:
        STREAM_STATE["conn"].close()
    STREAM_STATE["db_path"] = None
    STREAM_STATE["conn"] = None
    STREAM_STATE["cursor"] = None
    STREAM_STATE["frame_data"] = None
    STREAM_STATE["color_frame_data"] = None
    STREAM_STATE["agent_color_enabled"] = False
    STREAM_STATE["min_frame"] = 0
    STREAM_STATE["max_frame"] = 0
    STREAM_STATE["frame_step"] = 1
    STREAM_STATE["fps"] = None
    STREAM_STATE["agent_ids"] = []
    STREAM_STATE["id_to_index"] = {}
    STREAM_STATE["mode"] = None
    STREAM_STATE["objects"] = []
    STREAM_STATE["object_name"] = None
    STREAM_STATE["visible_indices"] = set()
    STREAM_STATE["handler_installed"] = False
