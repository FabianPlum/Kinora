# Kinora Developer Notes

Everything that used to live in the README but isn't strictly necessary for someone who just wants to load a file. Features, internals, troubleshooting, and the dev setup live here.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation (detailed)](#installation-detailed)
- [Usage (detailed)](#usage-detailed)
- [What gets created](#what-gets-created)
- [Display options](#display-options)
- [Simulation data structure](#simulation-data-structure)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Pre-commit hooks](#pre-commit-hooks)
- [Contributing](#contributing)

## Features

- **Import (JuPedSim) SQLite or h5 files**: trajectory data and walkable area geometry.
- **Import HDF5 files**: trajectory data and walkable area geometry.
- **Animated agents**: each agent is an animated cylinder following its trajectory.
- **Agent path visualisation**: each agent's complete path is created as a curve object.
- **Path visibility toggle**: show or hide all agent path curves with a single checkbox.
- **Geometry visualisation**: walkable area boundaries and obstacles are displayed as curves.
- **Big Data Mode**: stream agents as particles for very large datasets.
- **FDS fire & smoke**: load an FDS simulation's Smoke3D output (`.smv` + `.s3d`) and
  render it as an animated OpenVDB volume — physically-based smoke opacity
  (Beer-Lambert soot extinction, Smokeview's own convention) plus blackbody flame
  emission from HRRPUV, automatically time-synced to a loaded trajectory.
- **Display controls**: agent scale, geometry thickness, frame rate.
- **Built-in dependency installer** for the required Python packages.

## Requirements

- **Blender 4.0+** (Blender 3.x is not supported).
  - Development and testing on Blender 5.0.
  - Should work on Blender 4.0+ and 5.1+ but is not actively tested.
- **Python packages**: `pedpy` and `fdsreader` (plus their transitive deps), installed automatically via the addon.

`pedpy` is kept mainly for legacy compatibility and because we currently rely on its `shapely` dependency for geometry processing. SQLite reading is now handled via a streaming approach inspired by the [JuPedSim visualizer's reader](https://github.com/PedestrianDynamics/jupedsim/tree/master/python_modules/jupedsim_visualizer/jupedsim_visualizer). [`fdsreader`](https://github.com/FireDynamics/fdsreader) parses FDS Smoke3D output; the OpenVDB volumes are written with Blender's own bundled `openvdb` module, so no extra binary dependency is needed.

## Installation (detailed)

1. **Download the latest release** from [GitHub Releases](https://github.com/FabianPlum/Kinora/releases).
2. **Open Blender as your normal user** (do not run as Administrator or root for installation).
3. Go to **Edit > Preferences > Add-ons**.
4. Click **Install...** and select the downloaded ZIP file.
5. Enable the addon by checking the box next to "Kinora - JuPedSim Visualiser".
6. **(Recommended)** If you started Blender without a terminal (e.g. on Windows by double-clicking the icon), open **Window > Toggle System Console** before the next step. You can then see pip's progress while dependencies install; Blender may look unresponsive for one or two minutes.
7. Expand the addon settings and click **Install Dependencies** (this installs `pedpy` and `fdsreader` into the addon folder, constrained to Blender's own bundled `numpy`).
8. **Restart Blender.**

The **Kinora** panel will appear in the right sidebar of the 3D Viewport (press `N` if the sidebar is hidden).

> **Important:** Run Blender as the same user you use every day. If you install the addon or dependencies while Blender is run as Administrator or root, they are installed in that account's Blender config. When you then start Blender as a normal user, the addon may not appear, or pedpy may not be found. Always install and use Blender as your normal user.

## Usage (detailed)

1. Open Blender and go to the **3D Viewport**.
2. Open the sidebar (press `N` if hidden).
3. Find the **Kinora** tab.
4. Click **Browse...** to select your SQLite trajectory file.
5. (Optional) Adjust **Load Every Nth Frame** to downsample temporal resolution for faster loading:
   - `1` = load all frames (default).
   - `2` = load every 2nd frame (50% of keyframes).
   - `10` = load every 10th frame (10% of keyframes), and so on.
6. (Optional) Enable **Big Data Mode** to handle very large datasets (agents load as particles).
7. (Optional) Enable **Load Full Paths** if you want per-agent path curves.
8. Click **Load Simulation**.

### FDS fire & smoke

Works standalone or alongside a loaded trajectory (for combined
fire-and-evacuation scenes). The bundled `examples/t_junction.smv` +
`examples/t_junction.sqlite` pair is a ready-made coupled scenario.

1. In the **FDS Fire & Smoke** panel, click **Browse...** and pick the
   simulation's `.smv` file. The panel reports whether the file provides
   smoke (`SOOT DENSITY`) and flame (`HRRPUV`) Smoke3D output — smoke is
   required, flame is optional.
2. (Optional) Pick a **Refine** mode: light gaussian smoothing (default,
   removes the blocky CFD-grid look), smoothing plus 2× upsampling (nicer
   silhouettes, 8× the voxels), or off (raw voxels).
3. Click **Load Fire & Smoke**. Every FDS timestep is written as an OpenVDB
   file (in a temporary directory) and played back through Blender's native
   volume-sequence mechanism.

Notes:

- **Physically-based smoke**: the density grid carries the Beer-Lambert
  extinction coefficient (soot density × the mass extinction coefficient,
  default 8700 m²/kg — FDS's own `MASS_EXTINCTION_COEFFICIENT`), which is
  exactly how Smokeview computes smoke opacity. Real smoke is therefore
  nearly opaque near the fire; lower the **Smoke Density Multiplier** (e.g.
  to 0.1) if you want agents inside the plume to stay visible.
- **Flame**: HRRPUV drives blackbody emission; **Flame Colour Temp (K)** is
  a visual colour control (HRRPUV is a heat-release density, not a
  temperature).
- **Timeline sync**: with a trajectory loaded, smoke playback follows the
  trajectory's simulation clock automatically (each Blender frame shows the
  FDS state nearest in time); **Frame Offset** shifts it manually.
- Volumes need **Material Preview** or **Rendered** viewport shading; the
  addon switches Solid viewports over automatically, and raises Cycles'
  volume light bounces (default 0) so smoke doesn't render flat.

## What gets created

- **Kinora_Agents** collection: contains an animated cylinder mesh for each agent.
  - Agents automatically hide after reaching their destination.
  - Path curves for each agent showing their complete trajectory (hidden by default).
- **Big Data Mode**: creates a single particle system driven by streamed frame updates.
- **Kinora_Geometry** collection: contains curve objects for boundaries and obstacles.
- **Kinora_Smoke** collection (FDS loads): one Volume object per FDS mesh, playing an
  OpenVDB sequence written to a temporary directory (removed again on unload).
- Animation timeline is automatically set to match the simulation frames.

## Display options

After loading a simulation, a **Display Options** section appears in the panel:

- **Agent Scale (m)**: adjust size of agent cylinders or instances.
- **Geometry Thickness (m)**: adjust thickness of walkable area curves.
- **Frame Rate**: quick access to Blender frame rate presets.
- **Show Agent Paths**: toggle checkbox to show or hide all agent path curves.
  - Requires **Load Full Paths** on import.
  - Paths are hidden by default but can be toggled on or off at any time.
  - Each path is a 3D curve object showing the agent's complete trajectory.

After loading FDS fire & smoke, the **FDS Fire & Smoke** panel shows per-channel controls
(all live — no reload needed):

- **Smoke**: show toggle, density multiplier (1.0 = Smokeview-accurate opacity),
  mass extinction coefficient (m²/kg), and procedural detail noise amount.
- **Flame**: show toggle, density multiplier (emission brightness), and flame
  colour temperature (K).
- **Frame Offset**: shift the smoke sequence against the rest of the timeline.

## Simulation data structure

The addon uses [PedPy](https://github.com/PedestrianDynamics/PedPy) (mainly for `shapely`) and streams the SQLite data similarly to the JuPedSim visualizer to read:

- **Trajectory data**: agent positions over time (x, y coordinates per frame).
- **Walkable area**: the geometry defining where agents can move.

## Troubleshooting

### Addon does not appear, or pedpy not found after restart (macOS, Linux, Windows)

- **Do not run Blender as Administrator or root** for normal use or when installing the addon or dependencies. If you previously installed while running Blender as root (e.g. `sudo Blender` on macOS), the addon and pedpy were installed in root's Blender config. A normal user session then uses a different config, so the addon may not show or pedpy may be "not found."
- **Fix:** Run Blender as your normal user. Remove the addon from **Edit > Preferences > Add-ons** if it was installed as root. Install the addon again (Install... then enable) and click **Install Dependencies** while Blender is running as your normal user. Restart Blender as normal user. Dependencies are installed into the addon folder, so no admin rights are needed.

### "Dependencies not installed" error

- Install dependencies from **Edit > Preferences > Add-ons > Kinora > Install Dependencies**. No Administrator or root required.
- Check the Blender console (Window > Toggle System Console on Windows, or run Blender from Terminal on macOS or Linux) for error messages.
- Try reinstalling dependencies from the addon preferences.

### "File not found" error

- Use absolute paths or ensure the file path is correct.
- Check that the SQLite file is a valid JuPedSim trajectory file.

### Agents appear at wrong scale

- JuPedSim uses meters as units. Make sure your Blender scene is set to metric units.
- Agents are created as 1-meter cylinders by default.

### Loading takes too long

- Use the **Load Every Nth Frame** option to downsample the temporal resolution.
- Enable **Big Data Mode** for very large datasets.
- Disable **Load Full Paths** unless you really need per-agent curves.
- For very large simulations, try values like 5 or 10 to significantly reduce loading time.
- Linear interpolation will fill in the gaps between keyframes for smooth animation.

## Development

For developers who want to work with the git repository and have changes reflected immediately in Blender.

### Development installation (symbolic link)

Create a symbolic link to your `kinora` folder in your Blender addons directory. This allows for easier development as changes are reflected immediately.

**Windows:**

1. Open Command Prompt or PowerShell as Administrator.
2. Navigate to your Blender addons directory:

   ```
   cd "%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons"
   ```

   Replace `<version>` with your Blender version (e.g., `4.2`).

3. Create a symbolic link:

   ```
   mklink /D kinora "C:\path\to\Kinora\kinora"
   ```

   Replace `C:\path\to\Kinora\kinora` with the actual path to your `kinora` folder.

> **No admin? Use a junction instead.** Symbolic links on Windows require Administrator (or Developer Mode). A directory junction behaves the same for our purposes and needs neither. From a normal PowerShell or cmd prompt:
>
> ```
> New-Item -ItemType Junction `
>   -Path  "$env:APPDATA\Blender Foundation\Blender\<version>\scripts\addons\kinora" `
>   -Target "C:\path\to\Kinora\kinora"
> ```
>
> or in cmd: `mklink /J "%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\kinora" "C:\path\to\Kinora\kinora"`. Avoid Git Bash's `ln -s` here. Without proper symlink support enabled it silently makes a directory copy, so repo edits won't reach Blender.

**macOS, Linux:**

1. Open Terminal.
2. Navigate to your Blender addons directory:

   ```
   cd ~/Library/Application\ Support/Blender/<version>/scripts/addons  # macOS
   # or
   cd ~/.config/blender/<version>/scripts/addons  # Linux
   ```

3. Create a symbolic link:

   ```
   ln -s /path/to/Kinora/kinora kinora
   ```

4. Open Blender.
5. Go to **Edit > Preferences > Add-ons**.
6. Search for "Kinora" and enable the addon.

## Pre-commit hooks

CI runs `ruff check` and `ruff format --check`. To catch these locally before pushing:

```
pip install pre-commit
pre-commit install
```

Hooks then run on every `git commit`. To check the whole repo on demand:

```
pre-commit run --all-files
```

## Contributing

Contributions are welcome. Please open issues or pull requests at [github.com/FabianPlum/Kinora](https://github.com/FabianPlum/Kinora).
