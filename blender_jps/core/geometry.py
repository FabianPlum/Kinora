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


def create_geometry(context, geometry, collection, mat_cache):
    """Create curves from the walkable area geometry.

    Returns the number of boundary curves created.
    """
    polygon = geometry.polygon if hasattr(geometry, "polygon") else geometry

    # Ensure viewport clip distance covers the geometry extent
    bounds = polygon.bounds  # (minx, miny, maxx, maxy)
    max_dim = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    if max_dim > 0:
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    if space.type == "VIEW_3D" and space.clip_end < max_dim:
                        space.clip_end = max_dim * 4

    # Create a ground plane at origin, expanded 10% beyond geometry bounds.
    span_x = max(bounds[2] - bounds[0], 0.0)
    span_y = max(bounds[3] - bounds[1], 0.0)
    if span_x > 0.0 and span_y > 0.0:
        pad_x = span_x * 0.1
        pad_y = span_y * 0.1
        min_x = bounds[0] - pad_x
        max_x = bounds[2] + pad_x
        min_y = bounds[1] - pad_y
        max_y = bounds[3] + pad_y

        plane_mesh = bpy.data.meshes.new("JuPedSim_Ground_Plane_Mesh")
        bm = bmesh.new()
        verts = [
            bm.verts.new((min_x, min_y, 0.0)),
            bm.verts.new((max_x, min_y, 0.0)),
            bm.verts.new((max_x, max_y, 0.0)),
            bm.verts.new((min_x, max_y, 0.0)),
        ]
        bm.faces.new(verts)
        bm.to_mesh(plane_mesh)
        bm.free()
        plane_obj = bpy.data.objects.new("JuPedSim_Ground_Plane", plane_mesh)
        plane_obj.location = (0.0, 0.0, 0.0)
        plane_material = get_or_create_material(
            mat_cache, "JuPedSim_Ground_Plane_Material", (0.85, 0.85, 0.85, 1.0)
        )
        assign_material(plane_obj, plane_material)
        collection.objects.link(plane_obj)

    # Create curves for exterior boundary
    _create_curve_from_coords(
        context,
        "Walkable_Area_Boundary",
        list(polygon.exterior.coords),
        collection,
        mat_cache,
        closed=True,
    )

    # Create curves for any interior holes (obstacles)
    for i, interior in enumerate(polygon.interiors):
        _create_curve_from_coords(
            context, f"Obstacle_{i}", list(interior.coords), collection, mat_cache, closed=True
        )

    return 1 + len(list(polygon.interiors))


def _create_curve_from_coords(context, name, coords, collection, mat_cache, closed=False):
    """Create a curve object from a list of coordinates."""
    curve_data = bpy.data.curves.new(name=name, type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 2

    spline = curve_data.splines.new("POLY")
    spline.points.add(len(coords) - 1)

    for i, coord in enumerate(coords):
        x, y = coord[0], coord[1]
        spline.points[i].co = (x, y, 0.0, 1.0)

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

    return curve_obj


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
