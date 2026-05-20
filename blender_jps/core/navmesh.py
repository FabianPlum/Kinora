"""Navigation mesh + router debug visualization.

Wraps :class:`jupedsim.RoutingEngine` so we can render the triangulation
that jupedsim's router actually uses, and draw the waypoints it would
produce for an arbitrary (from, to) query.

Designed around a list of ``NavLevel`` dicts so single-floor and
multi-floor inputs share the same code path:

    NavLevel = {"id": int, "z": float, "polygon": shapely.(Multi)Polygon}

Engines are cached at module scope keyed by level id; clear them on
reload via :func:`clear_engines`.
"""

from __future__ import annotations

import math
from typing import Any

import bmesh
import bpy

from .geometry import assign_material, get_or_create_material

NAVMESH_COLLECTION = "JuPedSim_Navmesh"
ROUTE_COLLECTION = "JuPedSim_Routes"
LIVE_ROUTE_NAME = "Route_Live"
LIVE_FROM_NAME = "Route_Live_From"
LIVE_TO_NAME = "Route_Live_To"

# Sit just above the slab so wire edges are visible without z-fighting.
_NAVMESH_Z_OFFSET = 0.02
_ROUTE_Z_OFFSET = 0.05

_engines: dict[int, Any] = {}
# Single source of truth for per-level z. Both the navmesh objects and
# the route picker read from here so they can't drift apart.
_level_z: dict[int, float] = {}


def clear_engines() -> None:
    _engines.clear()
    _level_z.clear()


def level_z(level_id: int) -> float:
    """Return the z of a built level, or 0.0 if the level isn't loaded."""
    return _level_z.get(int(level_id), 0.0)


def get_engine(level_id: int) -> object | None:
    return _engines.get(level_id)


def available_levels() -> list[int]:
    return sorted(_engines.keys())


def is_routable(level_id: int, xy: tuple[float, float]) -> bool:
    engine = _engines.get(int(level_id))
    if engine is None:
        return False
    return bool(engine.is_routable(xy))


def build_routing_engines(nav_levels: list[dict]) -> dict[int, Any]:
    """Build one :class:`RoutingEngine` per level.

    Silent no-op if jupedsim isn't importable — the navmesh feature is
    optional and shouldn't break the main load path.
    """
    try:
        from jupedsim import RoutingEngine
    except ImportError:
        clear_engines()
        return {}

    clear_engines()
    for lvl in nav_levels:
        polygon = lvl.get("polygon")
        if polygon is None or polygon.is_empty:
            continue
        lvl_id = int(lvl["id"])
        try:
            _engines[lvl_id] = RoutingEngine(polygon)
            _level_z[lvl_id] = float(lvl.get("z", 0.0))
        except Exception as exc:  # noqa: BLE001 — surface but don't abort
            print(f"[BlenderJPS] navmesh: skipping level {lvl.get('id')}: {exc}")
    return dict(_engines)


def create_navmesh_objects(
    nav_levels: list[dict],
    collection: bpy.types.Collection,
    mat_cache: dict,
) -> int:
    """Materialize the cached engines as wireframe meshes, one per level."""
    if not _engines:
        return 0

    # Vivid blue — saturated enough to still read as blue once Workbench
    # studio lighting darkens the side facets of the wireframe tubes.
    color = (0.1, 0.45, 1.0, 1.0)
    material = get_or_create_material(mat_cache, "JuPedSim_Navmesh_Material", color)
    # Matte and non-metallic so the studio matcap doesn't put a white
    # specular highlight on top of the tint.
    material.metallic = 0.0
    material.roughness = 1.0
    material.specular_intensity = 0.0
    level_z = {int(lvl["id"]): float(lvl["z"]) for lvl in nav_levels}

    count = 0
    for level_id, engine in _engines.items():
        z = level_z.get(level_id, 0.0) + _NAVMESH_Z_OFFSET
        if _build_navmesh_object(level_id, engine, z, material, color, collection) is not None:
            count += 1
    return count


