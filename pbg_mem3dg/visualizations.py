"""Visualization Step subclasses for pbg-mem3dg.

Visualizations follow the pbg-superpowers convention (v0.4.15+):
each subclass overrides `update()` to consume per-step state via wires
(like an Emitter), accumulates history internally, and returns
``{'html': '<rendered figure>'}`` each step. The composite spec wires
the input ports to store paths.

See pbg_superpowers.visualization for the base-class contract.
"""
from __future__ import annotations

from pbg_superpowers.visualization import Visualization


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
    """three.js-based 3D viewer for the membrane mesh.

    Renders vertices + faces as a triangulated mesh, double-sided, with
    optional wireframe overlay. Mouse-orbitable camera (drag to rotate,
    scroll to zoom). Stores only the latest snapshot — a future
    'timeline' version can scrub through accumulated frames.
    """

    config_schema = {
        'title': {'_type': 'string', '_default': 'Membrane mesh'},
        'wireframe': {'_type': 'boolean', '_default': False},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._latest = None

    def inputs(self):
        return {
            'vertices': 'list[list[float]]',   # Nx3 coordinates
            'faces':    'list[list[integer]]', # Mx3 vertex indices
        }

    def update(self, state, interval=1.0):
        self._latest = {
            'vertices': state.get('vertices') or [],
            'faces': state.get('faces') or [],
        }
        return {'html': self._render()}

    def _render(self) -> str:
        import json
        title = (self.config or {}).get('title', 'Membrane mesh')
        wireframe = bool((self.config or {}).get('wireframe', False))
        data = self._latest or {'vertices': [], 'faces': []}
        verts = data.get('vertices') or []
        faces = data.get('faces') or []
        # Pre-flatten for three.js BufferGeometry. Vertices are guaranteed
        # 3D (Mem3DG); for safety we pad anything shorter to length 3.
        positions_flat = [c for v in verts for c in (list(v) + [0.0, 0.0, 0.0])[:3]]
        indices_flat = [int(i) for f in faces for i in list(f)[:3]]
        data_json = json.dumps({'positions': positions_flat, 'indices': indices_flat})
        return (
            '<div id="viz" style="width:100%;height:480px;border:1px solid #e5e7eb;border-radius:4px"></div>'
            '<script type="importmap">'
            '{"imports": {"three": "https://unpkg.com/three@0.158.0/build/three.module.js",'
            ' "three/addons/": "https://unpkg.com/three@0.158.0/examples/jsm/"}}'
            '</script>'
            '<script type="module">'
            'import * as THREE from "three";'
            'import { OrbitControls } from "three/addons/controls/OrbitControls.js";'
            'const data = ' + data_json + ';'
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
            'if (data.positions.length && data.indices.length) {'
            '  const geom = new THREE.BufferGeometry();'
            '  geom.setAttribute("position", new THREE.Float32BufferAttribute(data.positions, 3));'
            '  geom.setIndex(data.indices);'
            '  geom.computeVertexNormals();'
            '  const mat = new THREE.MeshPhongMaterial({color: 0x60a5fa, side: THREE.DoubleSide, wireframe: ' + ('true' if wireframe else 'false') + '});'
            '  scene.add(new THREE.Mesh(geom, mat));'
            '}'
            'function animate() { requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }'
            'animate();'
            '</script>'
            '<div style="font-size:0.85em;color:#6b7280;margin-top:4px">' + title + ' — drag to rotate, scroll to zoom</div>'
        )
