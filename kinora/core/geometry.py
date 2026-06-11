"""Blender 3D object creation for Kinora geometry, agents, and paths."""

from array import array

import bmesh
import bpy

# Default flat colour for agents when per-agent data colouring is off.  Shared by
# the per-agent objects and the big-data particle instance, and restored by
# ``core.agent_colors`` when colouring is disabled.
AGENT_DEFAULT_RGBA = (0.95, 0.7, 0.1, 1.0)

# Default ground-plane colour when no background overlay is loaded.  A neutral
# grey (rather than near-white) so agents and their data colours stay legible on
# top; sets the material's Principled base colour (Material Preview / Rendered) as
# well as the solid-viewport colour.  The overlay restores this material when it
# is switched off.
GROUND_PLANE_DEFAULT_RGBA = (0.75, 0.75, 0.75, 1.0)

# Geometry boundary-line colour: 75% grey towards black, dark enough to read
# clearly against the grey ground plane.
GEOMETRY_LINE_RGBA = (0.25, 0.25, 0.25, 1.0)

# Agent path ribbon: a flat strip in the XY plane (paths are planar at z=0),
# lifted just above the ground so it does not z-fight, carrying a per-vertex
# colour scalar in PATH_VALUE_ATTR so segments can be data-coloured (see
# core.path_colors).
PATH_DEFAULT_RGBA = (0.1, 0.1, 0.1, 1.0)
PATH_HALF_WIDTH = 0.03
PATH_Z = 0.03
PATH_VALUE_ATTR = "kinora_path_value"


def _ensure_principled(material):
    """Return *material*'s Principled BSDF, building a default node tree if absent.

    Blender 5.x materials are always node-based (``use_nodes = False`` is ignored),
    so the surface shown in Material Preview / Rendered is the Principled BSDF, not
    the legacy ``diffuse_color``.  Rebuilds a minimal Principled→Output tree when
    the material has a custom tree without a Principled node (e.g. an agent
    material previously switched to a data-colour emission shader).
    """
    material.use_nodes = True
    tree = material.node_tree
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        tree.nodes.clear()
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (300, 0)
        bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
        tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return bsdf


def set_material_color(material, rgba):
    """Set a material's flat colour for both render and viewport.

    Drives the Principled BSDF *Base Color* (Material Preview / Rendered) and the
    legacy ``diffuse_color`` (Solid viewport) so the material reads as *rgba* in
    every shading mode.
    """
    _ensure_principled(material).inputs["Base Color"].default_value = rgba
    material.diffuse_color = rgba


def get_or_create_material(cache, name, rgba):
    """Get or create a flat-coloured material (Principled base colour = *rgba*).

    *cache* is a plain dict used to avoid redundant lookups within a session.
    """
    if name in cache:
        return cache[name]
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
    set_material_color(mat, rgba)
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

        plane_mesh = bpy.data.meshes.new("Kinora_Ground_Plane_Mesh")
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
        plane_obj = bpy.data.objects.new("Kinora_Ground_Plane", plane_mesh)
        plane_obj.location = (0.0, 0.0, 0.0)
        # Record the (unpadded) walkable-area bounds so the background-image
        # overlay can UV-map a bitmap onto the exact geometry extent.
        plane_obj["kinora_geo_bounds"] = [bounds[0], bounds[1], bounds[2], bounds[3]]
        plane_material = get_or_create_material(
            mat_cache, "Kinora_Ground_Plane_Material", GROUND_PLANE_DEFAULT_RGBA
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
        mat_cache, "Kinora_Geometry_Material", GEOMETRY_LINE_RGBA
    )
    assign_material(curve_obj, curve_material)
    collection.objects.link(curve_obj)

    curve_data.bevel_depth = context.scene.kinora_props.geometry_thickness
    curve_data.bevel_resolution = 2

    return curve_obj


_shared_agent_mesh = None

# Every object / datablock Kinora creates is named with one of these.
_KINORA_COLLECTIONS = ("Kinora_Agents", "Kinora_Geometry", "Kinora_Voronoi")
_KINORA_NAME_PREFIXES = ("Agent_", "Path_Agent_", "Obstacle_", "Kinora_", "Walkable_Area_")


def _is_kinora_name(name):
    """Return True if *name* belongs to a Kinora-created object or datablock."""
    return name.startswith(_KINORA_NAME_PREFIXES)


