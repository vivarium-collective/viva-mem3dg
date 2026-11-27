"""Skip tests that drive the real Mem3DG engine when pymem3dg isn't importable.

pymem3dg builds from CMake source and is not reliably pip-installable on a vanilla CI
runner, so processes.py imports it lazily. These tests pass where the engine is built;
elsewhere the engine-driving modules are skipped so the import/structure checks still
gate the PR.
"""
import pytest

_ENGINE_MODULES = ("test_composites", "test_processes", "test_core_registration", "test_visualizations")


def _mem3dg_available() -> bool:
    try:
        import pymem3dg  # noqa: F401
        import pymem3dg.boilerplate  # noqa: F401
        return True
    except Exception:
        return False


_AVAILABLE = _mem3dg_available()


def pytest_collection_modifyitems(config, items):
    if _AVAILABLE:
        return
    skip = pytest.mark.skip(reason="Mem3DG engine (pymem3dg) not available on this runner")
    for item in items:
        if any(mod in item.nodeid for mod in _ENGINE_MODULES):
            item.add_marker(skip)
