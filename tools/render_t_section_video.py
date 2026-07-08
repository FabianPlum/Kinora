"""Render the t-section demo: FDS fire & smoke and JuPedSim agents in parallel.

One shared domain - the evacuating agents (softly emissive so they read
through the haze) walk beneath the spreading, physically-based smoke layer,
with both simulations' 300 s timelines exactly synced (1.2 s of simulation
per video frame).

Run from the repository root:

    blender --background --factory-startup --python tools/render_t_section_video.py -- stills
    blender --background --factory-startup --python tools/render_t_section_video.py -- video

``stills`` renders three preview frames to check the composition; ``video``
renders the full 250-frame MP4. Output lands in ``render_out/`` in the
repository root.
"""
# ruff: noqa: E402  (sys.path must be set up before the kinora imports)

import os
import pathlib
import sys
import threading

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
sys.path.insert(0, REPO)

import addon_utils
import bmesh
import bpy
import mathutils

addon_utils.enable("kinora", default_set=True, persistent=False)

mode = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "stills"
OUT = os.path.join(REPO, "render_out")
os.makedirs(OUT, exist_ok=True)

from kinora.core import geometry as geo
from kinora.core import smoke as smoke_core
from kinora.core.streaming import start_streaming
from kinora.io.fds_reader import build_fire_smoke_sequence, read_smoke_quantity
from kinora.io.sqlite_reader import read_simulation_data as read_sqlite

ev = threading.Event()
FRAMES = 250  # ~10 s video at 24 fps
TRAJ_STEP = 12  # 3000 sqlite frames @10fps -> 1.2 s sim per blender frame
SMOKE_STRIDE = 4  # 1001 fds steps (~0.3 s each) -> 1.2 s per file: synced timelines

for name in ("Cube", "Camera", "Light"):
    obj = bpy.data.objects.get(name)
    if obj:
        bpy.data.objects.remove(obj, do_unlink=True)

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"  # rasterizer, no ray tracing
scene.eevee.taa_render_samples = 64
scene.eevee.volumetric_tile_size = "2"  # high-res froxel volume
scene.eevee.volumetric_samples = 128
scene.eevee.use_volumetric_shadows = True
props = scene.kinora_props

# ---------- JuPedSim trajectories (native coordinates) ----------
db = pathlib.Path(REPO) / "kinora/examples/t_section_jupedsim/demo.sqlite"
traj, _ = read_sqlite(db, TRAJ_STEP, False, ev)
print("agents:", len(traj["agent_ids"]))
mat_cache = {}
geometry_coll = geo.get_or_create_collection("Kinora_Geometry")
geo.create_geometry(bpy.context, traj["geometry"], geometry_coll, mat_cache)
agents_coll = geo.get_or_create_collection("Kinora_Agents")
props.agent_scale = 0.45
for agent_id in traj["agent_ids"]:
    geo.create_agent(bpy.context, agent_id, agents_coll, mat_cache)
objects = [bpy.data.objects.get(f"Agent_{a}") for a in traj["agent_ids"]]
for obj in objects:
    obj.scale = (0.45, 0.45, 0.45)
start_streaming(
    db_path=traj["db_path"],
    agent_ids=traj["agent_ids"],
    min_frame=traj["min_frame"],
    max_frame=traj["max_frame"],
    frame_step=TRAJ_STEP,
    mode="default",
    objects=objects,
    frame_data=traj.get("frame_data"),
)
# Softly emissive agents so they ghost through the haze and only vanish in
# the thickest smoke - reads as "markers" through thin smoke.
agent_mat = bpy.data.materials.get("Kinora_Agent_Material")
if agent_mat and agent_mat.use_nodes:
    bsdf = next(n for n in agent_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Emission Color"].default_value = (1.0, 0.65, 0.1, 1.0)
    bsdf.inputs["Emission Strength"].default_value = 5.0

# ---------- FDS fire & smoke (same coordinates, on top) ----------
smv = pathlib.Path(REPO) / "kinora/examples/t_section_fds/demo.smv"
smoke_data, _ = read_smoke_quantity(smv, "SOOT DENSITY", None, ev)
flame_data, _ = read_smoke_quantity(smv, "HRRPUV", None, ev)
seq_dir, mesh_ids, frame_count = build_fire_smoke_sequence(
    smoke_data, flame_data, 1, SMOKE_STRIDE, "SMOOTH", ev
)
print("smoke frames:", frame_count)
smoke_core.set_vdb_sequence(seq_dir, mesh_ids, frame_count)
props.show_fds_smoke = True
props.show_fds_fire = True
# Semi-transparent smoke: at full physical opacity the branch plume would
# completely hide the agents walking through it - this is the built-in
# density-multiplier knob doing exactly what it is for in a combined view.
props.fds_smoke_density_multiplier = 0.1
smoke_core.refresh(bpy.context)

# ---------- environment ----------
world = scene.world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.45, 0.5, 0.58, 1.0)
bg.inputs[1].default_value = 0.7
sun_data = bpy.data.lights.new("Sun", type="SUN")
sun_data.energy = 3.5
sun_data.angle = 0.3
sun = bpy.data.objects.new("Sun", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (0.75, 0.25, 0.7)

fmesh = bpy.data.meshes.new("WorldFloor")
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=120)
bm.to_mesh(fmesh)
bm.free()
floor = bpy.data.objects.new("WorldFloor", fmesh)
floor.location = (15, 6, -0.02)
scene.collection.objects.link(floor)
fmat = bpy.data.materials.new("WorldFloorMat")
fmat.use_nodes = True
fbsdf = fmat.node_tree.nodes["Principled BSDF"]
fbsdf.inputs["Base Color"].default_value = (0.55, 0.55, 0.55, 1.0)
fbsdf.inputs["Roughness"].default_value = 0.9
fmesh.materials.append(fmat)