def clear_all_kinora_artefacts():
    """Remove every Kinora object, collection and orphaned datablock from the file.

    Loading a new simulation starts from a clean slate: this deletes agents,
    geometry, the ground plane, particle objects and paths wherever they live in
    the scene (not just inside the Kinora collections), removes the Kinora
    collections, then purges any now-orphaned Kinora meshes, curves, materials
    and particle settings.
    """
    global _shared_agent_mesh

    # Objects: union of Kinora collection members and anything matching our names.
    doomed = set()
    for coll_name in _KINORA_COLLECTIONS:
        coll = bpy.data.collections.get(coll_name)
        if coll:
            doomed.update(coll.objects)
    doomed.update(obj for obj in bpy.data.objects if _is_kinora_name(obj.name))
    for obj in doomed:
        bpy.data.objects.remove(obj, do_unlink=True)

    # Remove the (now empty) Kinora collections.
    for coll_name in _KINORA_COLLECTIONS:
        coll = bpy.data.collections.get(coll_name)
        if coll:
            bpy.data.collections.remove(coll)

    # Purge Kinora datablocks orphaned by the object removals.
    for datablocks in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.particles,
        bpy.data.images,
    ):
        for block in list(datablocks):
            if block.users == 0 and _is_kinora_name(block.name):
                datablocks.remove(block)

    # The cached shared mesh may have just been purged; rebuild it on next use.
    _shared_agent_mesh = None


def _get_shared_agent_mesh():
    """Return a shared cylinder mesh, creating it once.

    The cached reference is invalidated if the mesh datablock is removed (e.g.
    the user deletes every agent and Blender purges the now-orphaned mesh),
    leaving ``_shared_agent_mesh`` pointing at a freed StructRNA.  Validate it
    defensively and rebuild when stale instead of dereferencing a dead pointer.
    """
    global _shared_agent_mesh
    try:
        valid = _shared_agent_mesh is not None and _shared_agent_mesh.name in bpy.data.meshes
    except ReferenceError:
        valid = False
    if not valid:
        mesh = bpy.data.meshes.new("Kinora_Agent_Shared_Mesh")
        bm = bmesh.new()
        # Unit cylinder: radius 0.5 and depth 1.0 keep the 1x1x1 bounding box,
        # so the agent_scale dimension scaling is unchanged from the icosphere.
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            cap_tris=False,
            segments=32,
            radius1=0.5,
            radius2=0.5,
            depth=1.0,
        )
        # Smooth the curved side faces (quads) only; keep the flat caps faceted.
        for face in bm.faces:
            face.smooth = len(face.verts) == 4
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
        _shared_agent_mesh = mesh
    return _shared_agent_mesh


def create_agent(context, agent_id, collection, mat_cache):
    """Create a cylinder object for a single agent (streamed positions)."""
    mesh = _get_shared_agent_mesh()
    agent_obj = bpy.data.objects.new(f"Agent_{agent_id}", mesh)
    agent_material = get_or_create_material(mat_cache, "Kinora_Agent_Material", AGENT_DEFAULT_RGBA)
    assign_material(agent_obj, agent_material)
    scale = context.scene.kinora_props.agent_scale
    agent_obj.scale = (scale, scale, scale)

    collection.objects.link(agent_obj)

    # Initial state; positions are streamed per frame.
    agent_obj.hide_viewport = True
    agent_obj.hide_render = True


def _ribbon_geometry(coords, half_width, z):
    """Build a flat ribbon along *coords* (XY plane, height *z*).

    Each path point gets a left/right vertex pair offset by *half_width*
    perpendicular to the local direction (averaged across adjacent segments so
    corners miter); consecutive pairs are joined by quads wound so the face
    normal points up.  Returns ``(verts, faces)``.

    Legacy curves cannot carry custom attributes (and their bevel mesh exposes
    none either), so paths are meshes: this gives per-vertex data a place to live
    for in-shader colouring.
    """
    import math

    pts = [(c[0], c[1]) for c in coords]
    n = len(pts)
    verts = []
    prev_perp = (0.0, 1.0)
    for i in range(n):
        if i == 0:
            dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
        elif i == n - 1:
            dx, dy = pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]
        else:
            ax, ay = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
            bx, by = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
            la = math.hypot(ax, ay) or 1.0
            lb = math.hypot(bx, by) or 1.0
            dx, dy = ax / la + bx / lb, ay / la + by / lb
        length = math.hypot(dx, dy)
        if length < 1e-9:
            perp = prev_perp
        else:
            perp = (-dy / length, dx / length)
            prev_perp = perp
        px, py = pts[i]
        ox, oy = perp[0] * half_width, perp[1] * half_width
        verts.append((px + ox, py + oy, z))
        verts.append((px - ox, py - oy, z))
    faces = [(2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2) for i in range(n - 1)]
    return verts, faces


