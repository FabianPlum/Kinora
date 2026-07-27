"""FDS fire & smoke volume: one multi-grid VDB sequence, physically-based look.

Each selected (temporally-strided) FDS timestep is written as one multi-grid
``.vdb`` file (see ``io.fds_reader.build_fire_smoke_sequence``): a ``density``
grid carrying the raw soot mass density and an optional ``temperature`` grid
carrying the normalised HRRPUV flame. One Blender Volume object per FDS mesh
holds both grids, so a single material renders smoke and blackbody fire
together - and the flame correctly illuminates its own smoke (separate
overlapping volume objects would double-scatter instead).

Playback: by default Blender's native Volume ``is_sequence`` mechanism plays
one file per Blender frame. When a trajectory is loaded, that fixed rate
would only stay aligned with the agents for one specific stride/frame-step
combination - so a small frame-change handler retimes the sequence every
frame instead: it reads the trajectory's simulation clock
(``core.streaming.current_sim_time``) and shifts the Volume's ``frame_offset``
so the file whose FDS time is nearest is displayed. Any frame stride then
stays exactly in sync with any trajectory frame step.

Appearance parameters (the Beer-Lambert mass extinction coefficient,
per-channel show toggles and density multipliers, detail noise, flame
temperature) are live shader inputs, never baked into the files, so retuning
them costs nothing.
"""

import bisect

import bpy

from ..io import fds_reader
from . import shading, streaming, vdb_volume

SMOKE_COLLECTION = "Kinora_Smoke"
SMOKE_OBJECT_PREFIX = "Kinora_Smoke_"
SMOKE_MATERIAL_NAME = "Kinora_Smoke_Material"
SMOKE_VOLUME_NODE = "Kinora_Smoke_Volume"
SMOKE_SCALE_NODE = "Kinora_Smoke_Scale"
SMOKE_DETAIL_NODE = "Kinora_Smoke_Detail"
FLAME_TEMP_NODE = "Kinora_Flame_Temperature"
FLAME_INTENSITY_NODE = "Kinora_Flame_Intensity"

_STATE = {
    "sequence_dir": None,  # scratch dir holding the written .vdb sequence
    "mesh_ids": None,  # list of submesh ids, one Volume object each
    "frame_count": 0,  # number of sequence files per mesh
    "file_times": None,  # FDS simulation time (s) of each sequence file, ascending
    "active": False,
    "last_offset": None,  # last frame_offset written (skip redundant writes)
}
_handler_installed = False


def _object_name(mesh_id):
    return f"{SMOKE_OBJECT_PREFIX}{mesh_id}"


def set_vdb_sequence(sequence_dir, mesh_ids, frame_count, file_times):
    """Store a freshly-written VDB sequence; called once after the load operator finishes.

    Removes the previous sequence's Volume objects and scratch directory first
    (if any), since this replaces them.
    """
    vdb_volume.remove_volume_objects(_STATE["mesh_ids"], _object_name)
    fds_reader.cleanup_vdb_sequence(_STATE["sequence_dir"])
    _STATE["sequence_dir"] = sequence_dir
    _STATE["mesh_ids"] = mesh_ids
    _STATE["frame_count"] = frame_count
    _STATE["file_times"] = list(file_times)
    _STATE["last_offset"] = None


