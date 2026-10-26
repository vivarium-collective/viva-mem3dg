# viva-mem3dg

Process-bigraph wrapper for [Mem3DG](https://github.com/RangamaniLabUCSD/Mem3DG), a membrane mechanics simulator using discrete differential geometry on triangulated surface meshes.

**[View Interactive Demo Report](https://vivarium-collective.github.io/viva-mem3dg/)** -- osmotic deflation, membrane patch bulging, and tubular constriction with 3D mesh viewers, Plotly charts, and bigraph architecture diagrams.

## What it does

Wraps the Mem3DG simulation engine as a `process-bigraph` Process, enabling membrane mechanics simulations to be composed with other biological processes in the bigraph framework. The wrapper uses the **bridge pattern**: it lazily initializes a Mem3DG `System` and `Euler` integrator internally, and on each `update()` call advances the simulation by the requested time interval using manual stepping.

## Installation

```bash
# System dependency (macOS)
brew install netcdf-cxx

# Install into a virtual environment
uv venv .venv && source .venv/bin/activate
uv pip install -e .
```

## Quick Start

```python
from process_bigraph import Composite, allocate_core, gather_emitter_results
from process_bigraph.emitter import RAMEmitter
from pbg_mem3dg import Mem3DGProcess, make_membrane_document

core = allocate_core()
core.register_link('Mem3DGProcess', Mem3DGProcess)
core.register_link('ram-emitter', RAMEmitter)

doc = make_membrane_document(
    mesh_type='icosphere',
    radius=1.0,
    subdivision=3,
    Kbc=8.22e-5,
    tension_modulus=0.1,
    osmotic_strength=0.02,
    preferred_volume_fraction=0.7,
    interval=100.0,
)

sim = Composite({'state': doc}, core=core)
sim.run(1000.0)

results = gather_emitter_results(sim)
emitter_data = results[('emitter',)]
for entry in emitter_data:
    print(f"t={entry['time']:.0f}  E={entry['total_energy']:.6f}  V={entry['volume']:.4f}")
```

## API Reference

### `Mem3DGProcess` (Process)

| Config | Type | Default | Description |
|--------|------|---------|-------------|
| `mesh_type` | string | `'icosphere'` | Initial mesh shape (`icosphere`, `hexagon`, `cylinder`) |
| `radius` | float | `1.0` | Mesh radius |
| `subdivision` | integer | `3` | Mesh subdivision level |
| `Kbc` | float | `8.22e-5` | Bending rigidity coefficient |
| `H0c` | float | `0.0` | Spontaneous curvature coefficient |
| `tension_modulus` | float | `0.1` | Surface tension modulus (harmonic model) |
| `preferred_area` | float | `0.0` | Preferred surface area (0 = auto from mesh) |
| `osmotic_strength` | float | `0.02` | Osmotic pressure strength |
| `preferred_volume_fraction` | float | `0.7` | Target volume as fraction of initial |
| `Kse`, `Ksl`, `Kst` | float | `0.0` | Spring regularization constants |
| `characteristic_timestep` | float | `2.0` | Euler integrator base timestep |
| `tolerance` | float | `1e-11` | Convergence tolerance |
| `shape_variation` | boolean | `True` | Enable shape evolution |
| `protein_variation` | boolean | `False` | Enable protein density evolution |

**Output ports** (all `overwrite` — absolute values, not deltas):

| Port | Type | Description |
|------|------|-------------|
| `vertex_positions` | list | Vertex coordinates `[[x,y,z], ...]` |
| `mean_curvatures` | list | Per-vertex mean curvature |
| `total_energy` | float | Total system energy |
| `bending_energy` | float | Bending (spontaneous curvature) energy |
| `surface_energy` | float | Surface tension energy |
| `pressure_energy` | float | Osmotic pressure energy |
| `surface_area` | float | Total surface area |
| `volume` | float | Enclosed volume |
| `converged` | boolean | Whether integrator reached tolerance |

### `make_membrane_document()`

Factory function that returns a composite document dict with:
- `membrane`: the `Mem3DGProcess` wired to stores
- `stores`: shared state
- `emitter`: RAM emitter collecting energy, area, volume time series

## Architecture

```
Composite
├── membrane (Mem3DGProcess)
│   └── internally manages:
│       ├── pymem3dg.System (mesh + physics)
│       └── pymem3dg.Euler (integrator)
├── stores/
│   ├── vertex_positions
│   ├── mean_curvatures
│   ├── total_energy, bending_energy, ...
│   ├── surface_area, volume
│   └── converged
└── emitter (RAMEmitter)
    └── records energy, area, volume vs time
```

The bridge pattern: on each `update(state, interval)`, the process advances Mem3DG's Euler integrator by `interval` time units using `march()`, then reads updated geometry and energy back into PBG output ports.

## Demo

```bash
python demo/demo_report.py
```

Produces `demo/demo_output.png` showing energy, surface area, and volume evolution during membrane relaxation.

## Tests

```bash
pytest tests/ -v
```