def create_agent_path(context, agent_id, coords, values, collection, mat_cache):
    """Create a flat ribbon mesh for an agent's path.

    *values* is a per-point colour scalar (or None); when present it is stored as
    the per-vertex ``PATH_VALUE_ATTR`` float attribute so ``core.path_colors`` can
    colour segments through a ColorRamp.  The ribbon shares ``Kinora_Path_Material``
    with all other paths.
    """
    if len(coords) < 2:
        return None

    verts, faces = _ribbon_geometry(coords, PATH_HALF_WIDTH, PATH_Z)
    mesh = bpy.data.meshes.new(f"Path_Agent_{agent_id}")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    if values is not None:
        attr = mesh.attributes.new(PATH_VALUE_ATTR, "FLOAT", "POINT")
        flat = []
        for v in values:
            fv = 0.0 if v is None else float(v)
            flat.extend((fv, fv))  # left + right vertex of this point
        attr.data.foreach_set("value", flat)

    path_obj = bpy.data.objects.new(f"Path_Agent_{agent_id}", mesh)
    collection.objects.link(path_obj)
    assign_material(
        path_obj, get_or_create_material(mat_cache, "Kinora_Path_Material", PATH_DEFAULT_RGBA)
    )
    return path_obj


def create_big_data_points(context, agent_ids, agents_collection, mat_cache):
    """Create a single mesh with particle instances driven by the frame handler."""
    if not agent_ids:
        return

    mesh = bpy.data.meshes.new("Kinora_Particles")
    mesh.vertices.add(len(agent_ids))
    hide_z = -1.0e6
    coords = array("f", [0.0] * (len(agent_ids) * 3))
    for i in range(2, len(coords), 3):
        coords[i] = hide_z
    mesh.vertices.foreach_set("co", coords)
    mesh.update()

    obj = bpy.data.objects.new("Kinora_Particles", mesh)
    obj.display_type = "WIRE"
    obj.show_in_front = True
    agents_collection.objects.link(obj)

    instance_mesh = bpy.data.meshes.new("Kinora_ParticleInstanceMesh")
    bm = bmesh.new()
    # Low-poly unit cylinder (radius 0.5, depth 1.0) matching the icosphere bounds.
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=16,
        radius1=0.5,
        radius2=0.5,
        depth=1.0,
    )
    for face in bm.faces:
        face.smooth = len(face.verts) == 4
    bm.to_mesh(instance_mesh)
    bm.free()
    instance_mesh.update()

    instance_obj = bpy.data.objects.new("Kinora_ParticleInstance", instance_mesh)
    plane_obj = bpy.data.objects.get("Kinora_Ground_Plane")
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
    agent_material = get_or_create_material(mat_cache, "Kinora_Agent_Material", AGENT_DEFAULT_RGBA)
    assign_material(instance_obj, agent_material)
    scale = context.scene.kinora_props.agent_scale
    instance_obj.scale = (scale, scale, scale)
    instance_obj.hide_viewport = False
    instance_obj.hide_render = False
    instance_obj.display_type = "SOLID"
    agents_collection.objects.link(instance_obj)

    ps_settings = bpy.data.particles.new("Kinora_Particles_Settings")
    ps_settings.type = "HAIR"
    ps_settings.count = len(agent_ids)
    ps_settings.emit_from = "VERT"
    ps_settings.use_emit_random = False
    ps_settings.render_type = "OBJECT"
    ps_settings.instance_object = instance_obj
    # Keep instances upright. By default the particle system aligns each
    # instance's local axis to the hair/velocity direction, which lays the
    # Z-up cylinder on its side; rotation_mode "NONE" preserves the instance's
    # own orientation instead.
    ps_settings.use_rotations = True
    ps_settings.rotation_mode = "NONE"
    ps_settings.particle_size = 0.25
    ps_settings.display_method = "RENDER"
    ps_settings.display_percentage = 100

    ps_mod = obj.modifiers.new("KinoraParticles", type="PARTICLE_SYSTEM")
    ps_mod.particle_system.settings = ps_settings

    return obj.name


def update_path_visibility(collection, visible):
    """Update visibility of all agent path curves in a collection."""
    for obj in collection.objects:
        if obj.name.startswith("Path_Agent_"):
            obj.hide_viewport = not visible
            obj.hide_render = not visible
