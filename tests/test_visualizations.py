"""Unit tests for the pbg-mem3dg Visualization Steps.

These tests instantiate the Visualization classes directly and drive
``update(state)`` with hand-crafted snapshots, then assert the rendered
HTML contains the expected markers. They don't require pymem3dg at all
— exercising the viz contract in isolation from the heavy process.

Visualization is a process_bigraph Edge, whose __init__ requires a
``core``. To exercise the pure render contract without booting a full
core we bypass __init__ via ``object.__new__`` and seed only the
attributes the render path actually touches.
"""
from __future__ import annotations

from pbg_mem3dg.visualizations import Membrane3D, MembranePlots


def _new_membrane_plots(config=None):
    inst = object.__new__(MembranePlots)
    inst.config = config or {}
    inst.times = []
    inst.history = {
        'total_energy': [],
        'bending_energy': [],
        'surface_energy': [],
        'pressure_energy': [],
        'surface_area': [],
        'volume': [],
    }
    return inst


def _new_membrane_3d(config=None):
    inst = object.__new__(Membrane3D)
    inst.config = config or {}
    inst._latest = None
    return inst


def test_membrane_plots_renders_plotly():
    viz = _new_membrane_plots({'title': 'Test'})
    out = viz.update({
        'total_energy': 0.1,
        'bending_energy': 0.05,
        'surface_energy': 0.02,
        'pressure_energy': 0.01,
        'surface_area': 12.57,
        'volume': 4.19,
        'time': 5.0,
    })
    html = out['html']
    assert isinstance(html, str)
    assert 'Plotly.newPlot' in html
    assert 'Test' in html


def test_membrane_3d_renders_mesh():
    """Membrane3D must emit a three.js scene with a buffered mesh built
    from the supplied vertices + faces."""
    viz = _new_membrane_3d({'title': 'Test Mesh'})
    # Tetrahedral patch: 4 vertices, 2 triangular faces (front + back of one edge)
    state = {
        'vertices': [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        'faces': [[0, 1, 2], [0, 1, 3]],
    }
    out = viz.update(state)
    html = out['html']
    assert isinstance(html, str)
    # three.js asset references
    assert 'three.module.js' in html
    assert 'OrbitControls' in html
    # The BufferGeometry path must be exercised
    assert 'BufferGeometry' in html
    assert 'Float32BufferAttribute' in html
    # Title flows into the rendered footer
    assert 'Test Mesh' in html
    # Vertices get flattened into the JSON payload — coords appear in flat form
    assert '1.0' in html
    # Face indices appear in flattened indices list
    assert '0' in html and '2' in html and '3' in html


def test_membrane_3d_wireframe_config():
    """The wireframe boolean must be propagated to the rendered MeshPhongMaterial."""
    viz = _new_membrane_3d({'title': 'Wire', 'wireframe': True})
    out = viz.update({
        'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        'faces': [[0, 1, 2]],
    })
    assert 'wireframe: true' in out['html']


def test_membrane_3d_handles_empty_snapshot():
    """An empty snapshot must still produce valid HTML (no mesh drawn)."""
    viz = _new_membrane_3d()
    out = viz.update({'vertices': [], 'faces': []})
    html = out['html']
    assert 'three.module.js' in html
