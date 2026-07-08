"""FDS Smoke3D reading via ``fdsreader``.

``fdsreader`` lazy-loads per (mesh, quantity), not per timestep: touching a
``SubSmoke3D``'s ``data`` once decodes every timestep for that mesh/quantity in
one RLE pass.  There is no cheaper "just this frame" read available, so the
split here is: :func:`probe_fds_simulation`/:func:`list_available_quantities`
only ever look at cheap ``.smv``-derived metadata (mesh extents/dimensions,
quantity names/units/timestep counts), while :func:`read_smoke_quantity` is the
one heavy step that decodes a *single, explicitly selected* quantity's full
time series into memory.  Frames are then sliced out of that in-memory array
by ``core.smoke``, not re-read per frame.
"""

import pathlib
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

# FDS's default MASS_EXTINCTION_COEFFICIENT (m2/kg): Smokeview renders smoke
# opacity via the Beer-Lambert law with extinction K = Km * rho_soot, Km = 8700
# (see firemodels/fds#5118 and the FDS User's Guide). Blender's Principled
# Volume "Density" input is likewise an extinction coefficient in 1/m when the
# scene unit is metres. The density grid is baked at this *reference*
# coefficient; the shader multiplies by the live ratio Km_user / Km_reference
# (see core.smoke), so the user-editable coefficient never requires rewriting
# the sequence. Baking at the reference (values ~0-30 1/m) rather than storing
# raw soot density (~1e-5..1e-2 kg/m3) matters: Blender's volume attribute
# sampling loses very small absolute grid values (verified empirically - a
# constant 0.003 grid renders as empty at any shader multiplier and any
# clipping/precision setting, while the same optical depth baked as 26 x 0.1
# renders correctly).
SOOT_MASS_EXTINCTION = 8700.0

# The Smoke3D quantities this addon consumes, by FDS/Smokeview convention:
# soot density drives the smoke opacity, HRRPUV the flame envelope.
SMOKE_QUANTITY = "SOOT DENSITY"
FLAME_QUANTITY = "HRRPUV"

# Fraction of the smoke density carved away where the flame is at full
# strength. In the combustion zone the mixture is burning gas, not settled
# soot, and with the physically-thick Beer-Lambert extinction the flame would
# otherwise be completely buried inside its own opaque plume - the standard
# pyro-workflow fix is to suppress smoke density inside the flame envelope.
FLAME_SMOKE_CARVE = 0.75

# Grid names written into every .vdb file. These are Blender's *standard*
# volume grid names (the same ones Blender's own fluid sims write and the
# Principled Volume shader's Density/Temperature Attribute inputs default to):
# "density" carries the Beer-Lambert extinction coefficient (1/m) at the
# reference mass extinction coefficient, "temperature" the flame value
# normalised to 0..1 (multiplied by the shader's Kelvin Temperature socket for
# blackbody fire emission).
VDB_DENSITY_GRID_NAME = "density"
VDB_TEMPERATURE_GRID_NAME = "temperature"


def _mesh_manifest_entry(mesh) -> dict[str, Any]:
    """Extent/dimension summary for one mesh; never touches smoke data."""
    extent = mesh.extent.as_tuple(reduced=False)  # (x0, x1, y0, y1, z0, z1)
    return {
        "id": mesh.id,
        "extent": tuple(float(v) for v in extent),
        "dims": (int(mesh.dimension.x), int(mesh.dimension.y), int(mesh.dimension.z)),
    }


