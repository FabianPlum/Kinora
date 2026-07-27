[![Lint](https://github.com/FabianPlum/Kinora/actions/workflows/lint.yml/badge.svg)](https://github.com/FabianPlum/Kinora/actions/workflows/lint.yml) [![Blender Addon CI](https://github.com/FabianPlum/Kinora/actions/workflows/blender.yml/badge.svg)](https://github.com/FabianPlum/Kinora/actions/workflows/blender.yml)

<img src="images/kinora-logo-dark.svg#gh-dark-mode-only" height="120" alt="Kinora">
<img src="images/kinora-logo-light.svg#gh-light-mode-only" height="120" alt="Kinora">


### A Blender Add-on for visualising pedestrian simulation and experimental trajectory data — together with fire and smoke from FDS.

![Addon Preview](https://raw.githubusercontent.com/FabianPlum/Kinora/refs/heads/main/images/kinora_preview_loop.gif)

## Install

1. Download the latest ZIP from [Releases](https://github.com/FabianPlum/Kinora/releases).
2. In [Blender](https://www.blender.org/download/) (4.0 or newer): **Edit > Preferences > Add-ons > Install...** and pick the ZIP.
3. Tick the box next to **Kinora**.
4. Expand the addon and click **Install Dependencies** (give it a minute or two).
5. Restart Blender.

Run Blender as your normal user, not as Administrator or root. If something goes wrong, see [DEVELOPER.md](DEVELOPER.md#troubleshooting).

## Load a simulation

1. In the 3D Viewport, press `N` to open the sidebar.
2. Open the **Kinora** tab.
3. Click **Browse...** and pick your SQLite or HDF5 trajectory file.
4. Click **Load Simulation**.

Agents and walkable geometry appear in the scene, with the timeline set to match the simulation.

For big simulations, tick **Big Data Mode** or set **Load Every Nth Frame** to something above 1.

## Load fire & smoke (FDS)

Kinora can render the Smoke3D output of an [FDS](https://pages.nist.gov/fds-smv/) fire simulation as an animated, physically-based volume — on its own or on top of a loaded trajectory for combined fire-and-evacuation scenes.

1. In the **FDS Fire & Smoke** panel, click **Browse...** and pick the simulation's `.smv` file.
2. Click **Load Fire & Smoke**.

Smoke opacity comes straight from the soot density via the Beer-Lambert law (the same convention Smokeview uses), and the flame is rendered from HRRPUV with blackbody emission — no tuning needed, though both channels have their own show toggle and density slider. When a trajectory is loaded, smoke playback synchronises to its timeline automatically.

Try it with the bundled example: load `kinora/examples/t_junction.sqlite` as the trajectory and `kinora/examples/t_junction.smv` as the fire (a T-shaped corridor evacuation past a growing fire, both covering the same 300 s).

## More

- Full features, options, troubleshooting, and development setup: [DEVELOPER.md](DEVELOPER.md)
- Issues and contributions: [GitHub Issues](https://github.com/FabianPlum/Kinora/issues)

## License

MIT.

## Acknowledgments

- [JuPedSim](https://github.com/PedestrianDynamics/jupedsim)
- [PedPy](https://github.com/PedestrianDynamics/PedPy)
- [FDS / Smokeview](https://pages.nist.gov/fds-smv/) (NIST)
- [fdsreader](https://github.com/FireDynamics/fdsreader)