def _build_navmesh_object(level_id, engine, z, material, color, collection):
    try:
        verts2d, polys = engine.mesh()
    except Exception as exc:  # noqa: BLE001
        print(f"[BlenderJPS] navmesh: mesh() failed on level {level_id}: {exc}")
        return None
    if not verts2d or not polys:
        return None

    name = f"JuPedSim_Navmesh_L{level_id}"
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    verts3d = [(float(v[0]), float(v[1]), z) for v in verts2d]
    faces = [list(map(int, p)) for p in polys if len(p) >= 3]
    mesh.from_pydata(verts3d, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    assign_material(obj, material)
    # Wireframe modifier turns each edge into a thin tube so the material
    # color actually shows in solid viewport shading — display_type='WIRE'
    # would draw in the theme's wire color and look identical to the
    # geometry boundaries.
    mod = obj.modifiers.new(name="Wireframe", type="WIREFRAME")
    # Thin enough that side facets are barely visible from any angle —
    # what you see is the top facet's color, not the shaded sides.
    mod.thickness = 0.04
    mod.use_replace = True
    mod.use_even_offset = False
    # Mirror the material color into obj.color so users with viewport
    # shading set to "Object" color also see the tint.
    obj.color = color
    obj.hide_render = True
    collection.objects.link(obj)
    return obj


def _next_route_name(collection, level_id):
    """Pick the next free ``Route_L{lvl}_<n>`` suffix.

    Counting wouldn't work: after deleting a middle route Blender would
    suffix the new object with ``.001`` to avoid the collision. Scan
    existing suffixes and pick max+1 instead.
    """
    prefix = f"Route_L{level_id}_"
    highest = -1
    for obj in collection.objects:
        if not obj.name.startswith(prefix):
            continue
        tail = obj.name[len(prefix) :].split("_", 1)[0]
        try:
            n = int(tail)
        except ValueError:
            continue
        if n > highest:
            highest = n
    return f"{prefix}{highest + 1}"


def update_live_route(
    level_id: int,
    from_xy: tuple[float, float],
    to_xy: tuple[float, float],
    z: float,
    collection: bpy.types.Collection,
    mat_cache: dict,
    from_routable: bool | None = None,
) -> tuple[float | None, str | None]:
    """Replace the single live-route curve in place. Cheap to call per mousemove.

    Pass ``from_routable`` to skip re-checking ``is_routable(from_xy)``
    on the hot path — the source doesn't move during a drag.

    Returns ``(length, error)``. ``length`` is None when the query was
    rejected (out of walkable area, empty path); the curve is hidden in
    that case rather than removed, so toggling stays smooth.
    """
    engine = _engines.get(int(level_id))
    if engine is None:
        return None, f"No routing engine for level {level_id}"

    if from_routable is None:
        from_routable = bool(engine.is_routable(from_xy))
    if not from_routable or not engine.is_routable(to_xy):
        _hide_live_route()
        return None, None

    try:
        waypoints = engine.compute_waypoints(from_xy, to_xy)
    except Exception as exc:  # noqa: BLE001
        _hide_live_route()
        return None, f"compute_waypoints failed: {exc}"

    if len(waypoints) < 2:
        _hide_live_route()
        return None, None

    length = 0.0
    for a, b in zip(waypoints[:-1], waypoints[1:], strict=True):
        length += math.hypot(a[0] - b[0], a[1] - b[1])

    route_z = z + _ROUTE_Z_OFFSET
    material = get_or_create_material(mat_cache, "JuPedSim_Route_Material", (1.0, 0.05, 0.05, 1.0))
    _set_live_route_curve(waypoints, route_z, collection, material)
    _set_live_endpoint(LIVE_FROM_NAME, from_xy, route_z, collection, material)
    _set_live_endpoint(LIVE_TO_NAME, to_xy, route_z, collection, material)
    return length, None


def _set_live_route_curve(waypoints, route_z, collection, material):
    obj = bpy.data.objects.get(LIVE_ROUTE_NAME)
    if obj is None or obj.type != "CURVE":
        curve_data = bpy.data.curves.new(name=LIVE_ROUTE_NAME, type="CURVE")
        curve_data.dimensions = "3D"
        curve_data.resolution_u = 2
        curve_data.bevel_depth = 0.05
        curve_data.bevel_resolution = 2
        obj = bpy.data.objects.new(LIVE_ROUTE_NAME, curve_data)
        assign_material(obj, material)
        # Debug overlay — must not bleed into final renders.
        obj.hide_render = True
        collection.objects.link(obj)
    curve_data = obj.data
    curve_data.splines.clear()
    spline = curve_data.splines.new("POLY")
    spline.points.add(len(waypoints) - 1)
    for i, pt in enumerate(waypoints):
        spline.points[i].co = (float(pt[0]), float(pt[1]), route_z, 1.0)
    spline.use_cyclic_u = False
    obj.hide_viewport = False


def _set_live_endpoint(name, xy, z, collection, material):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        mesh = bpy.data.meshes.new(f"{name}_Mesh")
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.15)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        assign_material(obj, material)
        obj.hide_render = True
        collection.objects.link(obj)
    obj.location = (float(xy[0]), float(xy[1]), float(z))
    obj.hide_viewport = False


