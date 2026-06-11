"""Standalone: dump every image in a Kinora HDF5 file to PNGs for inspection.

Renders the static ``image_data`` and all animated ``image_frame_data`` frames so
their appearance, colour mapping (tone mapping) and interpolation can be judged
by eye before wiring choices into the Blender overlay shader.

Run with a normal Python that has numpy + h5py + matplotlib + pillow (e.g. a
conda env), NOT Blender's bundled Python:

    python tools/dump_hdf5_images.py [path/to/file.h5] [output_dir]

Outputs (default output dir: <temp>/kinora_image_dump):
  static/   colour-map x interpolation comparison of image_data
  animated/ every frame in a default colour map + an animated GIF + a montage
  colourmaps_static.png / colourmaps_anim_peak.png  side-by-side comparisons

Orientation note: HDF5 row 0 = world y-max, which is also PNG row 0 (top), so
images are written as-is here (north up). Blender's pixel buffer is bottom-up,
so the addon flips vertically instead.
"""

import os
import sys
import tempfile

import h5py
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw

# --- configuration ----------------------------------------------------------
COLOURMAPS = ["gray", "viridis", "inferno", "magma", "turbo", "hot", "cividis"]
DEFAULT_ANIM_CMAP = "viridis"
UPSCALE = 8  # integer pixel zoom for crisp viewing
GIF_FPS = 15
INTERP = {"nearest": Image.NEAREST, "bilinear": Image.BILINEAR}


def colourize(field, cmap, vmin, vmax):
    """Map a 2-D scalar field to an HxWx3 uint8 RGB image via a colour map."""
    if vmax > vmin:
        norm = np.clip((field - vmin) / (vmax - vmin), 0.0, 1.0)
    else:
        norm = np.zeros_like(field)
    rgba = plt.get_cmap(cmap)(norm)
    return (rgba[..., :3] * 255).astype(np.uint8)


def upscale(rgb, factor, resample):
    img = Image.fromarray(rgb)
    return img.resize((img.width * factor, img.height * factor), resample=resample)


def label(img, text):
    """Return a copy of *img* with a caption bar at the top."""
    bar = 18
    out = Image.new("RGB", (img.width, img.height + bar), (20, 20, 20))
    out.paste(img, (0, bar))
    ImageDraw.Draw(out).text((4, 4), text, fill=(240, 240, 240))
    return out


def montage(tiles, cols, pad=6, bg=(30, 30, 30)):
    """Arrange labelled PIL tiles (assumed equal size) into a grid image."""
    if not tiles:
        return None
    tw, th = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * tw + (cols + 1) * pad, rows * th + (rows + 1) * pad), bg)
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        canvas.paste(tile, (pad + c * (tw + pad), pad + r * (th + pad)))
    return canvas


def dump_static(values, out_dir):
    """Colour-map x interpolation comparison + per-map PNGs for image_data."""
    static_dir = os.path.join(out_dir, "static")
    os.makedirs(static_dir, exist_ok=True)
    vmin, vmax = 0.0, float(values.max())
    print(f"static image_data: {values.shape}, range 0..{vmax:.4f}")

    tiles = []
    for cmap in COLOURMAPS:
        rgb = colourize(values, cmap, vmin, vmax)
        for interp_name, resample in INTERP.items():
            tile = label(upscale(rgb, UPSCALE, resample), f"{cmap} / {interp_name}")
            tiles.append(tile)
        # also a standalone nearest PNG per colour map
        upscale(rgb, UPSCALE, Image.NEAREST).save(
            os.path.join(static_dir, f"image_data_{cmap}.png")
        )
    grid = montage(tiles, cols=len(INTERP))
    grid.save(os.path.join(out_dir, "colourmaps_static.png"))
    print(f"  wrote {len(COLOURMAPS)} PNGs + colourmaps_static.png")


def dump_animated(frames, frame_idx, out_dir):
    """Every frame in the default colour map + GIF + montage + colour compare."""
    anim_dir = os.path.join(out_dir, "animated")
    os.makedirs(anim_dir, exist_ok=True)
    n = frames.shape[0]

    # Robust global normalisation so frames are comparable over time.
    vmin = 0.0
    vmax = float(np.percentile(frames, 99.5))
    print(f"animated: {frames.shape}, global norm 0..{vmax:.4f} (p99.5)")

    gif_frames = []
    for i in range(n):
        rgb = colourize(frames[i], DEFAULT_ANIM_CMAP, vmin, vmax)
        big = upscale(rgb, UPSCALE, Image.NEAREST)
        fnum = int(frame_idx[i])
        big.save(os.path.join(anim_dir, f"frame_{fnum:04d}.png"))
        gif_frames.append(big)
        if (i + 1) % 50 == 0 or i == n - 1:
            print(f"  frame {i + 1}/{n}", end="\r")
    print()

    gif_frames[0].save(
        os.path.join(out_dir, "animated.gif"),
        save_all=True,
        append_images=gif_frames[1:],
        duration=int(1000 / GIF_FPS),
        loop=0,
        optimize=True,
    )

    # Montage: every Nth frame for a quick overview of the whole sequence.
    step = max(1, n // 24)
    overview = [
        label(
            upscale(colourize(frames[i], DEFAULT_ANIM_CMAP, vmin, vmax), 4, Image.NEAREST),
            f"f{int(frame_idx[i])}",
        )
        for i in range(0, n, step)
    ]
    montage(overview, cols=6).save(os.path.join(out_dir, "animated_overview.png"))

    # Colour-map comparison on the peak-density frame.
    peak = int(np.argmax(frames.max(axis=(1, 2))))
    tiles = [
        label(upscale(colourize(frames[peak], c, vmin, vmax), UPSCALE, Image.NEAREST), c)
        for c in COLOURMAPS
    ]
    montage(tiles, cols=3).save(os.path.join(out_dir, "colourmaps_anim_peak.png"))
    print(
        f"  wrote {n} frame PNGs + animated.gif + overview + peak compare (frame {int(frame_idx[peak])})"
    )


def main():
    h5_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "kinora",
            "examples",
            "trajectory_export.h5",
        )
    )
    out_dir = (
        sys.argv[2]
        if len(sys.argv) > 2
        else os.path.join(tempfile.gettempdir(), "kinora_image_dump")
    )
    os.makedirs(out_dir, exist_ok=True)
    print(f"file:   {h5_path}")
    print(f"output: {out_dir}\n")

    with h5py.File(h5_path, "r") as f:
        static = np.asarray(f["image_data"][:], dtype=np.float64)
        frames = np.asarray(f["image_frame_data/data"][:], dtype=np.float64)
        frame_idx = np.asarray(f["image_frame_data/frame"][:])

    dump_static(static, out_dir)
    dump_animated(frames, frame_idx, out_dir)
    print(f"\nDone. Open: {out_dir}")


if __name__ == "__main__":
    main()
