"""FDS fire & smoke volume: one multi-grid VDB sequence, physically-based look.

Each selected (temporally-strided) FDS timestep is written as one multi-grid
``.vdb`` file (see ``io.fds_reader.build_fire_smoke_sequence``): a ``density``
grid carrying Smokeview's Beer-Lambert extinction coefficient (soot density x
8700 m2/kg) and an optional ``temperature`` grid carrying the normalised flame
quantity (typically HRRPUV). One Blender Volume object per FDS mesh holds
both grids, so a single stock-Principled-Volume material renders smoke and
blackbody fire together - and the flame correctly illuminates its own smoke
(separate overlapping volume objects would double-scatter instead).

Blender's native Volume ``is_sequence`` playback - the same mechanism real
fluid-simulation caches use - handles animation on its own; no custom
frame-change handler. Appearance parameters (smoke thickness, detail noise,
flame temperature/intensity) are live shader inputs, never baked into the
files, so retuning them costs nothing.
"""

import bpy

from ..io import fds_reader
from . import shading, vdb_volume

SMOKE_COLLECTION = "Kinora_Smoke"
SMOKE_OBJECT_PREFIX = "Kinora_Smoke_"
SMOKE_MATERIAL_NAME = "Kinora_Smoke_Material"
SMOKE_VOLUME_NODE = "Kinora_Smoke_Volume"
SMOKE_THICKNESS_NODE = "Kinora_Smoke_Thickness"
SMOKE_DETAIL_NODE = "Kinora_Smoke_Detail"
FLAME_TEMP_NODE = "Kinora_Flame_Temperature"
FLAME_INTENSITY_NODE = "Kinora_Flame_Intensity"

_STATE = {
    "sequence_dir": None,  # scratch dir holding the written .vdb sequence
    "mesh_ids": None,  # list of submesh ids, one Volume object each
    "frame_count": 0,  # number of sequence files per mesh
    "active": False,
}


def _object_name(mesh_id):
    return f"{SMOKE_OBJECT_PREFIX}{mesh_id}"


def set_vdb_sequence(sequence_dir, mesh_ids, frame_count):
    """Store a freshly-written VDB sequence; called once after the load operator finishes.

    Removes the previous sequence's Volume objects and scratch directory first
    (if any), since this replaces them.
    """
    vdb_volume.remove_volume_objects(_STATE["mesh_ids"], _object_name)
    fds_reader.cleanup_vdb_sequence(_STATE["sequence_dir"])
    _STATE["sequence_dir"] = sequence_dir
    _STATE["mesh_ids"] = mesh_ids
    _STATE["frame_count"] = frame_count


def _build_material(props):
    material = bpy.data.materials.get(SMOKE_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(SMOKE_MATERIAL_NAME)
    _volume, thickness, detail_range, temp_mult, intensity_mult = shading.build_fire_smoke_material(
        material,
        SMOKE_VOLUME_NODE,
        SMOKE_THICKNESS_NODE,
        SMOKE_DETAIL_NODE,
        FLAME_TEMP_NODE,
        FLAME_INTENSITY_NODE,
    )
    _apply_appearance(props, thickness, detail_range, temp_mult, intensity_mult)
    return material


def _apply_appearance(props, thickness, detail_range, temp_mult, intensity_mult):
    thickness.inputs[1].default_value = props.fds_smoke_thickness
    shading.set_detail_amount(detail_range, props.fds_detail_amount)
    temp_mult.inputs[1].default_value = props.fds_flame_temperature
    intensity_mult.inputs[1].default_value = props.fds_flame_intensity


def _hide_all():
    for mesh_id in _STATE["mesh_ids"] or []:
        obj = bpy.data.objects.get(_object_name(mesh_id))
        if obj is not None:
            obj.hide_viewport = True
            obj.hide_render = True


def refresh(context):
    """Show/hide and (re)configure the volume(s) according to current properties.

    Safe to call any time: no sequence loaded, or the toggle off, simply hides
    the volume objects. The sequence itself is immutable once written; only
    the material and each Volume's playback alignment are (re)applied.
    """
    props = context.scene.kinora_props
    if not props.show_fds_smoke or not _STATE["mesh_ids"]:
        _STATE["active"] = False
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


def update_appearance(context):
    """Live thickness/detail/flame-temperature/flame-intensity/frame-offset update.

    All of these are shader (or simple Volume datablock) parameters, not data
    baked into the sequence files, so this never re-reads or rewrites anything.
    """
    props = context.scene.kinora_props

    material = bpy.data.materials.get(SMOKE_MATERIAL_NAME)
    if material is not None and material.use_nodes:
        nodes = material.node_tree.nodes
        thickness = nodes.get(SMOKE_THICKNESS_NODE)
        detail_range = nodes.get(SMOKE_DETAIL_NODE)
        temp_mult = nodes.get(FLAME_TEMP_NODE)
        intensity_mult = nodes.get(FLAME_INTENSITY_NODE)
        if None not in (thickness, detail_range, temp_mult, intensity_mult):
            _apply_appearance(props, thickness, detail_range, temp_mult, intensity_mult)

    for mesh_id in _STATE["mesh_ids"] or []:
        obj = bpy.data.objects.get(_object_name(mesh_id))
        if obj is not None:
            obj.data.frame_offset = props.fds_smoke_frame_offset


def clear():
    """Full teardown: Volume objects/datablocks, the collection, and the scratch sequence.

    Called by ``KINORA_OT_unload_fds_smoke`` and on addon unregister.
    """
    vdb_volume.remove_volume_objects(_STATE["mesh_ids"], _object_name)
    coll = bpy.data.collections.get(SMOKE_COLLECTION)
    if coll is not None:
        bpy.data.collections.remove(coll)
    fds_reader.cleanup_vdb_sequence(_STATE["sequence_dir"])
    _STATE.update(sequence_dir=None, mesh_ids=None, frame_count=0, active=False)