def probe_fds_simulation(
    smv_path: pathlib.Path, cancel_event: threading.Event
) -> tuple[dict[str, Any] | None, dict[str, float]]:
    """Load only ``.smv``-derived metadata: meshes and available Smoke3D quantities.

    Never touches ``SubSmoke3D.data``, so this is cheap (tens to a few hundred
    milliseconds) regardless of simulation size.  Returns ``(manifest,
    timings)``; ``manifest`` is None if cancelled before completion.
    """
    import fdsreader as fds

    timings: dict[str, float] = {}
    start = time.perf_counter()
    sim = fds.Simulation(str(smv_path))
    timings["load_simulation_metadata"] = time.perf_counter() - start
    if cancel_event.is_set():
        return None, timings

    meshes = [_mesh_manifest_entry(mesh) for mesh in sim.meshes]

    quantities = []
    for smoke3d in sim.smoke_3d:
        cell_count = sum(
            sub.mesh.dimension.x * sub.mesh.dimension.y * sub.mesh.dimension.z
            for sub in smoke3d.subsmokes
        )
        quantities.append(
            {
                "name": smoke3d.quantity.name,
                "short_name": smoke3d.quantity.short_name,
                "unit": smoke3d.quantity.unit,
                "n_t": int(smoke3d.n_t),
                "mesh_ids": [sub.mesh.id for sub in smoke3d.subsmokes],
                # Full-timeseries decode cost if this quantity is selected (float32).
                "estimated_bytes": int(cell_count * smoke3d.n_t * 4),
            }
        )

    manifest = {"smv_path": str(smv_path), "meshes": meshes, "quantities": quantities}
    return manifest, timings


def list_available_quantities(smv_path: pathlib.Path) -> list[dict[str, Any]]:
    """Cheap quantity listing, e.g. to populate a dropdown. See :func:`probe_fds_simulation`."""
    manifest, _ = probe_fds_simulation(smv_path, threading.Event())
    return manifest["quantities"] if manifest else []


def read_smoke_quantity(
    smv_path: pathlib.Path,
    quantity_name: str,
    mesh_ids: list[str] | None,
    cancel_event: threading.Event,
) -> tuple[dict[str, Any] | None, dict[str, float]]:
    """Decode one Smoke3D quantity's full time series for the given (or all) meshes.

    This is the heavy step: each submesh's ``.data`` access RLE-decodes every
    timestep at once (not interruptible mid-mesh), so *cancel_event* is only
    checked between submeshes.  *mesh_ids* of None reads every submesh the
    quantity has.

    Returns ``(data, timings)`` where ``data`` is::

        {
            "quantity": {"name", "short_name", "unit"},
            "times": np.ndarray,           # shared time axis, seconds
            "submeshes": [
                {
                    "mesh_id": str,
                    "extent": (x0, x1, y0, y1, z0, z1),
                    "dims": (nx, ny, nz),           # grid *point* counts
                    "coordinates": {"x": ndarray, "y": ndarray, "z": ndarray},
                    "raw": ndarray,                 # (n_t, nx, ny, nz) float32, values 0..254
                    "upper_bounds": ndarray,        # (n_t,) float32, physical units per timestep
                }, ...
            ],
        }

    Each submesh is kept in its own mesh-local index space (not merged via
    ``Smoke3D.to_global()``, which upsamples coarser meshes onto the finest
    grid and would mislead independent per-mesh VDB sequences). The decoded
    arrays are consumed by :func:`build_fire_smoke_sequence` and then discarded
    (not kept resident) - once written to disk, nothing here needs to survive.
    """
    import fdsreader as fds

    timings: dict[str, float] = {}
    start = time.perf_counter()
    sim = fds.Simulation(str(smv_path))
    timings["load_simulation_metadata"] = time.perf_counter() - start
    if cancel_event.is_set():
        return None, timings

    smoke3d = sim.smoke_3d.get_by_quantity(quantity_name)
    times = np.asarray(smoke3d.times, dtype=np.float64)

    submeshes = []
    start = time.perf_counter()
    for sub in smoke3d.subsmokes:
        if mesh_ids is not None and sub.mesh.id not in mesh_ids:
            continue
        if cancel_event.is_set():
            return None, timings
        raw = np.asarray(sub.data, dtype=np.float32)  # heavy: full-timeseries RLE decode
        submeshes.append(
            {
                "mesh_id": sub.mesh.id,
                "extent": tuple(float(v) for v in sub.mesh.extent.as_tuple(reduced=False)),
                "dims": (
                    int(sub.mesh.dimension.x),
                    int(sub.mesh.dimension.y),
                    int(sub.mesh.dimension.z),
                ),
                "coordinates": {
                    axis: np.asarray(sub.mesh.coordinates[axis], dtype=np.float64)
                    for axis in ("x", "y", "z")
                },
                "raw": raw,
                "upper_bounds": np.asarray(sub.upper_bounds, dtype=np.float32),
            }
        )
    timings["decode_smoke3d"] = time.perf_counter() - start

    data = {
        "quantity": {
            "name": smoke3d.quantity.name,
            "short_name": smoke3d.quantity.short_name,
            "unit": smoke3d.quantity.unit,
        },
        "times": times,
        "submeshes": submeshes,
    }
    return data, timings


