"""Background image overlay: render a pre-computed bitmap on the ground plane.

Element 2 renders the *static* background source as a grayscale, shadeless
(emission) texture on the existing ``Kinora_Ground_Plane``.  The image is mapped
to the walkable-area bounding box via a UV layer and clipped outside it, so the
10% padding ring keeps the plane's neutral grey.

Deliberately left for later elements:
  * animated (per-frame) sources and timeline sync  (Element 3)
  * value -> colour mapping beyond grayscale          (Element 4)
"""

import json

import bpy
import numpy as np

from . import colormaps

GROUND_PLANE_NAME = "Kinora_Ground_Plane"
OVERLAY_IMAGE_NAME = "Kinora_Overlay_Image"
OVERLAY_MATERIAL_NAME = "Kinora_Ground_Overlay_Material"
BASE_MATERIAL_NAME = "Kinora_Ground_Plane_Material"
OVERLAY_UV_NAME = "Kinora_Overlay_UV"
OVERLAY_TEX_NODE = "Kinora_Overlay_Tex"
OVERLAY_RAMP_NODE = "Kinora_Overlay_Ramp"
GEO_BOUNDS_KEY = "kinora_geo_bounds"


def _selected_source(props):
    """Return the manifest entry for the chosen background, or None."""
    raw = props.adv_vis_manifest
    if not raw:
        return None
    try:
        manifest = json.loads(raw)
    except (ValueError, TypeError):
        return None
    source_id = props.image_overlay_source
    for background in manifest.get("backgrounds", []):
        if background.get("id") == source_id:
            return background
    return None


def _read_static_values(h5_path, data_path):
    """Read a 2-D background bitmap ``(rows, cols)`` from the HDF5 file."""
    import h5py

    with h5py.File(h5_path, "r") as f:
        return np.asarray(f[data_path][:], dtype=np.float32)


def _values_to_pixels(values):
    """Map a ``(rows, cols)`` scalar field to a bottom-up grayscale RGBA buffer.

    The buffer stores the raw value (R=G=B=value, alpha=1); the shader's
    ColorRamp turns value into colour, so colour-map changes need no rebuild.

    Normalisation is the upstream data processing's responsibility: values are
    assumed to be in [0, 1] and are simply clipped here (clip at 1).  No min/max
    rescaling is applied.

    The HDF5 image is stored top-down (row 0 = world y-max), so it is flipped to
    Blender's bottom-up pixel order.
    """
    norm = np.clip(values, 0.0, 1.0)
    norm = np.flipud(norm)  # top-down HDF5 -> bottom-up Blender
    height, width = norm.shape
    pixels = np.ones((height, width, 4), dtype=np.float32)
    pixels[..., 0] = norm
    pixels[..., 1] = norm
    pixels[..., 2] = norm
    return pixels


def _get_overlay_image(values):
    """Create or refresh the Blender image datablock holding the bitmap."""
    pixels = _values_to_pixels(values)
    height, width = pixels.shape[0], pixels.shape[1]
    image = bpy.data.images.get(OVERLAY_IMAGE_NAME)
    if image is not None and tuple(image.size) != (width, height):
        bpy.data.images.remove(image)
        image = None
    if image is None:
        image = bpy.data.images.new(
            OVERLAY_IMAGE_NAME, width=width, height=height, alpha=True, float_buffer=True
        )
    # Treat the stored values as raw data, not sRGB-encoded colour.
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(pixels.ravel())
    image.update()
    return image