def _build_material(props):
    material = bpy.data.materials.get(SMOKE_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(SMOKE_MATERIAL_NAME)
    _volume, smoke_scale, detail_range, temp_mult, intensity_mult = (
        shading.build_fire_smoke_material(
            material,
            SMOKE_VOLUME_NODE,
            SMOKE_SCALE_NODE,
            SMOKE_DETAIL_NODE,
            FLAME_TEMP_NODE,
            FLAME_INTENSITY_NODE,
        )
    )
    _apply_appearance(props, smoke_scale, detail_range, temp_mult, intensity_mult)
    return material


def _apply_appearance(props, smoke_scale, detail_range, temp_mult, intensity_mult):
    # The density grid is baked at the reference mass extinction coefficient
    # (see io.fds_reader.SOOT_MASS_EXTINCTION), so the live shader factor is
    # the ratio of the user's coefficient to the reference, times the artistic
    # multiplier. A disabled channel is just its multiplier forced to 0 - both
    # channels live in one volume object, so per-channel visibility is
    # shader-side, not object-side.
    km_ratio = props.fds_mass_extinction / fds_reader.SOOT_MASS_EXTINCTION
    smoke = km_ratio * props.fds_smoke_density_multiplier
    smoke_scale.inputs[1].default_value = smoke if props.show_fds_smoke else 0.0
    shading.set_detail_amount(detail_range, props.fds_detail_amount)
    # Flame colour: the MapRange's "To Max" is the peak (core) blackbody
    # temperature; its floor is fixed in shading.FLAME_TEMP_FLOOR.
    temp_mult.inputs["To Max"].default_value = props.fds_flame_temperature
    flame = props.fds_fire_density_multiplier * shading.FLAME_EMISSION_GAIN
    intensity_mult.inputs[1].default_value = flame if props.show_fds_fire else 0.0


def _hide_all():
    for mesh_id in _STATE["mesh_ids"] or []:
        obj = bpy.data.objects.get(_object_name(mesh_id))
        if obj is not None:
            obj.hide_viewport = True
            obj.hide_render = True


def _target_offset(scene):
    """Volume ``frame_offset`` that shows the right sequence file at the current frame.

    A Volume sequence displays file ``scene_frame + frame_offset`` (1-based,
    verified empirically). When a trajectory is loaded, its simulation clock
    is the master: pick the file whose FDS time is nearest, so any frame
    stride stays in sync with any trajectory frame step. Without a trajectory
    the sequence plays natively (one file per frame). The user's frame-offset
    property shifts the evaluation point in scene frames either way.
    """
    props = scene.kinora_props
    user_offset = props.fds_smoke_frame_offset
    file_times = _STATE["file_times"]
    frame = scene.frame_current - user_offset

    sim_time = streaming.current_sim_time(scene)
    if sim_time is None or not file_times:
        return user_offset

    pos = bisect.bisect_left(file_times, sim_time)
    pos = max(0, min(pos, len(file_times) - 1))
    if pos > 0 and abs(file_times[pos - 1] - sim_time) <= abs(file_times[pos] - sim_time):
        pos -= 1
    return (pos + 1) - frame


def _sync_frame_handler(scene):
    """Retime the smoke sequence to the trajectory clock (see :func:`_target_offset`)."""
    try:
        if not _STATE["active"]:
            return
        offset = _target_offset(scene)
        if offset == _STATE["last_offset"]:
            return
        _STATE["last_offset"] = offset
        for mesh_id in _STATE["mesh_ids"] or []:
            obj = bpy.data.objects.get(_object_name(mesh_id))
            if obj is not None:
                obj.data.frame_offset = offset
    except Exception as exc:  # noqa: BLE001 - never break playback
        print(f"[Kinora] smoke sequence sync failed: {exc}")


def _install_handler():
    global _handler_installed
    if not _handler_installed:
        bpy.app.handlers.frame_change_pre.append(_sync_frame_handler)
        _handler_installed = True


def _remove_handler():
    global _handler_installed
    if _handler_installed and _sync_frame_handler in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(_sync_frame_handler)
    _handler_installed = False


def refresh(context):
    """Show/hide and (re)configure the volume(s) according to current properties.

    Safe to call any time: no sequence loaded, or both channels toggled off,
    simply hides the volume objects (the per-channel toggles themselves act on
    the shader, see :func:`_apply_appearance`). The sequence is immutable once
    written; only the material and each Volume's playback alignment are
    (re)applied.
    """
    props = context.scene.kinora_props
    any_channel = props.show_fds_smoke or props.show_fds_fire
    if not any_channel or not _STATE["mesh_ids"]:
        _STATE["active"] = False
        _remove_handler()
        _hide_all()
        return

    vdb_volume.ensure_volume_render_quality(context)
    material = _build_material(props)
    for mesh_id in _STATE["mesh_ids"]:
        obj = vdb_volume.get_or_create_volume_object(SMOKE_COLLECTION, _object_name(mesh_id))
        vdb_volume.configure_sequence(
            obj,
            _STATE["sequence_dir"],
            mesh_id,
            _STATE["frame_count"],
            props.fds_smoke_frame_offset,
        )
        obj.data.materials.clear()
        obj.data.materials.append(material)
        obj.hide_viewport = False
        obj.hide_render = False
    _STATE["active"] = True
    _STATE["last_offset"] = None
    _install_handler()
    _sync_frame_handler(context.scene)


def update_appearance(context):
    """Live update of the shader/playback parameters (extinction coefficient,
    per-channel toggles and density multipliers, detail, flame temperature,
    frame offset).

    All of these are shader (or simple Volume datablock) parameters, not data
    baked into the sequence files, so this never re-reads or rewrites anything.
    """
    props = context.scene.kinora_props

    material = bpy.data.materials.get(SMOKE_MATERIAL_NAME)
    if material is not None and material.use_nodes:
        nodes = material.node_tree.nodes
        smoke_scale = nodes.get(SMOKE_SCALE_NODE)
        detail_range = nodes.get(SMOKE_DETAIL_NODE)
        temp_mult = nodes.get(FLAME_TEMP_NODE)
        intensity_mult = nodes.get(FLAME_INTENSITY_NODE)
        if None not in (smoke_scale, detail_range, temp_mult, intensity_mult):
            _apply_appearance(props, smoke_scale, detail_range, temp_mult, intensity_mult)

    # Frame-offset changes route through the sync handler so trajectory sync
    # and the user shift compose consistently.
    _STATE["last_offset"] = None
    _sync_frame_handler(context.scene)


def clear():
    """Full teardown: Volume objects/datablocks, the collection, and the scratch sequence.

    Called by ``KINORA_OT_unload_fds_smoke`` and on addon unregister.
    """
    _remove_handler()
    vdb_volume.remove_volume_objects(_STATE["mesh_ids"], _object_name)
    coll = bpy.data.collections.get(SMOKE_COLLECTION)
    if coll is not None:
        bpy.data.collections.remove(coll)
    fds_reader.cleanup_vdb_sequence(_STATE["sequence_dir"])
    _STATE.update(
        sequence_dir=None,
        mesh_ids=None,
        frame_count=0,
        file_times=None,
        active=False,
        last_offset=None,
    )
