"""Shared Blender-side helpers for FDS-derived VDB volume sequences.

Object/sequence bookkeeping for ``core.smoke``'s Volume objects - one real
Blender Volume object per FDS submesh, configured to play back a scratch
``.vdb`` sequence via Blender's native ``is_sequence`` mechanism - plus the
Cycles render-quality nudge volumes need to look right.
"""

import bpy


def get_or_create_collection(name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def get_or_create_volume_object(collection_name, object_name):
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        vol_data = bpy.data.volumes.new(object_name)
        obj = bpy.data.objects.new(object_name, vol_data)
        get_or_create_collection(collection_name).objects.link(obj)
    return obj


def configure_sequence(obj, sequence_dir, mesh_id, frame_count, frame_offset):
    """Point *obj*'s Volume datablock at the scratch sequence and align its playback."""
    vol_data = obj.data
    vol_data.filepath = f"{sequence_dir}/{mesh_id}_0001.vdb"
    vol_data.is_sequence = True
    vol_data.frame_start = 1
    vol_data.frame_duration = frame_count
    vol_data.frame_offset = frame_offset


def remove_volume_objects(mesh_ids, object_name_fn):
    """Remove every Volume object/datablock named ``object_name_fn(mesh_id)``."""
    for mesh_id in mesh_ids or []:
        obj = bpy.data.objects.get(object_name_fn(mesh_id))
        if obj is None:
            continue
        vol_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if vol_data is not None and vol_data.users == 0:
            bpy.data.volumes.remove(vol_data)


def ensure_volume_render_quality(context, min_bounces=4):
    """Raise Cycles' volume light-bounce limit so smoke/fire render with multiple scattering.

    Blender's own default is 0 volume bounces - i.e. only single-scattering
    (one light bounce through the volume, then out). Real smoke/fire looks
    soft and bright because light scatters many times before escaping; at 0
    bounces it renders visibly flatter and darker regardless of density or
    colour settings. Only ever raises the value (never lowers a user's own
    higher setting) and only touches Cycles (the engine every render/verify
    pass in this addon has used; EEVEE's volume settings are a separate,
    unexplored concern). Cheap to call every refresh - a no-op once satisfied.
    """
    scene = context.scene
    if scene.render.engine != "CYCLES":
        return
    cycles = scene.cycles
    changed = False
    if cycles.volume_bounces < min_bounces:
        cycles.volume_bounces = min_bounces
        changed = True
    if cycles.max_bounces < min_bounces:
        cycles.max_bounces = min_bounces
        changed = True
    if changed:
        print(
            f"[Kinora] Raised Cycles volume/max bounces to {min_bounces} for more "
            "realistic smoke/fire rendering (Render Properties > Light Paths)"
        )
