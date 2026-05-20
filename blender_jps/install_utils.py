"""
Shared dependency installation utilities.
Used by both the addon preferences UI and CI tests.
"""

import os
import subprocess
import sys


def get_deps_dir(addon_dir):
    """Get the path to the deps directory."""
    return os.path.join(addon_dir, "deps")


def install_dependencies(addon_dir, timeout=300):
    """
    Install pedpy and dependencies to the addon's deps directory.

    Args:
        addon_dir: Path to the addon directory (blender_jps)
        timeout: Timeout in seconds for pip install

    Returns:
        tuple: (success: bool, message: str)
    """
    deps_dir = get_deps_dir(addon_dir)
    py_exec = sys.executable

    try:
        # Create deps directory
        os.makedirs(deps_dir, exist_ok=True)

        # Ensure pip is available
        subprocess.check_call([py_exec, "-m", "ensurepip", "--upgrade"])

        # Upgrade pip
        subprocess.check_call([py_exec, "-m", "pip", "install", "--upgrade", "pip"])

        # Install pedpy and numpy to the local deps directory
        subprocess.check_call(
            [
                py_exec,
                "-m",
                "pip",
                "install",
                "--target",
                deps_dir,
                "--upgrade",
                "--no-user",
                "pedpy",
                "jupedsim<2.0",
                "numpy<2.0",
            ],
            timeout=timeout,
        )

        return True, "Dependencies installed successfully"

    except subprocess.CalledProcessError as e:
        return False, f"Failed to install dependencies: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def ensure_deps_in_path(addon_dir):
    """Add the local deps directory to sys.path if it exists."""
    deps_dir = get_deps_dir(addon_dir)
    if os.path.exists(deps_dir) and deps_dir not in sys.path:
        sys.path.insert(0, deps_dir)


def is_pedpy_installed(addon_dir):
    """Check if pedpy is installed and importable."""
    import importlib.util

    ensure_deps_in_path(addon_dir)
    return importlib.util.find_spec("pedpy") is not None


def is_jupedsim_installed(addon_dir):
    """Check if jupedsim is installed and importable.

    Optional: required only for the navmesh / router debug feature.
    """
    import importlib.util

    ensure_deps_in_path(addon_dir)
    return importlib.util.find_spec("jupedsim") is not None


def dependencies_installed(addon_dir):
    """
    True if pedpy is importable or was just installed into the addon deps dir.
    Used to grey out the install button and show restart prompt.
    """
    if is_pedpy_installed(addon_dir):
        return True
    pedpy_dir = os.path.join(get_deps_dir(addon_dir), "pedpy")
    return os.path.isdir(pedpy_dir)
