import argparse
import os
import sqlite3
import sys
import tempfile
import traceback

import addon_utils
import bpy


def _script_args():
    """Return args passed after `--` (Blender convention)."""
    argv = sys.argv
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return []


def _add_repo_root_to_syspath():
    """
    Ensure the repository root is on sys.path so the add-on can be imported
    directly from the checkout in CI.
    """
    here = os.path.abspath(os.path.dirname(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    return repo_root


def _parse_args():
    p = argparse.ArgumentParser(description="Headless Blender add-on smoke test")
    p.add_argument("--addon", required=True, help="Blender add-on module name")
    p.add_argument(
        "--require-module",
        action="append",
        default=[],
        help="Python module that must be importable (repeatable)",
    )
    p.add_argument(
        "--require-operator",
        action="append",
        default=[],
        help="Operator that must exist, e.g. 'kinora.load_simulation' (repeatable)",
    )
    p.add_argument(
        "--factory-startup",
        action="store_true",
        help="Reset Blender to factory settings",
    )
    p.add_argument(
        "--test-sqlite-loading",
        action="store_true",
        help="Test SQLite file loading operator",
    )
    p.add_argument(
        "--test-example-file",
        action="store_true",
        help="Test loading the prepackaged examples/trajectories.sqlite file",
    )
    p.add_argument(
        "--test-hdf5-loading",
        action="store_true",
        help="Test HDF5 file loading via the hdf5_reader module",
    )
    p.add_argument(
        "--test-dependency-installation",
        action="store_true",
        help="Test the addon's dependency installation system",
    )
    return p.parse_args(_script_args())


def _operator_exists(op_id: str) -> bool:
    """Check if bpy.ops.<category>.<op> exists."""
    if "." not in op_id:
        return False
    cat, op = op_id.split(".", 1)
    cat_obj = getattr(bpy.ops, cat, None)
    return cat_obj is not None and getattr(cat_obj, op, None) is not None


def _create_test_sqlite_file():
    """
    Create a minimal test SQLite file with JuPedSim trajectory data.
    Returns the path to the temporary file.
    """
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE trajectory_data (
            id INTEGER,
            frame INTEGER,
            pos_x REAL,
            pos_y REAL
        )
    """)

    test_data = []
    for frame in range(0, 50, 10):
        for agent_id in range(1, 4):
            x = agent_id * 2.0 + frame * 0.1
            y = agent_id * 1.5 + frame * 0.05
            test_data.append((agent_id, frame, x, y))

    cursor.executemany(
        "INSERT INTO trajectory_data (id, frame, pos_x, pos_y) VALUES (?, ?, ?, ?)", test_data
    )

    cursor.execute("""
        CREATE TABLE geometry (
            wkt TEXT
        )
    """)

    wkt = "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))"
    cursor.execute("INSERT INTO geometry (wkt) VALUES (?)", (wkt,))

    conn.commit()
    conn.close()

    print(f"✓ Created test SQLite file: {db_path}")
    print("  - 3 agents")
    print("  - 5 frames (0, 10, 20, 30, 40)")
    print("  - Simple geometry")

    return db_path


def _test_sqlite_loading(addon_name):
    """Test the SQLite loading operator."""
    print("\n" + "=" * 72)
    print("Testing SQLite Loading Operator")
    print("=" * 72 + "\n")

    db_path = _create_test_sqlite_file()

    try:
        bpy.context.scene.kinora_props.sqlite_file = db_path
        print(f"✓ Set sqlite_file property to: {db_path}")

        if not _operator_exists("kinora.load_simulation"):
            raise RuntimeError("Operator 'kinora.load_simulation' not found")
        print("✓ Operator 'kinora.load_simulation' exists")

        if not _operator_exists("kinora.select_file"):
            raise RuntimeError("Operator 'kinora.select_file' not found")
        print("✓ Operator 'kinora.select_file' exists")

        props = bpy.context.scene.kinora_props
        assert hasattr(props, "sqlite_file"), "Missing 'sqlite_file' property"
        assert hasattr(props, "loading_in_progress"), "Missing 'loading_in_progress' property"
        assert hasattr(props, "frame_step"), "Missing 'frame_step' property"
        print("✓ Scene properties are properly registered")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trajectory_data")
        count = cursor.fetchone()[0]
        conn.close()

        if count != 15:  # 3 agents x 5 frames
            raise RuntimeError(f"Expected 15 trajectory records, found {count}")
        print(f"✓ SQLite file contains correct data ({count} records)")

        print("\n✓ SQLite loading operator test passed!")

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"✓ Cleaned up test file: {db_path}")


def _validate_sqlite_schema(db_path, min_agents=1, min_frames=1):
    """
    Validate that a SQLite file has the expected JuPedSim schema and data.

    Args:
        db_path: Path to the SQLite file
        min_agents: Minimum number of unique agents expected
        min_frames: Minimum number of frames expected

    Returns:
        tuple: (agent_count, frame_count, has_geometry)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='trajectory_data'
    """)
    if not cursor.fetchone():
        raise RuntimeError("Missing 'trajectory_data' table")
    print("✓ Found 'trajectory_data' table")

    cursor.execute("PRAGMA table_info(trajectory_data)")
    columns = {row[1] for row in cursor.fetchall()}
    required_cols = {"id", "frame", "pos_x", "pos_y"}
    if not required_cols.issubset(columns):
        missing = required_cols - columns
        raise RuntimeError(f"Missing required columns in trajectory_data: {missing}")
    print(f"✓ trajectory_data has required columns: {required_cols}")

    cursor.execute("SELECT COUNT(*) FROM trajectory_data")
    total_records = cursor.fetchone()[0]
    print(f"✓ Found {total_records} trajectory records")

    cursor.execute("SELECT COUNT(DISTINCT id) FROM trajectory_data")
    agent_count = cursor.fetchone()[0]
    print(f"✓ Found {agent_count} unique agents")

    cursor.execute("SELECT COUNT(DISTINCT frame) FROM trajectory_data")
    frame_count = cursor.fetchone()[0]
    print(f"✓ Found {frame_count} unique frames")

    cursor.execute("SELECT MIN(frame), MAX(frame) FROM trajectory_data")
    min_frame, max_frame = cursor.fetchone()
    print(f"✓ Frame range: {min_frame} to {max_frame}")

    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='geometry'
    """)
    has_geometry = cursor.fetchone() is not None
    if has_geometry:
        cursor.execute("SELECT COUNT(*) FROM geometry")
        geom_count = cursor.fetchone()[0]
        print(f"✓ Found geometry table with {geom_count} records")
    else:
        print("ℹ No geometry table found (optional)")

    conn.close()

    if agent_count < min_agents:
        raise RuntimeError(f"Expected at least {min_agents} agents, found {agent_count}")
    if frame_count < min_frames:
        raise RuntimeError(f"Expected at least {min_frames} frames, found {frame_count}")

    return agent_count, frame_count, has_geometry


def _test_example_file(addon_name, repo_root):
    """Test loading the prepackaged examples/trajectories.sqlite file."""
    print("\n" + "=" * 72)
    print("Testing Prepackaged Example File (examples/trajectories.sqlite)")
    print("=" * 72 + "\n")

    example_path = os.path.join(repo_root, "kinora", "examples", "trajectories.sqlite")

    if not os.path.exists(example_path):
        raise RuntimeError(f"Example file not found: {example_path}")
    print(f"✓ Found example file: {example_path}")

    file_size = os.path.getsize(example_path)
    print(f"✓ File size: {file_size:,} bytes ({file_size / 1024:.1f} KB)")

    agent_count, frame_count, has_geometry = _validate_sqlite_schema(
        example_path,
        min_agents=1,
        min_frames=1,
    )

    bpy.context.scene.kinora_props.sqlite_file = example_path
    print("✓ Set sqlite_file property to example file")

    conn = sqlite3.connect(example_path)
    cursor = conn.cursor()

    cursor.execute("SELECT id, frame, pos_x, pos_y FROM trajectory_data LIMIT 5")
    sample_data = cursor.fetchall()
    print("✓ Sample data (first 5 records):")
    for row in sample_data:
        print(f"  Agent {row[0]}, Frame {row[1]}: ({row[2]:.2f}, {row[3]:.2f})")

    conn.close()

    print("\n✓ Example file validation passed!")
    print(f"  - {agent_count} agents")
    print(f"  - {frame_count} frames")
    print(f"  - Geometry: {'Yes' if has_geometry else 'No'}")


def _test_hdf5_loading(addon_name, repo_root):
    """Test loading an HDF5 trajectory file via the hdf5_reader module."""
    print("\n" + "=" * 72)
    print("Testing HDF5 Loading")
    print("=" * 72 + "\n")

    import pathlib

    example_path = pathlib.Path(repo_root) / "kinora" / "examples" / "040_l020_g1_rf_h-.h5"
    if not example_path.exists():
        raise RuntimeError(f"HDF5 example file not found: {example_path}")
    print(f"✓ Found HDF5 example file: {example_path}")

    file_size = example_path.stat().st_size
    print(f"✓ File size: {file_size:,} bytes ({file_size / 1024:.1f} KB)")

    from kinora.io.hdf5_reader import read_simulation_data

    cancel_event = __import__("threading").Event()
    data, timings = read_simulation_data(
        example_path, frame_step=1, load_full_paths=False, cancel_event=cancel_event
    )

    assert data is not None, "read_simulation_data returned None"
    assert len(data["agent_ids"]) > 0, "No agents found in HDF5 file"
    assert data["min_frame"] <= data["max_frame"], "Invalid frame range"
    assert data["frame_data"] is not None, "frame_data is None"
    assert len(data["frame_data"]) > 0, "frame_data is empty"
    assert data["geometry"] is not None, "geometry is None"

    print(f"✓ Loaded {len(data['agent_ids'])} agents")
    print(f"✓ Frame range: {data['min_frame']} to {data['max_frame']}")
    print(f"✓ {len(data['frame_data'])} frames pre-grouped")
    print("✓ Geometry loaded")

    # Test with frame_step > 1
    data2, _ = read_simulation_data(
        example_path, frame_step=5, load_full_paths=False, cancel_event=cancel_event
    )
    assert len(data2["frame_data"]) <= len(data["frame_data"]), (
        "frame_step filtering did not reduce frames"
    )
    print(
        f"✓ frame_step=5 reduced frames from {len(data['frame_data'])} to {len(data2['frame_data'])}"
    )

    # Test with full paths
    data3, _ = read_simulation_data(
        example_path, frame_step=1, load_full_paths=True, cancel_event=cancel_event
    )
    assert data3["path_groups"] is not None, "path_groups is None with load_full_paths=True"
    assert len(data3["path_groups"]) == len(data3["agent_ids"]), "path_groups count mismatch"
    print(f"✓ Full paths loaded for {len(data3['path_groups'])} agents")

    for key, value in timings.items():
        print(f"  - {key}: {value:.3f}s")

    print("\n✓ HDF5 loading test passed!")


def _test_dependency_installation(addon_name, repo_root):
    """Test the addon's dependency installation operator."""
    print("\n" + "=" * 72)
    print("Testing Dependency Installation System")
    print("=" * 72 + "\n")

    if not _operator_exists("kinora.install_dependencies"):
        raise RuntimeError("Operator 'kinora.install_dependencies' not found")
    print("✓ Operator 'kinora.install_dependencies' exists")

    if not _operator_exists("kinora.uninstall_dependencies"):
        raise RuntimeError("Operator 'kinora.uninstall_dependencies' not found")
    print("✓ Operator 'kinora.uninstall_dependencies' exists")

    addon_dir = os.path.join(repo_root, "kinora")
    deps_dir = os.path.join(addon_dir, "deps")

    if not os.path.exists(deps_dir):
        raise RuntimeError(f"deps directory not found: {deps_dir}")
    print(f"✓ deps directory exists: {deps_dir}")

    deps_contents = os.listdir(deps_dir)
    print(f"✓ deps directory contains {len(deps_contents)} items")

    pedpy_dir = os.path.join(deps_dir, "pedpy")
    if not os.path.isdir(pedpy_dir):
        raise RuntimeError(f"pedpy package directory not found: {pedpy_dir}")
    print(f"✓ pedpy package directory exists: {pedpy_dir}")

    if deps_dir not in sys.path:
        sys.path.insert(0, deps_dir)

    import importlib.util

    if importlib.util.find_spec("pedpy") is None:
        raise RuntimeError("pedpy not importable from deps.")
    else:
        import pedpy

        print(f"✓ pedpy importable from deps (version: {pedpy.__version__})")

    if importlib.util.find_spec("shapely") is None:
        raise RuntimeError("shapely not importable.")
    else:
        print("✓ shapely importable (pedpy dependency)")

    # Test the preferences class exists
    try:
        _ = bpy.context.preferences.addons[addon_name].preferences
        print("✓ Addon preferences accessible")
    except (KeyError, AttributeError) as e:
        raise RuntimeError(f"Cannot access addon preferences: {e}") from e

    print("\n✓ Dependency installation system test passed!")


def main():
    args = _parse_args()

    print("\n" + "=" * 72)
    print(f"Blender: {bpy.app.version_string}")
    print(f"Addon module: {args.addon}")
    print("=" * 72)

    repo_root = _add_repo_root_to_syspath()
    print(f"Repo root on sys.path: {repo_root}")

    if args.factory_startup:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        print("Loaded factory startup settings (empty).")

    enabled_ok = False

    try:
        addon_utils.enable(args.addon, default_set=True, persistent=False)
        enabled_ok = True
        print(f"✓ Enabled add-on '{args.addon}'")

        if args.addon not in bpy.context.preferences.addons.keys():
            raise RuntimeError(
                f"Add-on '{args.addon}' not present in bpy.context.preferences.addons"
            )
        print(f"✓ Add-on '{args.addon}' present in enabled add-ons list")

        for mod in args.require_module:
            __import__(mod)
            print(f"✓ Required module import ok: {mod}")

        for op_id in args.require_operator:
            if not _operator_exists(op_id):
                raise RuntimeError(f"Required operator not found: {op_id}")
            print(f"✓ Required operator exists: {op_id}")

        if args.test_dependency_installation:
            _test_dependency_installation(args.addon, repo_root)

        if args.test_sqlite_loading:
            _test_sqlite_loading(args.addon)

        if args.test_hdf5_loading:
            _test_hdf5_loading(args.addon, repo_root)

        if args.test_example_file:
            _test_example_file(args.addon, repo_root)

        print("\n" + "=" * 72)
        print("All tests passed!")
        print("=" * 72 + "\n")
        return 0

    except Exception as e:
        print("\n✗ TEST FAILED")
        print(f"Reason: {e}")
        traceback.print_exc()
        return 1

    finally:
        if enabled_ok:
            try:
                addon_utils.disable(args.addon, default_set=True)
                print(f"✓ Disabled add-on '{args.addon}'")
            except Exception as e:
                print(f"⚠ Failed to disable add-on '{args.addon}': {e}")


if __name__ == "__main__":
    raise SystemExit(main())
