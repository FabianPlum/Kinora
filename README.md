[![Lint](https://github.com/FabianPlum/Kinora/actions/workflows/lint.yml/badge.svg)](https://github.com/FabianPlum/Kinora/actions/workflows/lint.yml) [![Blender Addon CI](https://github.com/FabianPlum/Kinora/actions/workflows/blender.yml/badge.svg)](https://github.com/FabianPlum/Kinora/actions/workflows/blender.yml)

<img src="images/kinora-logo-dark.svg#gh-dark-mode-only" height="120" alt="Kinora">
<img src="images/kinora-logo-light.svg#gh-light-mode-only" height="120" alt="Kinora">


### A Blender Add-on for visualising pedestrian simulation and experimental trajectory data.

![Addon Preview](images/preview_v2.jpg)

## Install

1. Download the latest ZIP from [Releases](https://github.com/FabianPlum/Kinora/releases).
2. In Blender (4.0 or newer): **Edit > Preferences > Add-ons > Install...** and pick the ZIP.
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

## More

- Full features, options, troubleshooting, and development setup: [DEVELOPER.md](DEVELOPER.md)
- Issues and contributions: [GitHub Issues](https://github.com/FabianPlum/Kinora/issues)

## License

MIT.

## Acknowledgments

- [JuPedSim](https://github.com/PedestrianDynamics/jupedsim)
- [PedPy](https://github.com/PedestrianDynamics/PedPy)
