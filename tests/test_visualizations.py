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
    inst._history = []
    inst._faces = []
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
    """An empty snapshot must still produce valid placeholder HTML (no mesh drawn)."""
    viz = _new_membrane_3d()
    out = viz.update({'vertices': [], 'faces': []})
    html = out['html']
    # No frames yet → placeholder, not a full three.js scene.
    assert '<div id="viz"' in html
    assert 'No frames yet' in html


def test_membrane_3d_accumulates_frames():
    """Each update() call must append a frame to _history; faces captured once."""
    viz = _new_membrane_3d({'title': 'Acc'})
    base = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    faces = [[0, 1, 2]]
    for i in range(5):
        # Translate vertices to simulate motion across ticks
        verts = [[v[0] + i * 0.1, v[1], v[2]] for v in base]
        viz.update({'vertices': verts, 'faces': faces})
    assert len(viz._history) == 5
    # Faces captured once and stable
    assert viz._faces == [[0, 1, 2]]


def test_membrane_3d_renders_play_controls():
    """With >1 frames the HTML must include play/pause + slider + frames JSON."""
    viz = _new_membrane_3d({'title': 'Ctrls'})
    verts0 = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    faces = [[0, 1, 2]]
    for i in range(3):
        verts = [[v[0] + i * 0.1, v[1], v[2]] for v in verts0]
        out = viz.update({'vertices': verts, 'faces': faces})
    html = out['html']
    assert 'id="play-btn"' in html
    assert 'id="frame-slider"' in html
    assert 'id="frame-label"' in html
    # Frames JSON has 3 entries — verify by parsing the embedded data blob
    import re
    import json as _json
    match = re.search(r'const data = (\{.*?\});', html)
    assert match, 'data JSON blob not found in rendered HTML'
    data = _json.loads(match.group(1))
    assert len(data['frames']) == 3
    # Each frame is N*3 floats (3 vertices × 3 coords)
    assert all(len(f) == 9 for f in data['frames'])


def test_membrane_3d_sampling_when_history_too_large():
    """A 200-frame history of a moderate mesh must be sampled down so the
    embedded frames JSON stays bounded."""
    viz = _new_membrane_3d({'title': 'Big'})
    # ~500-vertex mesh, 200 frames → unsampled payload would be ~4-5 MB
    n_verts = 500
    base = [[float(j % 10), float((j // 10) % 10), 0.0] for j in range(n_verts)]
    faces = [[0, 1, 2]]
    for i in range(200):
        verts = [[v[0] + i * 0.001, v[1], v[2]] for v in base]
        out = viz.update({'vertices': verts, 'faces': faces})
    # _history still records every tick — sampling is a render-time concern.
    assert len(viz._history) == 200

    html = out['html']
    import re
    import json as _json
    match = re.search(r'const data = (\{.*?\});', html)
    assert match
    data = _json.loads(match.group(1))
    # Threshold check: sampled-down to fewer frames than recorded.
    # Stride-sampling can leave up to (max_frames + 1) entries when we force-
    # append the final frame; allow a small slack around the ~100-frame budget.
    assert len(data['frames']) <= 110
    # And substantially fewer than 200 — confirm sampling actually fired.
    assert len(data['frames']) < 200


def test_membrane_3d_single_frame_hides_controls():
    """A single-frame history should hide the play controls (display:none)."""
    viz = _new_membrane_3d()
    viz.update({'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                'faces': [[0, 1, 2]]})
    html = viz._render()
    assert 'display:none' in html
    # And only one frame in the JSON payload
    import re
    import json as _json
    match = re.search(r'const data = (\{.*?\});', html)
    assert match
    assert len(_json.loads(match.group(1))['frames']) == 1


def test_membrane_3d_skips_malformed_frames():
    """Frames whose vertex count diverges from frame 0 are dropped at render time."""
    viz = _new_membrane_3d()
    faces = [[0, 1, 2]]
    viz.update({'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                'faces': faces})
    # Inject a malformed frame directly (4 vertices instead of 3)
    viz._history.append([[0.0, 0.0, 0.0]] * 4)
    viz.update({'vertices': [[0.0, 0.0, 0.5], [1.0, 0.0, 0.5], [0.0, 1.0, 0.5]],
                'faces': faces})
    html = viz._render()
    import re
    import json as _json
    data = _json.loads(re.search(r'const data = (\{.*?\});', html).group(1))
    assert len(data['frames']) == 2  # malformed frame skipped