def physical_value_at(raw_frame: np.ndarray, upper_bound: float) -> np.ndarray:
    """Recover FDS's physical quantity value from a raw ``0..254`` frame.

    fdsreader stores Smoke3D data pre-scaled to a byte range; ``upper_bound``
    is that frame's ``SubSmoke3D.upper_bounds`` entry (physical units).
    """
    return raw_frame.astype(np.float32) / 255.0 * np.float32(upper_bound)


def _refine_field(field: np.ndarray, refinement: str) -> np.ndarray:
    """Smooth (and optionally 2x-upsample) one frame's voxel field.

    The raw CFD grid is coarse (often 10-25 cm voxels) and renders visibly
    blocky; this is the standard pyro-workflow fix. scipy is available in the
    addon's deps directory (installed alongside pedpy) - no new dependency; if
    it is somehow missing, refinement silently degrades to the raw field
    rather than blocking the load.

    - ``SMOOTH``: light gaussian, removes hard voxel steps at native resolution.
    - ``UPSAMPLE``: 2x trilinear-ish zoom + gaussian, 8x the voxels but much
      rounder silhouettes (caller must halve the voxel size to match).
    """
    if refinement == "SMOOTH":
        from scipy import ndimage

        return ndimage.gaussian_filter(field, sigma=0.6)
    if refinement == "UPSAMPLE":
        from scipy import ndimage

        up = ndimage.zoom(field, 2.0, order=2, prefilter=False)
        # zoom with order>0 can slightly overshoot into negative values.
        return ndimage.gaussian_filter(np.maximum(up, 0.0), sigma=0.8)
    return field


def _effective_refinement(refinement: str) -> str:
    """Downgrade refinement to OFF when scipy is unavailable.

    scipy arrives in the addon's deps directory alongside pedpy (no separate
    dependency); if it is somehow missing, degrade gracefully to raw voxels
    once for the whole sequence - crucially *before* any voxel-size halving,
    so UPSAMPLE cannot silently produce a wrongly-scaled volume.
    """
    if refinement in ("SMOOTH", "UPSAMPLE"):
        try:
            import scipy.ndimage  # noqa: F401
        except ImportError:
            print("[Kinora] scipy unavailable; writing raw (unsmoothed) smoke voxels")
            return "OFF"
    return refinement


def _upsample_factor(refinement: str) -> int:
    return 2 if refinement == "UPSAMPLE" else 1


def _make_grid(field: np.ndarray, name: str, voxel_size: float, origin: tuple[float, ...]):
    """Wrap one frame's voxel field in a world-placed OpenVDB fog-volume grid."""
    import openvdb

    grid = openvdb.FloatGrid()
    grid.copyFromArray(np.ascontiguousarray(field, dtype=np.float32))
    transform = openvdb.createLinearTransform(voxelSize=voxel_size)
    transform.postTranslate(origin)
    grid.transform = transform
    grid.name = name
    grid.gridClass = openvdb.GridClass.FOG_VOLUME
    return grid