def _build_overlay_material(image, colormap, interpolation):
    """Build the shadeless overlay material: image inside the bbox, grey outside.

    The raw image value feeds a ColorRamp (the *colormap*); the resulting colour
    drives an emission shader.  The image's clipped alpha mixes between a neutral
    grey emission (padding ring) and the bitmap emission (over the walkable
    area).  *interpolation* is the image-texture sampling mode.
    """
    material = bpy.data.materials.get(OVERLAY_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(OVERLAY_MATERIAL_NAME)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)

    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.name = OVERLAY_TEX_NODE
    tex.image = image
    tex.extension = "CLIP"  # transparent outside [0, 1] UV -> shows grey base
    tex.interpolation = interpolation
    tex.location = (-500, 0)

    ramp = tree.nodes.new("ShaderNodeValToRGB")  # ColorRamp = value -> colour
    ramp.name = OVERLAY_RAMP_NODE
    ramp.location = (-220, 0)
    colormaps.apply_colormap(ramp.color_ramp, colormap)

    image_emit = tree.nodes.new("ShaderNodeEmission")
    image_emit.location = (100, 150)

    base_emit = tree.nodes.new("ShaderNodeEmission")
    base_emit.inputs["Color"].default_value = (0.85, 0.85, 0.85, 1.0)
    base_emit.location = (100, -150)

    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.location = (350, 0)

    links = tree.links
    links.new(tex.outputs["Color"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], image_emit.inputs["Color"])
    links.new(tex.outputs["Alpha"], mix.inputs[0])  # Fac
    links.new(base_emit.outputs["Emission"], mix.inputs[1])
    links.new(image_emit.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])
    return material


def _ensure_plane_uv(plane):
    """Map the walkable-area bounding box onto image UV ``[0, 1]``.

    Padding around the geometry lands outside ``[0, 1]`` and is clipped.  Returns
    False if the plane lacks usable stored bounds.
    """
    raw = plane.get(GEO_BOUNDS_KEY)
    if raw is None:
        return False
    gx0, gy0, gx1, gy1 = (float(v) for v in raw)
    span_x = gx1 - gx0
    span_y = gy1 - gy0
    if span_x <= 0.0 or span_y <= 0.0:
        return False

    mesh = plane.data
    uv = mesh.uv_layers.get(OVERLAY_UV_NAME) or mesh.uv_layers.new(name=OVERLAY_UV_NAME)
    mesh.uv_layers.active = uv
    # The plane is created at the origin with vertices in world coordinates, so
    # local vertex positions are world positions.
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = ((co.x - gx0) / span_x, (co.y - gy0) / span_y)
    return True


def _assign_material(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)


def _restore_base(plane):
    """Reassign the plain grey ground-plane material, hiding any overlay."""
    base = bpy.data.materials.get(BASE_MATERIAL_NAME)
    if base is not None:
        _assign_material(plane, base)


def refresh(context):
    """Apply or update the background overlay according to current properties.

    Safe to call any time: no plane, overlay off, or an animated/invalid source
    simply restores the plain grey plane.  Errors reading the file are reported
    and never raised, so a bad overlay can't break interaction.
    """
    props = context.scene.kinora_props
    plane = bpy.data.objects.get(GROUND_PLANE_NAME)
    if plane is None:
        return

    if not props.show_image_overlay:
        _restore_base(plane)
        return

    source = _selected_source(props)
    h5_path = bpy.path.abspath(props.sqlite_file) if props.sqlite_file else ""
    # Animated sources arrive in Element 3; only static is rendered for now.
    if source is None or source.get("kind") != "static" or not h5_path:
        _restore_base(plane)
        return

    try:
        values = _read_static_values(h5_path, source["data_path"])
    except Exception as exc:  # noqa: BLE001 - overlay must never break loading/UI
        print(f"[Kinora] Failed to read background image: {exc}")
        _restore_base(plane)
        return

    image = _get_overlay_image(values)
    if not _ensure_plane_uv(plane):
        _restore_base(plane)
        return
    material = _build_overlay_material(
        image, props.image_overlay_colormap, props.image_overlay_interpolation
    )
    _assign_material(plane, material)


def update_appearance(context):
    """Update colour map / interpolation on the existing overlay material.

    A lightweight companion to :func:`refresh`: changes presentation only, with
    no file read or image rebuild, so the colour-scheme and interpolation enums
    update live.  No-op when the overlay material does not exist yet.
    """
    material = bpy.data.materials.get(OVERLAY_MATERIAL_NAME)
    if material is None or not material.use_nodes:
        return
    props = context.scene.kinora_props
    nodes = material.node_tree.nodes
    tex = nodes.get(OVERLAY_TEX_NODE)
    if tex is not None:
        tex.interpolation = props.image_overlay_interpolation
    ramp = nodes.get(OVERLAY_RAMP_NODE)
    if ramp is not None:
        colormaps.apply_colormap(ramp.color_ramp, props.image_overlay_colormap)
