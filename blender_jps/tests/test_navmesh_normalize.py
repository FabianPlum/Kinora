"""Unit test for ``navmesh.normalize_levels_arg``.

Runs under plain CPython (no Blender). The function is pure logic but
lives in a module that imports ``bpy`` / ``bmesh`` at the top, so we
stub those in ``sys.modules`` before importing.

This is the seam where v3 multi-floor will plug in — a fast regression
guard for the single-floor ↔ multi-floor adapter.
"""

from __future__ import annotations

import os
import sys
import types
import unittest


def _load_normalize_levels_arg():
    """Load ``normalize_levels_arg`` without dragging the full addon in.

    The function is pure logic, but its module imports ``bpy`` /
    ``bmesh`` and the package's ``__init__`` runs a lot of Blender
    setup. We stub the Blender modules, fake out the ``geometry``
    sibling so the relative import resolves, then load ``navmesh.py``
    directly from disk.
    """
    import importlib.util

    here = os.path.abspath(os.path.dirname(__file__))
    addon_dir = os.path.abspath(os.path.join(here, ".."))

    for name in ("bpy", "bpy.types", "bpy.props", "bpy.app", "bmesh"):
        sys.modules.setdefault(name, types.ModuleType(name))

    # Minimal package hierarchy so `from .geometry import ...` resolves
    # without executing the real package __init__.
    pkg = types.ModuleType("blender_jps")
    pkg.__path__ = [addon_dir]
    sys.modules["blender_jps"] = pkg
    core = types.ModuleType("blender_jps.core")
    core.__path__ = [os.path.join(addon_dir, "core")]
    sys.modules["blender_jps.core"] = core
    geometry = types.ModuleType("blender_jps.core.geometry")
    geometry.assign_material = lambda *a, **kw: None
    geometry.get_or_create_material = lambda *a, **kw: None
    sys.modules["blender_jps.core.geometry"] = geometry

    spec = importlib.util.spec_from_file_location(
        "blender_jps.core.navmesh", os.path.join(addon_dir, "core", "navmesh.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["blender_jps.core.navmesh"] = module
    spec.loader.exec_module(module)
    return module.normalize_levels_arg


class _FakePolygon:
    """Just enough to look like a shapely polygon to the adapter."""

    is_empty = False


class _ObjectWithPolygon:
    def __init__(self, polygon):
        self.polygon = polygon


class NormalizeLevelsArgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normalize = staticmethod(_load_normalize_levels_arg())

    def test_v3_list_of_dicts_passes_through(self):
        levels = [
            {"id": 0, "z": 0.0, "polygon": _FakePolygon()},
            {"id": 1, "z": 3.5, "polygon": _FakePolygon()},
        ]
        self.assertIs(self.normalize(levels), levels)

    def test_empty_list_returns_empty(self):
        self.assertEqual(self.normalize([]), [])

    def test_bare_polygon_wraps_to_single_level(self):
        polygon = _FakePolygon()
        result = self.normalize(polygon)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 0)
        self.assertEqual(result[0]["z"], 0.0)
        self.assertIs(result[0]["polygon"], polygon)

    def test_object_with_polygon_attr_is_unwrapped(self):
        polygon = _FakePolygon()
        wrapper = _ObjectWithPolygon(polygon)
        result = self.normalize(wrapper)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0]["polygon"], polygon)


if __name__ == "__main__":
    unittest.main()