def build_fire_smoke_sequence(
    smoke_data: dict[str, Any],
    flame_data: dict[str, Any] | None,
    decimation: int,
    frame_stride: int,
    refinement: str,
    cancel_event: threading.Event,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[str, list[str], int] | None:
    """Write one multi-grid ``.vdb`` file per selected timestep, per submesh.

    Each file carries Blender's standard volume grids:

    - ``density``: soot mass density x :data:`SOOT_MASS_EXTINCTION` - the
      Beer-Lambert extinction coefficient (1/m) at the *reference* mass
      extinction coefficient, exactly what Smokeview uses for smoke opacity.
      The shader applies the live ratio (user coefficient / reference), so
      changing the coefficient never requires a rebake; see the
      :data:`SOOT_MASS_EXTINCTION` comment for why the reference scale is
      baked rather than raw density.
    - ``temperature``: the flame quantity (typically HRRPUV) normalised 0..1
      by its whole-series maximum; the shader's Kelvin Temperature socket
      scales it into blackbody fire emission. Omitted when *flame_data* is
      None.

    *smoke_data*/*flame_data* are :func:`read_smoke_quantity` outputs from the
    same simulation (same meshes and time axis). Files are named
    ``{mesh_id}_{sequence_index:04d}.vdb`` in a fresh temp directory, played
    back via the Volume datablock's native ``is_sequence`` mechanism.
    *decimation* subsamples the spatial grid; *frame_stride* selects every Nth
    timestep; *refinement* applies :func:`_refine_field` per frame.

    Uses Blender's own bundled ``openvdb`` module - no new dependency. Heavy
    (many file writes); runs in the load operator's worker thread and checks
    *cancel_event* between files. Returns ``(sequence_dir, mesh_ids,
    frame_count)``, or None if cancelled.
    """
    import openvdb

    refinement = _effective_refinement(refinement)
    times = smoke_data["times"]
    sequence_dir = tempfile.mkdtemp(prefix="kinora_smoke_")
    frame_indices = list(range(0, len(times), max(1, frame_stride)))
    mesh_ids = [sub["mesh_id"] for sub in smoke_data["submeshes"]]
    total_files = len(mesh_ids) * len(frame_indices)
    written = 0

    flame_subs = {}
    flame_max = 0.0
    if flame_data is not None:
        flame_subs = {sub["mesh_id"]: sub for sub in flame_data["submeshes"]}
        flame_max = max(
            (float(sub["upper_bounds"].max()) for sub in flame_data["submeshes"]),
            default=0.0,
        )

    for sub in smoke_data["submeshes"]:
        dims = sub["dims"]
        coords = sub["coordinates"]
        idx_x = np.arange(0, dims[0], decimation)
        idx_y = np.arange(0, dims[1], decimation)
        idx_z = np.arange(0, dims[2], decimation)
        origin = (
            float(coords["x"][idx_x[0]]),
            float(coords["y"][idx_y[0]]),
            float(coords["z"][idx_z[0]]),
        )
        # FDS meshes use uniform grid spacing; the first step along x stands in
        # for the (decimated) voxel size on all three axes.
        step_x = float(coords["x"][1] - coords["x"][0]) if dims[0] > 1 else 1.0
        voxel_size = step_x * decimation / _upsample_factor(refinement)
        flame_sub = flame_subs.get(sub["mesh_id"])

        for file_idx, t_idx in enumerate(frame_indices):
            if cancel_event.is_set():
                shutil.rmtree(sequence_dir, ignore_errors=True)
                return None

            cell = sub["raw"][t_idx][np.ix_(idx_x, idx_y, idx_z)]
            extinction = physical_value_at(cell, sub["upper_bounds"][t_idx]) * SOOT_MASS_EXTINCTION

            flame_norm = None
            if flame_sub is not None and flame_max > 0:
                flame_cell = flame_sub["raw"][t_idx][np.ix_(idx_x, idx_y, idx_z)]
                flame_phys = physical_value_at(flame_cell, flame_sub["upper_bounds"][t_idx])
                flame_norm = np.clip(flame_phys / flame_max, 0.0, 1.0)
                # Carve smoke inside the flame envelope so the fire is visible.
                extinction = extinction * (1.0 - FLAME_SMOKE_CARVE * flame_norm)

            grids = [
                _make_grid(
                    _refine_field(extinction, refinement),
                    VDB_DENSITY_GRID_NAME,
                    voxel_size,
                    origin,
                )
            ]
            if flame_norm is not None:
                grids.append(
                    _make_grid(
                        _refine_field(flame_norm, refinement),
                        VDB_TEMPERATURE_GRID_NAME,
                        voxel_size,
                        origin,
                    )
                )

            fname = pathlib.Path(sequence_dir) / f"{sub['mesh_id']}_{file_idx + 1:04d}.vdb"
            openvdb.write(str(fname), grids=grids)
            written += 1
            if progress_cb is not None:
                progress_cb(written, total_files)

    return sequence_dir, mesh_ids, len(frame_indices)


def cleanup_vdb_sequence(sequence_dir: str | None) -> None:
    """Remove a previously written scratch VDB sequence directory, if any."""
    if sequence_dir:
        shutil.rmtree(sequence_dir, ignore_errors=True)
