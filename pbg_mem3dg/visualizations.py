"""Visualization Step subclasses for pbg-mem3dg.

Visualizations follow the pbg-superpowers convention (v0.4.15+):
each subclass overrides `update()` to consume per-step state via wires
(like an Emitter), accumulates history internally, and returns
``{'html': '<rendered figure>'}`` each step. The composite spec wires
the input ports to store paths.

See pbg_superpowers.visualization for the base-class contract.
"""
from __future__ import annotations

import json

from pbg_superpowers.visualization import Visualization


# Soft cap on embedded frame data (bytes). Above this we sample-down frames
# so the rendered HTML stays manageable for the dashboard.
_MAX_FRAMES_BYTES = 2 * 1024 * 1024  # ~2 MB


class MembranePlots(Visualization):
    """Time-series HTML plot of Mem3DG's scalar membrane outputs.

    Consumes the six core Mem3DG scalars (total_energy, bending_energy,
    surface_energy, pressure_energy, surface_area, volume) at each step,
    accumulates them across calls, and emits a Plotly HTML figure on
    every update. Downstream consumers (dashboards, notebook viewers)
    read the latest 'html' from the wired store.
    """

    config_schema = {
        'title': {'_type': 'string', '_default': 'Mem3DG membrane relaxation'},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # One list per consumed scalar; aligned by index across all signals.
        self.times: list[float] = []
        self.history: dict[str, list[float]] = {
            'total_energy': [],
            'bending_energy': [],
            'surface_energy': [],
            'pressure_energy': [],
            'surface_area': [],
            'volume': [],
        }

    def inputs(self):
        return {
            'total_energy': 'float',
            'bending_energy': 'float',
            'surface_energy': 'float',
            'pressure_energy': 'float',
            'surface_area': 'float',
            'volume': 'float',
            'time': 'float',
        }

    def update(self, state, interval=1.0):
        self.times.append(float(state.get('time', len(self.times) * (interval or 1.0))))
        for key in self.history:
            v = state.get(key)
            self.history[key].append(float(v) if v is not None else 0.0)

        title = (self.config or {}).get('title', 'Mem3DG membrane relaxation')
        traces = []
        for key, ys in self.history.items():
            traces.append(
                '{"x":' + repr(self.times) + ',"y":' + repr(ys) +
                ',"type":"scatter","mode":"lines","name":"' + key + '"}'
            )
        html = (
            f'<div id="mem3dg-mp" style="height:380px"></div>'
            f'<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>'
            f'<script>Plotly.newPlot("mem3dg-mp",[{",".join(traces)}],'
            f'{{title:"{title}",margin:{{l:55,r:15,t:35,b:40}},'
            f'xaxis:{{title:"time"}},'
            f'legend:{{orientation:"h",y:-0.2}}}},'
            f'{{responsive:true,displayModeBar:false}});</script>'
        )
        return {'html': html}


class Membrane3D(Visualization):
    """three.js-based 3D viewer for the membrane mesh, animated over time.

    Accumulates vertex positions across calls into ``self._history`` and
    renders a three.js scene with a play/pause control + frame slider so the
    user can scrub through the simulated deformation. Mesh topology (faces)
    is captured once on the first update — Mem3DG never re-triangulates.

    For long runs the embedded frame data is sampled down to keep the HTML
    payload bounded (see ``_MAX_FRAMES_BYTES``).
    """

    config_schema = {
        'title': {'_type': 'string', '_default': 'Membrane mesh'},
        'wireframe': {'_type': 'boolean', '_default': False},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._history: list[list[list[float]]] = []  # per-tick vertex positions
        self._faces: list[list[int]] = []            # captured once

    def inputs(self):
        return {
            'vertices': 'list[list[float]]',   # Nx3 coordinates
            'faces':    'list[list[integer]]', # Mx3 vertex indices
        }

    def update(self, state, interval=1.0):
        verts = state.get('vertices') or []
        faces = state.get('faces') or []
        if not self._faces and faces:
            # Capture topology once — Mem3DG keeps the connectivity fixed.
            self._faces = [list(f) for f in faces]
        if verts:
            self._history.append([list(v) for v in verts])
        return {'html': self._render()}

    def _render(self) -> str:
        title = (self.config or {}).get('title', 'Membrane mesh')
        wireframe = bool((self.config or {}).get('wireframe', False))

        # No frames yet → empty placeholder.
        if not self._history or not self._faces:
            return (
                '<div id="viz" style="width:100%;height:480px;border:1px solid #e5e7eb;'
                'border-radius:4px;display:flex;align-items:center;justify-content:center;'
                'color:#9ca3af;font-family:sans-serif">No frames yet</div>'
                f'<div style="font-size:0.85em;color:#6b7280;margin-top:4px">{title}</div>'
            )

        # Flatten faces into a single triangle-index list (constant across frames).
        indices_flat: list[int] = [int(i) for f in self._faces for i in list(f)[:3]]

        # Build per-frame flat position lists, skipping malformed frames whose
        # vertex count diverges from frame 0.
        n0 = len(self._history[0])
        frames_flat: list[list[float]] = []
        for verts in self._history:
            if len(verts) != n0:
                continue
            flat: list[float] = []
            for v in verts:
                xyz = (list(v) + [0.0, 0.0, 0.0])[:3]
                flat.extend(float(c) for c in xyz)
            frames_flat.append(flat)

        # Cap embedded payload size by uniformly down-sampling frames.
        frames_flat = _sample_frames(frames_flat, _MAX_FRAMES_BYTES)

        n_frames = len(frames_flat)
        data_json = json.dumps({'frames': frames_flat, 'indices': indices_flat})

        # Single-frame: hide controls — there's nothing to animate.
        controls_visible = n_frames > 1
        controls_html = (
            f'<div style="display:flex;align-items:center;gap:10px;margin-top:6px;font-size:0.88em">'
            f'<button id="play-btn" style="width:32px">&#9208;</button>'
            f'<input type="range" id="frame-slider" min="0" max="{n_frames - 1}" value="0" style="flex:1">'
            f'<span id="frame-label" style="font-family:monospace">1 / {n_frames}</span>'
            f'</div>'
        ) if controls_visible else (
            f'<div style="display:none">'
            f'<button id="play-btn"></button>'
            f'<input type="range" id="frame-slider" min="0" max="0" value="0">'
            f'<span id="frame-label">1 / 1</span>'
            f'</div>'
        )

        return (
            '<div id="viz" style="width:100%;height:480px;border:1px solid #e5e7eb;border-radius:4px"></div>'
            + controls_html +
            '<script type="importmap">'
            '{"imports": {"three": "https://unpkg.com/three@0.158.0/build/three.module.js",'
            ' "three/addons/": "https://unpkg.com/three@0.158.0/examples/jsm/"}}'
            '</script>'
            '<script type="module">'
            'import * as THREE from "three";'
            'import { OrbitControls } from "three/addons/controls/OrbitControls.js";'
            'const data = ' + data_json + ';'
            'const frames = data.frames;'
            'const indices = data.indices;'
            'let currentFrame = 0;'
            'let playing = ' + ('true' if controls_visible else 'false') + ';'
            'const container = document.getElementById("viz");'
            'const renderer = new THREE.WebGLRenderer({antialias:true});'
            'renderer.setSize(container.clientWidth, 480);'
            'renderer.setClearColor(0xffffff, 1);'
            'container.appendChild(renderer.domElement);'
            'const scene = new THREE.Scene();'
            'const camera = new THREE.PerspectiveCamera(60, container.clientWidth/480, 0.01, 1000);'
            'camera.position.set(3, 3, 3);'
            'const controls = new OrbitControls(camera, renderer.domElement);'
            'scene.add(new THREE.AmbientLight(0xffffff, 0.5));'
            'const sun = new THREE.DirectionalLight(0xffffff, 0.8);'
            'sun.position.set(5,10,5);'
            'scene.add(sun);'
            'const geom = new THREE.BufferGeometry();'
            'geom.setAttribute("position", new THREE.Float32BufferAttribute(frames[0].slice(), 3));'
            'geom.setIndex(indices);'
            'geom.computeVertexNormals();'
            'const mat = new THREE.MeshPhongMaterial({color: 0x60a5fa, side: THREE.DoubleSide, wireframe: ' + ('true' if wireframe else 'false') + '});'
            'scene.add(new THREE.Mesh(geom, mat));'
            'function setFrame(idx) {'
            '  if (idx < 0 || idx >= frames.length) return;'
            '  currentFrame = idx;'
            '  const attr = geom.attributes.position;'
            '  attr.array.set(frames[currentFrame]);'
            '  attr.needsUpdate = true;'
            '  geom.computeVertexNormals();'
            '  const slider = document.getElementById("frame-slider");'
            '  if (slider) slider.value = currentFrame;'
            '  const label = document.getElementById("frame-label");'
            '  if (label) label.textContent = (currentFrame + 1) + " / " + frames.length;'
            '}'
            'function advance() {'
            '  if (!playing || frames.length <= 1) return;'
            '  setFrame((currentFrame + 1) % frames.length);'
            '}'
            'setInterval(advance, 167);'
            'const playBtn = document.getElementById("play-btn");'
            'if (playBtn) {'
            '  playBtn.addEventListener("click", () => {'
            '    playing = !playing;'
            '    playBtn.innerHTML = playing ? "&#9208;" : "&#9654;";'
            '  });'
            '}'
            'const slider = document.getElementById("frame-slider");'
            'if (slider) {'
            '  slider.addEventListener("input", (e) => {'
            '    playing = false;'
            '    if (playBtn) playBtn.innerHTML = "&#9654;";'
            '    setFrame(parseInt(e.target.value, 10));'
            '  });'
            '}'
            'function animate() { requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }'
            'animate();'
            '</script>'
            '<div style="font-size:0.85em;color:#6b7280;margin-top:4px">' + title + f' &mdash; {n_frames} frame' + ('s' if n_frames != 1 else '') + ', drag to rotate, scroll to zoom</div>'
        )


def _sample_frames(frames: list[list[float]], max_bytes: int) -> list[list[float]]:
    """Return ``frames`` sampled uniformly down to keep the JSON payload
    under ``max_bytes``. We use a coarse byte-per-float estimate to pick the
    stride; a single frame is always kept.
    """
    if not frames:
        return frames
    # ~16 bytes per float in JSON ("0.12345678901234, ").
    per_frame_bytes = 16 * len(frames[0]) if frames[0] else 16
    if per_frame_bytes <= 0:
        return frames
    total = per_frame_bytes * len(frames)
    if total <= max_bytes:
        return frames
    max_frames = max(1, max_bytes // per_frame_bytes)
    if len(frames) <= max_frames:
        return frames
    stride = max(2, len(frames) // max_frames)
    sampled = frames[::stride]
    # Ensure the last frame is always included so the user sees the end state.
    if sampled and sampled[-1] is not frames[-1]:
        sampled.append(frames[-1])
    return sampled
