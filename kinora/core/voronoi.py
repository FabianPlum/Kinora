"""Per-frame coloured Voronoi cells from ``polygon_data`` on their own collection.

The cells for the current frame are drawn as filled polygons in a single mesh
(object ``Kinora_Voronoi`` in its own ``Kinora_Voronoi`` collection, a separate
"layer").  A frame-change handler rebuilds the mesh each frame from the preloaded
per-frame polygon lists, kept in sync with the agents via the same Blender→data
frame mapping as ``core.streaming``.  Each cell's scalar drives a per-face
attribute that a ColorRamp turns into a shadeless emission colour.

Polygon vertex counts and counts-per-frame vary, so the mesh is rebuilt (not just
updated) each frame.  Scalars are clipped to [0, 1] upstream (the project-wide
value→colour contract).
"""

import bpy

from . import shading

VORONOI_COLLECTION = "Kinora_Voronoi"
VORONOI_NAME = "Kinora_Voronoi"
VORONOI_MATERIAL_NAME = "Kinora_Voronoi_Material"
VORONOI_RAMP_NODE = "Kinora_Voronoi_Ramp"
VORONOI_VALUE_ATTR = "kinora_voronoi_value"
VORONOI_Z = 0.01  # just above the ground plane, below the path ribbons (0.03)

# Preloaded per-frame polygons and handler state, mirroring core.overlay's _ANIM.
_STATE = {
    "frames": None,  # dict: data_frame -> [(exterior_xy, colour), ...]
    "active": False,
    "last_frame": "unset",  # sentinel distinct from any real frame / None
}
_handler_installed = False


def set_frame_data(polygon_frame_data):
    """Store the preloaded per-frame polygons (called at load); None clears."""
    _STATE["frames"] = polygon_frame_data
    _STATE["last_frame"] = "unset"


def _get_collection():
    coll = bpy.data.collections.get(VORONOI_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(VORONOI_COLLECTION)
        bpy.context.scene.collection.children.link(coll)
    return coll


def _get_object():
    obj = bpy.data.objects.get(VORONOI_NAME)
    if obj is None:
        mesh = bpy.data.meshes.new(VORONOI_NAME)
        obj = bpy.data.objects.new(VORONOI_NAME, mesh)
        _get_collection().objects.link(obj)
    return obj


def _build_material(colormap):
    """Shadeless material: per-face scalar attribute → ColorRamp → emission."""
    material = bpy.data.materials.get(VORONOI_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(VORONOI_MATERIAL_NAME)
    shading.build_attribute_emission(material, VORONOI_VALUE_ATTR, VORONOI_RAMP_NODE, colormap)
    return material


def _rebuild_mesh(mesh, polys):
    """Replace *mesh* with the given polygons and per-face colour scalars."""
    mesh.clear_geometry()
    if not polys:
        mesh.update()
        return
    verts = []
    faces = []
    face_values = []
    for exterior, color in polys:
        base = len(verts)
        verts.extend((x, y, VORONOI_Z) for x, y in exterior)
        faces.append(tuple(range(base, base + len(exterior))))
        face_values.append(color)
    mesh.from_pydata(verts, [], faces)
    attr = mesh.attributes.new(VORONOI_VALUE_ATTR, "FLOAT", "FACE")
    attr.data.foreach_set("value", face_values)
    mesh.update()


def _apply_frame(scene):
    """Rebuild the Voronoi mesh for the current frame (if it changed)."""
    if not _STATE["active"] or _STATE["frames"] is None:
        return
    from .streaming import _current_data_frame

    db_frame = _current_data_frame(scene)
    if db_frame == _STATE["last_frame"]:
        return
    _STATE["last_frame"] = db_frame
    obj = bpy.data.objects.get(VORONOI_NAME)
    if obj is None:
        return
    polys = _STATE["frames"].get(db_frame) if db_frame is not None else None
    _rebuild_mesh(obj.data, polys)
    # clear_geometry can drop material slots; keep the material assigned.
    material = bpy.data.materials.get(VORONOI_MATERIAL_NAME)
    if material is not None and not obj.data.materials:
        obj.data.materials.append(material)


def _frame_handler(scene):
    try:
        _apply_frame(scene)
    except Exception as exc:  # noqa: BLE001 - never break playback
        print(f"[Kinora] voronoi frame update failed: {exc}")


def _install_handler():
    global _handler_installed
    if not _handler_installed:
        bpy.app.handlers.frame_change_post.append(_frame_handler)
        _handler_installed = True


def _remove_handler():
    global _handler_installed
    if _handler_installed and _frame_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_handler)
    _handler_installed = False


def refresh(context):
    """Show or hide the Voronoi overlay according to the current properties.

    Safe to call any time.  Shown only when the file provides polygon data and the
    toggle is on; otherwise the handler is removed and the object hidden.  Viewport
    shading is handled by the caller (emission needs Material Preview).
    """
    props = context.scene.kinora_props
    if props.show_voronoi and _STATE["frames"]:
        obj = _get_object()
        material = _build_material(props.voronoi_colormap)
        obj.data.materials.clear()
        obj.data.materials.append(material)
        obj.hide_viewport = False
        obj.hide_render = False
        _STATE["active"] = True
        _STATE["last_frame"] = "unset"
        _install_handler()
        _apply_frame(context.scene)
    else:
        _STATE["active"] = False
        _remove_handler()
        obj = bpy.data.objects.get(VORONOI_NAME)
        if obj is not None:
            obj.hide_viewport = True
            obj.hide_render = True


def update_appearance(context):
    """Update the Voronoi colour map live.  No-op when not currently active."""
    shading.update_ramp(
        VORONOI_MATERIAL_NAME, VORONOI_RAMP_NODE, context.scene.kinora_props.voronoi_colormap
    )


def clear():
    """Tear down handler + preloaded data (load reset / unregister)."""
    _remove_handler()
    _STATE["frames"] = None
    _STATE["active"] = False
    _STATE["last_frame"] = "unset"
