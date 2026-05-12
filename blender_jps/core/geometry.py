"""Blender 3D object creation for JuPedSim geometry, agents, and paths."""

from array import array

import bmesh
import bpy


def get_or_create_material(cache, name, rgba):
    """Get or create a simple material with a viewport color.

    *cache* is a plain dict used to avoid redundant lookups within a session.
    """
    if name in cache:
        return cache[name]
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
    mat.use_nodes = False
    mat.diffuse_color = rgba
    cache[name] = mat
    return mat


def assign_material(obj, material):
    """Assign a single material to an object's data."""
    if not obj or not material:
        return
    if hasattr(obj.data, "materials"):
        obj.data.materials.clear()
        obj.data.materials.append(material)


def get_or_create_collection(name):
    """Get or create a collection, clearing existing objects if it exists."""
    if name in bpy.data.collections:
        collection = bpy.data.collections[name]
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    else:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def create_geometry(context, geometry_or_levels, collection, mat_cache):
    """Create slabs + boundary curves for the walkable geometry.

    Accepts either:
    - A list of level dicts ``[{"id", "z", "polygon"}, ...]`` (v3 multi-
      floor path), or
    - A single shapely Polygon/MultiPolygon (legacy v2 path), which is
      treated as one level at z=0.

    Returns the total number of boundary curves created.
    """
    levels = _normalize_levels_arg(geometry_or_levels)

    # Compute combined bounds for the viewport clip distance + ground-plane
    # padding.
    combined_bounds = _combined_bounds(levels)
    if combined_bounds is None:
        return 0
    minx, miny, maxx, maxy = combined_bounds
    max_dim = max(maxx - minx, maxy - miny)
    if max_dim > 0:
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type == "VIEW_3D" and space.clip_end < max_dim:
                        space.clip_end = max_dim * 4

    pad_x = (maxx - minx) * 0.1
    pad_y = (maxy - miny) * 0.1

    plane_material = get_or_create_material(
        mat_cache, "JuPedSim_Ground_Plane_Material", (0.85, 0.85, 0.85, 1.0)
    )

    num_curves = 0
    for lvl in levels:
        polygon = lvl["polygon"]
        z = float(lvl["z"])
        lvl_id = lvl["id"]
        lvl_bounds = polygon.bounds  # tight bounds per level for the slab
        if not lvl_bounds:
            continue
        slab_min_x = lvl_bounds[0] - pad_x * 0.1
        slab_max_x = lvl_bounds[2] + pad_x * 0.1
        slab_min_y = lvl_bounds[1] - pad_y * 0.1
        slab_max_y = lvl_bounds[3] + pad_y * 0.1
        _create_slab(
            f"JuPedSim_Level_{lvl_id}",
            slab_min_x,
            slab_min_y,
            slab_max_x,
            slab_max_y,
            z,
            plane_material,
            collection,
        )
        for poly_part in _polygon_parts(polygon):
            num_curves += _create_curve_from_coords(
                context,
                f"Walkable_Boundary_L{lvl_id}",
                list(poly_part.exterior.coords),
                collection,
                mat_cache,
                closed=True,
                z=z,
            )
            for i, interior in enumerate(poly_part.interiors):
                num_curves += _create_curve_from_coords(
                    context,
                    f"Obstacle_L{lvl_id}_{i}",
                    list(interior.coords),
                    collection,
                    mat_cache,
                    closed=True,
                    z=z,
                )
    return num_curves


def _normalize_levels_arg(arg):
    """Coerce the legacy single-polygon input into the multi-level shape."""
    if isinstance(arg, list) and arg and isinstance(arg[0], dict) and "polygon" in arg[0]:
        return arg
    polygon = arg.polygon if hasattr(arg, "polygon") else arg
    return [{"id": 0, "z": 0.0, "polygon": polygon}]


def _polygon_parts(geom):
    """Yield each Polygon part of a Polygon or MultiPolygon."""
    if hasattr(geom, "geoms"):  # MultiPolygon
        for g in geom.geoms:
            yield g
    else:
        yield geom


def _combined_bounds(levels):
    out = None
    for lvl in levels:
        b = lvl["polygon"].bounds
        if not b:
            continue
        if out is None:
            out = list(b)
        else:
            out[0] = min(out[0], b[0])
            out[1] = min(out[1], b[1])
            out[2] = max(out[2], b[2])
            out[3] = max(out[3], b[3])
    return tuple(out) if out is not None else None


def _create_slab(name, min_x, min_y, max_x, max_y, z, material, collection):
    plane_mesh = bpy.data.meshes.new(f"{name}_Mesh")
    bm = bmesh.new()
    verts = [
        bm.verts.new((min_x, min_y, z)),
        bm.verts.new((max_x, min_y, z)),
        bm.verts.new((max_x, max_y, z)),
        bm.verts.new((min_x, max_y, z)),
    ]
    bm.faces.new(verts)
    bm.to_mesh(plane_mesh)
    bm.free()
    plane_obj = bpy.data.objects.new(name, plane_mesh)
    plane_obj.location = (0.0, 0.0, 0.0)
    assign_material(plane_obj, material)
    collection.objects.link(plane_obj)
    return plane_obj


