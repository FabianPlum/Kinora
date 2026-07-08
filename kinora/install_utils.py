"""
Shared dependency installation utilities.
Used by both the addon preferences UI and CI tests.
"""

import json
import os
import subprocess
import sys
import tempfile

# Heavy packages Blender may ship in its own Python.  Our deps directory must
# REUSE these, never install a second, conflicting copy — a duplicate numpy that
# overrides Blender's at import time is what dragged the viewport to a crawl.
_BLENDER_SHARED_PACKAGES = ("numpy", "scipy", "pandas", "shapely", "h5py", "matplotlib")


def get_deps_dir(addon_dir):
    """Get the path to the deps directory."""
    return os.path.join(addon_dir, "deps")


def _bundled_versions(py_exec):
    """Return ``{package: version}`` for shared packages Blender actually loads.

    Read from each module's ``__version__`` (not ``importlib.metadata``, which
    can report a stale dist-info version that differs from the importable
    module — e.g. a leftover numpy-1.24.3.dist-info next to numpy 1.26.4).
    Queried from a clean subprocess of *py_exec* so the addon deps dir is not on
    the path.
    """
    query = (
        "import json\n"
        "out = {}\n"
        f"for p in {list(_BLENDER_SHARED_PACKAGES)!r}:\n"
        "    try:\n"
        "        out[p] = __import__(p).__version__\n"
        "    except Exception:\n"
        "        pass\n"
        "print(json.dumps(out))\n"
    )
    raw = subprocess.check_output([py_exec, "-c", query], text=True)
    return json.loads(raw.strip().splitlines()[-1])


def _deps_dist_version(deps_dir, pkg):
    """Version of *pkg* installed inside *deps_dir* (from its dist-info), else None."""
    if not os.path.isdir(deps_dir):
        return None
    prefix = pkg.lower() + "-"
    suffix = ".dist-info"
    for entry in os.listdir(deps_dir):
        low = entry.lower()
        if low.startswith(prefix) and low.endswith(suffix):
            return entry[len(prefix) : -len(suffix)]
    return None


def _verify_no_numpy_collision(deps_dir, bundled):
    """Fail if deps_dir holds a numpy whose version differs from Blender's.

    numpy is the shared C-extension whose duplication/override degrades the
    viewport, so it is the one we must never let deps install at a different
    version than Blender already ships.
    """
    bver = bundled.get("numpy")
    dver = _deps_dist_version(deps_dir, "numpy")
    if dver is not None and bver is not None and dver != bver:
        return False, (
            f"deps installed numpy {dver} but Blender ships {bver}; aborting to "
            "avoid a runtime numpy swap. Install Kinora in a Blender whose "
            "bundled numpy this pedpy supports."
        )
    return True, "ok"


def install_dependencies(addon_dir, timeout=300):
    """Install pedpy, fdsreader, and their dependencies into the addon's deps directory.

    The install is *constrained to Blender's own bundled numpy version*, so pip
    reuses Blender's numpy and back-tracks pedpy to a release compatible with it
    (e.g. pedpy 1.2.x on a numpy-1.x Blender, 1.5.x on a numpy-2.x Blender),
    instead of dropping a second, conflicting numpy into deps that would have to
    override Blender's at runtime.  fdsreader's own dependencies (numpy plus
    pure-Python ``incremental``/``typing_extensions``) don't conflict with
    pedpy's, so both install in the same pass.

    Args:
        addon_dir: Path to the addon directory (kinora)
        timeout: Timeout in seconds for pip install

    Returns:
        tuple: (success: bool, message: str)
    """
    deps_dir = get_deps_dir(addon_dir)
    py_exec = sys.executable
    bundled = {}

    try:
        os.makedirs(deps_dir, exist_ok=True)

        # Ensure pip is available, then upgrade it.
        subprocess.check_call([py_exec, "-m", "ensurepip", "--upgrade"])
        subprocess.check_call([py_exec, "-m", "pip", "install", "--upgrade", "pip"])

        # Tighter check: pin numpy to Blender's bundled version so pip reuses it
        # and selects a compatible pedpy, never installing a conflicting numpy.
        # No --upgrade, so already-satisfied requirements are left in place.
        bundled = _bundled_versions(py_exec)
        cmd = [py_exec, "-m", "pip", "install", "--target", deps_dir, "--no-user"]
        constraint_file = None
        numpy_ver = bundled.get("numpy")
        if numpy_ver:
            fd, constraint_file = tempfile.mkstemp(prefix="kinora_constraints_", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"numpy=={numpy_ver}\n")
            cmd += ["--constraint", constraint_file]
        cmd += ["pedpy", "fdsreader>=1.11.5"]

        try:
            subprocess.check_call(cmd, timeout=timeout)
        finally:
            if constraint_file and os.path.exists(constraint_file):
                os.remove(constraint_file)

        ok, detail = _verify_no_numpy_collision(deps_dir, bundled)
        if not ok:
            return False, detail

        return True, "Dependencies installed successfully"

    except subprocess.CalledProcessError as e:
        hint = ""
        numpy_ver = bundled.get("numpy")
        if numpy_ver:
            hint = (
                f" No pedpy release is compatible with Blender's bundled "
                f"numpy {numpy_ver} — install Kinora in a Blender whose bundled "
                "numpy pedpy supports."
            )
        return False, f"Failed to install dependencies: {e}.{hint}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def ensure_deps_in_path(addon_dir):
    """Make the local deps directory importable for packages Blender lacks.

    The deps dir is *appended* to ``sys.path`` (not prepended), so Blender's own
    bundled packages — numpy above all — always win over anything in deps.  The
    installer constrains deps to Blender's bundled numpy, so deps only needs to
    supply the genuinely-missing packages (pedpy and its non-Blender deps).

    We deliberately do NOT evict Blender's already-imported modules.  Swapping a
    live C-extension (numpy) under a running Blender is what previously crippled
    viewport performance.
    """
    deps_dir = get_deps_dir(addon_dir)
    if not os.path.exists(deps_dir):
        return
    if deps_dir not in sys.path:
        sys.path.append(deps_dir)


def is_pedpy_installed(addon_dir):
    """Check if pedpy is installed and importable."""
    import importlib.util

    ensure_deps_in_path(addon_dir)
    return importlib.util.find_spec("pedpy") is not None


def is_fdsreader_installed(addon_dir):
    """Check if fdsreader is installed and importable."""
    import importlib.util

    ensure_deps_in_path(addon_dir)
    return importlib.util.find_spec("fdsreader") is not None


def dependencies_installed(addon_dir):
    """
    True if pedpy is importable or was just installed into the addon deps dir.
    Used to grey out the install button and show restart prompt.
    """
    if is_pedpy_installed(addon_dir):
        return True
    pedpy_dir = os.path.join(get_deps_dir(addon_dir), "pedpy")
    return os.path.isdir(pedpy_dir)