# ---------- camera: low 3/4 view from the south-west so agents stay visible
# beneath the ceiling smoke layer, with the fire junction in the middle ground.
cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 30
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
cam.location = (7.0, -17.0, 12.0)
direction = mathutils.Vector((18.5, 9.0, 1.0)) - cam.location
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
scene.camera = cam

# ---------- time bar overlay (parented to the camera, bottom of frame) ----------
# The camera has a 36 mm sensor: at local distance 1 the visible half-width is
# sensor/(2*lens) = 0.6, half-height 0.6 * 9/16 = 0.3375. Everything below is
# placed in those camera-local units at z = -1.
SIM_SECONDS_PER_FRAME = TRAJ_STEP / 10.0  # sqlite runs at 10 fps


def _overlay_material(name, rgba):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    emit = tree.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = rgba
    tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    return mat


def _overlay_plane(name, rgba):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    # Unit plane spanning x 0..1, y -0.5..0.5, so scale.x grows from the left edge.
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
    for v in bm.verts:
        v.co.x += 0.5
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    mesh.materials.append(_overlay_material(f"{name}_Mat", rgba))
    obj.parent = cam
    return obj


BAR_W, BAR_H, BAR_Y = 1.0, 0.012, -0.30
track = _overlay_plane("TimeBar_Track", (0.06, 0.06, 0.06, 1.0))
track.location = (-BAR_W / 2, BAR_Y, -1.0)
track.scale = (BAR_W, BAR_H, 1.0)

fill = _overlay_plane("TimeBar_Fill", (1.0, 0.55, 0.1, 1.0))
fill.location = (-BAR_W / 2, BAR_Y, -0.999)
fill.scale = (0.0, BAR_H * 0.6, 1.0)

time_curve = bpy.data.curves.new("TimeText", type="FONT")
time_curve.size = 0.035
time_text = bpy.data.objects.new("TimeText", time_curve)
scene.collection.objects.link(time_text)
time_text.parent = cam
time_text.location = (-BAR_W / 2, BAR_Y + 0.02, -1.0)
time_curve.materials.append(_overlay_material("TimeText_Mat", (0.9, 0.9, 0.9, 1.0)))


def _update_time_overlay(scene_handle):
    """Advance the fill bar and timestamp with the timeline (fires during render)."""
    frac = (scene_handle.frame_current - scene_handle.frame_start) / max(1, FRAMES - 1)
    fill.scale[0] = BAR_W * max(0.0, min(1.0, frac))
    t = (scene_handle.frame_current - scene_handle.frame_start) * SIM_SECONDS_PER_FRAME
    time_curve.body = f"t = {t:5.1f} s"


bpy.app.handlers.frame_change_pre.append(_update_time_overlay)
_update_time_overlay(scene)

# ---------- timeline / render ----------
scene.frame_start = 1
scene.frame_end = FRAMES
scene.render.fps = 24
scene.render.resolution_x = 1280
scene.render.resolution_y = 720

if mode == "stills":
    scene.render.image_settings.file_format = "PNG"
    for f in (10, 120, 245):
        scene.frame_set(f)
        scene.render.filepath = f"{OUT}/par_still_f{f}.png"
        bpy.ops.render.render(write_still=True)
        print("still", f, "done")
else:
    scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.filepath = os.path.join(OUT, "kinora_t_section_fire_evac.mp4")
    bpy.ops.render.render(animation=True)
    print("VIDEO DONE:", scene.render.filepath)
