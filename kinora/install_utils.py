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
        addon_dir: Path to the addon directory (kinora)
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

        # Install pedpy and its dependencies into the local deps directory.
        # We don't pin pedpy or numpy ourselves — whatever numpy pedpy pulls
        # in (1.x or 2.x) wins at runtime because ensure_deps_in_path evicts
        # Blender's bundled numpy from sys.modules so our deps copy loads.
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
            ],
            timeout=timeout,
        )

        return True, "Dependencies installed successfully"

    except subprocess.CalledProcessError as e:
        return False, f"Failed to install dependencies: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


_SHADOWABLE_TOPLEVEL = frozenset(
    {
        "numpy",
        "scipy",
        "pedpy",
        "shapely",
        "h5py",
        "pandas",
        "matplotlib",
        "contourpy",
        "kiwisolver",
        "cycler",
        "PIL",
        "mpl_toolkits",
        "dateutil",
        "fontTools",
    }
)


def ensure_deps_in_path(addon_dir):
    """Add the local deps directory to sys.path and force our deps to win.

    Blender ships its own numpy in site-packages, which Python caches in
    sys.modules before our addon loads. Inserting deps_dir at sys.path[0]
    alone is not enough — already-cached modules win regardless of path order.
    So we also evict any cached copy of a shadowable dependency whose
    __file__ is not inside our deps dir, forcing the next `import X` to
    rebind to our bundled version.
    """
    deps_dir = get_deps_dir(addon_dir)
    if not os.path.exists(deps_dir):
        return
    if deps_dir not in sys.path:
        sys.path.insert(0, deps_dir)

    deps_norm = os.path.normcase(os.path.realpath(deps_dir))
    for name in list(sys.modules):
        if name.split(".", 1)[0] not in _SHADOWABLE_TOPLEVEL:
            continue
        mod = sys.modules.get(name)
        mod_file = getattr(mod, "__file__", None) if mod is not None else None
        if not mod_file:
            continue
        if not os.path.normcase(os.path.realpath(mod_file)).startswith(deps_norm):
            del sys.modules[name]


def is_pedpy_installed(addon_dir):
    """Check if pedpy is installed and importable."""
    import importlib.util

    ensure_deps_in_path(addon_dir)
    return importlib.util.find_spec("pedpy") is not None


def dependencies_installed(addon_dir):
    """
    True if pedpy is importable or was just installed into the addon deps dir.
    Used to grey out the install button and show restart prompt.
    """
    if is_pedpy_installed(addon_dir):
        return True
    pedpy_dir = os.path.join(get_deps_dir(addon_dir), "pedpy")
    return os.path.isdir(pedpy_dir)
