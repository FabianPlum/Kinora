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
    "agent_ids": [],
    "id_to_index": {},
    "mode": None,  # "default" or "big"
    "objects": [],
    "object_name": None,
    "handler_installed": False,
    "frame_data": None,  # dict: frame_number -> list of (id, x, y) tuples (HDF5 mode)
}


def stream_frame_handler(scene: bpy.types.Scene) -> None:
    """Stream positions from SQLite or in-memory HDF5 data for the current frame."""
    state = STREAM_STATE
    if not state["agent_ids"]:
        return
    if state["db_path"] is None and state["frame_data"] is None:
        return
    blender_frame = scene.frame_current
    step = state["frame_step"]
    if step <= 1:
        db_frame = blender_frame
    else:
        db_frame = state["min_frame"] + (blender_frame - scene.frame_start) * step
    if db_frame < state["min_frame"] or db_frame > state["max_frame"]:
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

    # Default mode: update agent objects directly.
    for obj in state["objects"]:
        obj.hide_viewport = True
        obj.hide_render = True
    for agent_id, x, y in rows:
        idx = state["id_to_index"].get(agent_id)
        if idx is None:
            continue
        obj = state["objects"][idx]
        obj.location = (float(x), float(y), 0.5)
        obj.hide_viewport = False
        obj.hide_render = False


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
) -> None:
    """Register the frame-change handler and populate streaming state."""
    STREAM_STATE["db_path"] = db_path
    STREAM_STATE["frame_data"] = frame_data
    STREAM_STATE["min_frame"] = min_frame
    STREAM_STATE["max_frame"] = max_frame
    STREAM_STATE["frame_step"] = frame_step
    STREAM_STATE["agent_ids"] = list(agent_ids)
    STREAM_STATE["id_to_index"] = {agent_id: idx for idx, agent_id in enumerate(agent_ids)}
    STREAM_STATE["mode"] = mode
    STREAM_STATE["objects"] = objects or []
    STREAM_STATE["object_name"] = object_name
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
    STREAM_STATE["min_frame"] = 0
    STREAM_STATE["max_frame"] = 0
    STREAM_STATE["frame_step"] = 1
    STREAM_STATE["agent_ids"] = []
    STREAM_STATE["id_to_index"] = {}
    STREAM_STATE["mode"] = None
    STREAM_STATE["objects"] = []
    STREAM_STATE["object_name"] = None
    STREAM_STATE["handler_installed"] = False