def _create_curve_from_coords(context, name, coords, collection, mat_cache, closed=False, z=0.0):
    """Create a curve object from a list of coordinates, lifted to ``z``."""
    curve_data = bpy.data.curves.new(name=name, type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 2

    spline = curve_data.splines.new("POLY")
    spline.points.add(len(coords) - 1)

    for i, coord in enumerate(coords):
        x, y = coord[0], coord[1]
        spline.points[i].co = (x, y, z, 1.0)

    if closed:
        spline.use_cyclic_u = True

    curve_obj = bpy.data.objects.new(name, curve_data)
    curve_material = get_or_create_material(
        mat_cache, "JuPedSim_Geometry_Material", (0.2, 0.2, 0.2, 1.0)
    )
    assign_material(curve_obj, curve_material)
    collection.objects.link(curve_obj)

    curve_data.bevel_depth = context.scene.jupedsim_props.geometry_thickness
    curve_data.bevel_resolution = 2

    return 1


_shared_agent_mesh = None


def _get_shared_agent_mesh():
    """Return a shared icosphere mesh, creating it once."""
    global _shared_agent_mesh
    if _shared_agent_mesh is None or _shared_agent_mesh.name not in bpy.data.meshes:
        mesh = bpy.data.meshes.new("JuPedSim_Agent_Shared_Mesh")
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.5)
        bm.to_mesh(mesh)
        bm.free()
        mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        mesh.update()
        _shared_agent_mesh = mesh
    return _shared_agent_mesh


def create_agent(context, agent_id, collection, mat_cache):
    """Create an icosphere object for a single agent (streamed positions)."""
    mesh = _get_shared_agent_mesh()
    agent_obj = bpy.data.objects.new(f"Agent_{agent_id}", mesh)
    agent_material = get_or_create_material(
        mat_cache, "JuPedSim_Agent_Material", (0.95, 0.7, 0.1, 1.0)
    )
    assign_material(agent_obj, agent_material)
    scale = context.scene.jupedsim_props.agent_scale
    agent_obj.scale = (scale, scale, scale)

    collection.objects.link(agent_obj)

    # Initial state; positions are streamed per frame.
    agent_obj.hide_viewport = True
    agent_obj.hide_render = True


def create_agent_path(context, agent_id, coords, collection):
    """Create a curve representing the path of an agent."""
    if len(coords) < 2:
        return None

    curve_data = bpy.data.curves.new(name=f"Path_Agent_{agent_id}", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 2

    spline = curve_data.splines.new("POLY")
    spline.points.add(len(coords) - 1)

    for i, coord in enumerate(coords):
        spline.points[i].co = (*coord, 1.0)

    spline.use_cyclic_u = False

    curve_obj = bpy.data.objects.new(f"Path_Agent_{agent_id}", curve_data)
    collection.objects.link(curve_obj)

    curve_data.bevel_depth = 0.02
    curve_data.bevel_resolution = 2

    return curve_obj


def create_big_data_points(context, agent_ids, agents_collection, mat_cache):
    """Create a single mesh with particle instances driven by the frame handler."""
    if not agent_ids:
        return

    mesh = bpy.data.meshes.new("JuPedSim_Particles")
    mesh.vertices.add(len(agent_ids))
    hide_z = -1.0e6
    coords = array("f", [0.0] * (len(agent_ids) * 3))
    for i in range(2, len(coords), 3):
        coords[i] = hide_z
    mesh.vertices.foreach_set("co", coords)
    mesh.update()

    obj = bpy.data.objects.new("JuPedSim_Particles", mesh)
    obj.display_type = "WIRE"
    obj.show_in_front = True
    agents_collection.objects.link(obj)

    instance_mesh = bpy.data.meshes.new("JuPedSim_ParticleInstanceMesh")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.5)
    bm.to_mesh(instance_mesh)
    bm.free()
    instance_mesh.polygons.foreach_set("use_smooth", [True] * len(instance_mesh.polygons))
    instance_mesh.update()

    instance_obj = bpy.data.objects.new("JuPedSim_ParticleInstance", instance_mesh)
    plane_obj = bpy.data.objects.get("JuPedSim_Ground_Plane")
    if plane_obj:
        bb = plane_obj.bound_box
        center_x = (bb[0][0] + bb[6][0]) * 0.5
        center_y = (bb[0][1] + bb[6][1]) * 0.5
        instance_obj.location = (
            center_x + plane_obj.location.x,
            center_y + plane_obj.location.y,
            -1.0,
        )
    else:
        instance_obj.location = (0.0, 0.0, -1.0)
    agent_material = get_or_create_material(
        mat_cache, "JuPedSim_Agent_Material", (0.95, 0.7, 0.1, 1.0)
    )
    assign_material(instance_obj, agent_material)
    scale = context.scene.jupedsim_props.agent_scale
    instance_obj.scale = (scale, scale, scale)
    instance_obj.hide_viewport = False
    instance_obj.hide_render = False
    instance_obj.display_type = "SOLID"
    agents_collection.objects.link(instance_obj)

    ps_settings = bpy.data.particles.new("JuPedSim_Particles_Settings")
    ps_settings.type = "HAIR"
    ps_settings.count = len(agent_ids)
    ps_settings.emit_from = "VERT"
    ps_settings.use_emit_random = False
    ps_settings.render_type = "OBJECT"
    ps_settings.instance_object = instance_obj
    ps_settings.particle_size = 0.25
    ps_settings.display_method = "RENDER"
    ps_settings.display_percentage = 100

    ps_mod = obj.modifiers.new("JuPedSimParticles", type="PARTICLE_SYSTEM")
    ps_mod.particle_system.settings = ps_settings

    return obj.name


def update_path_visibility(collection, visible):
    """Update visibility of all agent path curves in a collection."""
    for obj in collection.objects:
        if obj.name.startswith("Path_Agent_"):
            obj.hide_viewport = not visible
            obj.hide_render = not visible