def _hide_live_route():
    for n in (LIVE_ROUTE_NAME, LIVE_FROM_NAME, LIVE_TO_NAME):
        obj = bpy.data.objects.get(n)
        if obj is not None:
            obj.hide_viewport = True


def remove_live_route():
    for n in (LIVE_ROUTE_NAME, LIVE_FROM_NAME, LIVE_TO_NAME):
        obj = bpy.data.objects.get(n)
        if obj is not None:
            _remove_object_with_data(obj)


def _remove_object_with_data(obj) -> None:
    """Remove an object and free its mesh/curve datablock.

    Without this the per-pick endpoint meshes and curves linger as
    orphan data and accumulate over a session.
    """
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is None or data.users:
        return
    if isinstance(data, bpy.types.Mesh):
        bpy.data.meshes.remove(data)
    elif isinstance(data, bpy.types.Curve):
        bpy.data.curves.remove(data)


def commit_live_route(level_id: int, collection: bpy.types.Collection) -> bool:
    """Promote the live route + endpoints to numbered persistent objects.

    Returns True if a live route existed and was renamed; False otherwise.
    """
    live = bpy.data.objects.get(LIVE_ROUTE_NAME)
    if live is None or live.hide_viewport:
        return False
    base = _next_route_name(collection, level_id)
    rename_map = {
        LIVE_ROUTE_NAME: base,
        LIVE_FROM_NAME: base + "_From",
        LIVE_TO_NAME: base + "_To",
    }
    for old, new in rename_map.items():
        obj = bpy.data.objects.get(old)
        if obj is None:
            continue
        obj.name = new
        if obj.data is not None:
            obj.data.name = new + "_Mesh" if obj.type == "MESH" else new
    return True


def clear_navmesh_objects(collection: bpy.types.Collection) -> int:
    """Remove all navmesh wireframe objects from a collection."""
    n = 0
    for obj in list(collection.objects):
        if obj.name.startswith("JuPedSim_Navmesh_"):
            _remove_object_with_data(obj)
            n += 1
    return n


def clear_routes(collection: bpy.types.Collection) -> int:
    """Remove all ``Route_*`` objects from a collection. Returns count."""
    n = 0
    for obj in list(collection.objects):
        if obj.name.startswith("Route_"):
            _remove_object_with_data(obj)
            n += 1
    return n


def normalize_levels_arg(geometry_or_levels) -> list[dict]:
    """Coerce single-floor or multi-floor inputs into ``list[NavLevel]``.

    Mirrors the legacy/v3 split in :mod:`blender_jps.core.geometry`:
    accepts either a list of ``{"id", "z", "polygon"}`` dicts (v3 path)
    or a single shapely geometry (legacy single-floor path).
    """
    if isinstance(geometry_or_levels, list):
        if not geometry_or_levels:
            return []
        if isinstance(geometry_or_levels[0], dict) and "polygon" in geometry_or_levels[0]:
            return geometry_or_levels
    polygon = getattr(geometry_or_levels, "polygon", geometry_or_levels)
    return [{"id": 0, "z": 0.0, "polygon": polygon}]
