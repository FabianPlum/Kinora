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


def clear_engines() -> None:
    _engines.clear()


def get_engine(level_id: int) -> object | None:
    return _engines.get(level_id)


def available_levels() -> list[int]:
    return sorted(_engines.keys())


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
        try:
            _engines[int(lvl["id"])] = RoutingEngine(polygon)
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

    # Muted slate-blue: distinguishable from the dark geometry curves
    # without competing for attention.
    material = get_or_create_material(
        mat_cache, "JuPedSim_Navmesh_Material", (0.35, 0.5, 0.65, 1.0)
    )
    level_z = {int(lvl["id"]): float(lvl["z"]) for lvl in nav_levels}

    count = 0
    for level_id, engine in _engines.items():
        z = level_z.get(level_id, 0.0) + _NAVMESH_Z_OFFSET
        if _build_navmesh_object(level_id, engine, z, material, collection) is not None:
            count += 1
    return count


def _build_navmesh_object(level_id, engine, z, material, collection):
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
    mod.thickness = 0.03
    mod.use_replace = True
    mod.use_even_offset = False
    obj.hide_render = True
    collection.objects.link(obj)
    return obj


def compute_route_curve(
    level_id: int,
    from_xy: tuple[float, float],
    to_xy: tuple[float, float],
    z: float,
    collection: bpy.types.Collection,
    mat_cache: dict,
) -> tuple[object | None, float, str | None]:
    """Compute a path on the cached engine and render it as a curve.

    Returns ``(curve_obj, length, error_message)``. ``curve_obj`` is None
    when the query couldn't be served; ``error_message`` describes why.
    """
    engine = _engines.get(int(level_id))
    if engine is None:
        return None, 0.0, f"No routing engine for level {level_id}"

    if not engine.is_routable(from_xy):
        return None, 0.0, f"From point {from_xy} is not in the walkable area"
    if not engine.is_routable(to_xy):
        return None, 0.0, f"To point {to_xy} is not in the walkable area"

    try:
        waypoints = engine.compute_waypoints(from_xy, to_xy)
    except Exception as exc:  # noqa: BLE001
        return None, 0.0, f"compute_waypoints failed: {exc}"

    if len(waypoints) < 2:
        return None, 0.0, "Router returned an empty path"

    length = 0.0
    for a, b in zip(waypoints[:-1], waypoints[1:], strict=True):
        length += math.hypot(a[0] - b[0], a[1] - b[1])

    name = _next_route_name(collection, level_id)
    curve_data = bpy.data.curves.new(name=name, type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 2
    spline = curve_data.splines.new("POLY")
    spline.points.add(len(waypoints) - 1)
    route_z = z + _ROUTE_Z_OFFSET
    for i, pt in enumerate(waypoints):
        spline.points[i].co = (float(pt[0]), float(pt[1]), route_z, 1.0)
    spline.use_cyclic_u = False
    curve_data.bevel_depth = 0.05
    curve_data.bevel_resolution = 2

    material = get_or_create_material(mat_cache, "JuPedSim_Route_Material", (1.0, 0.05, 0.05, 1.0))
    curve_obj = bpy.data.objects.new(name, curve_data)
    assign_material(curve_obj, material)
    collection.objects.link(curve_obj)

    _add_route_endpoint(name + "_From", from_xy, route_z, collection, material)
    _add_route_endpoint(name + "_To", to_xy, route_z, collection, material)

    return curve_obj, length, None


def _next_route_name(collection, level_id):
    """Avoid clobbering prior routes — they're cheap and useful side-by-side."""
    n = 0
    prefix = f"Route_L{level_id}_"
    for obj in collection.objects:
        if obj.name.startswith(prefix):
            n += 1
    return f"{prefix}{n}"


def _add_route_endpoint(name, xy, z, collection, material):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.15)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (float(xy[0]), float(xy[1]), float(z))
    assign_material(obj, material)
    collection.objects.link(obj)


def update_live_route(
    level_id: int,
    from_xy: tuple[float, float],
    to_xy: tuple[float, float],
    z: float,
    collection: bpy.types.Collection,
    mat_cache: dict,
) -> tuple[float | None, str | None]:
    """Replace the single live-route curve in place. Cheap to call per mousemove.

    Returns ``(length, error)``. ``length`` is None when the query was
    rejected (out of walkable area, empty path); the curve is hidden in
    that case rather than removed, so toggling stays smooth.
    """
    engine = _engines.get(int(level_id))
    if engine is None:
        return None, f"No routing engine for level {level_id}"

    if not engine.is_routable(from_xy) or not engine.is_routable(to_xy):
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
            bpy.data.objects.remove(obj, do_unlink=True)


def clear_navmesh_objects(collection: bpy.types.Collection) -> int:
    """Remove all navmesh wireframe objects from a collection."""
    n = 0
    for obj in list(collection.objects):
        if obj.name.startswith("JuPedSim_Navmesh_"):
            bpy.data.objects.remove(obj, do_unlink=True)
            n += 1
    return n


def clear_routes(collection: bpy.types.Collection) -> int:
    """Remove all ``Route_*`` objects from a collection. Returns count."""
    n = 0
    for obj in list(collection.objects):
        if obj.name.startswith("Route_"):
            bpy.data.objects.remove(obj, do_unlink=True)
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
