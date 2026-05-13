"""Mem3DG composite documents + composite-spec discovery.

Two flavors of composite construction live in this package:

1. **Hand-coded factories** — `make_membrane_document(...)` builds a PBG
   state-dict programmatically for callers that want full control over the
   wiring. Used by the legacy demo report and the unit tests.

2. **Declarative `*.composite.yaml`** — sibling files in this directory follow
   the pbg-superpowers composite-spec convention. `build_composite()` loads
   one by name and instantiates `process_bigraph.Composite` with parameter
   substitution. The dashboard's composite explorer discovers these
   automatically once the package is installed in a workspace.

Both flavors are equivalent — pick the one that fits your use case.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any

import yaml
from process_bigraph import allocate_core
from process_bigraph.emitter import RAMEmitter

from pbg_mem3dg.processes import Mem3DGProcess


# ---------------------------------------------------------------------------
# Hand-coded composite factories (legacy / programmatic API)
# ---------------------------------------------------------------------------

def register_mem3dg(core=None):
    """Return a core with Mem3DGProcess, the RAM emitter, and the membrane
    Visualization registered."""
    if core is None:
        core = allocate_core()
    core.register_link('Mem3DGProcess', Mem3DGProcess)
    core.register_link('ram-emitter', RAMEmitter)
    # Register Visualization Steps so composites can wire them by name.
    from pbg_mem3dg.visualizations import MembranePlots, Membrane3D
    core.register_link('MembranePlots', MembranePlots)
    core.register_link('Membrane3D', Membrane3D)
    return core


def make_membrane_document(
    mesh_type='icosphere',
    radius=1.0,
    subdivision=3,
    Kbc=8.22e-5,
    tension_modulus=0.1,
    osmotic_strength=0.02,
    preferred_volume_fraction=0.7,
    characteristic_timestep=2.0,
    tolerance=1e-11,
    interval=10.0,
):
    """Create a composite document for a membrane mechanics simulation.

    Returns a document dict ready for use with Composite().

    Args:
        mesh_type: Initial mesh shape ('icosphere', 'hexagon', 'cylinder')
        radius: Mesh radius
        subdivision: Mesh subdivision level
        Kbc: Bending rigidity coefficient
        tension_modulus: Surface tension modulus
        osmotic_strength: Osmotic pressure strength
        preferred_volume_fraction: Fraction of initial volume as target
        characteristic_timestep: Euler integrator base timestep
        tolerance: Convergence tolerance
        interval: Time interval between process updates

    Returns:
        dict: Composite document with membrane process, stores, and emitter
    """
    return {
        'membrane': {
            '_type': 'process',
            'address': 'local:Mem3DGProcess',
            'config': {
                'mesh_type': mesh_type,
                'radius': radius,
                'subdivision': subdivision,
                'Kbc': Kbc,
                'tension_modulus': tension_modulus,
                'osmotic_strength': osmotic_strength,
                'preferred_volume_fraction': preferred_volume_fraction,
                'characteristic_timestep': characteristic_timestep,
                'tolerance': tolerance,
            },
            'interval': interval,
            'inputs': {},
            'outputs': {
                'vertex_positions': ['stores', 'vertex_positions'],
                'faces': ['stores', 'faces'],
                'mean_curvatures': ['stores', 'mean_curvatures'],
                'total_energy': ['stores', 'total_energy'],
                'bending_energy': ['stores', 'bending_energy'],
                'surface_energy': ['stores', 'surface_energy'],
                'pressure_energy': ['stores', 'pressure_energy'],
                'surface_area': ['stores', 'surface_area'],
                'volume': ['stores', 'volume'],
                'converged': ['stores', 'converged'],
            },
        },
        'stores': {},
        'emitter': {
            '_type': 'step',
            'address': 'local:ram-emitter',
            'config': {
                'emit': {
                    'total_energy': 'float',
                    'bending_energy': 'float',
                    'surface_energy': 'float',
                    'pressure_energy': 'float',
                    'surface_area': 'float',
                    'volume': 'float',
                    'time': 'float',
                },
            },
            'inputs': {
                'total_energy': ['stores', 'total_energy'],
                'bending_energy': ['stores', 'bending_energy'],
                'surface_energy': ['stores', 'surface_energy'],
                'pressure_energy': ['stores', 'pressure_energy'],
                'surface_area': ['stores', 'surface_area'],
                'volume': ['stores', 'volume'],
                'time': ['global_time'],
            },
        },
    }


# ---------------------------------------------------------------------------
# Declarative composite-spec loader (*.composite.yaml)
# ---------------------------------------------------------------------------

_COMPOSITES_DIR = Path(__file__).parent

_FULL_PLACEHOLDER = re.compile(r"^\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")
_INLINE_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _cast(value: Any, declared_type: str | None) -> Any:
    if declared_type is None:
        return value
    if declared_type == "float":
        return float(value)
    if declared_type == "int":
        return int(value)
    if declared_type in ("string", "str"):
        return str(value)
    if declared_type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    return value


def _substitute(state: Any, params: dict, overrides: dict) -> Any:
    if isinstance(state, dict):
        return {k: _substitute(v, params, overrides) for k, v in state.items()}
    if isinstance(state, list):
        return [_substitute(v, params, overrides) for v in state]
    if isinstance(state, str):
        m = _FULL_PLACEHOLDER.match(state)
        if m:
            pname = m.group(1)
            pdef = params.get(pname, {})
            raw = overrides.get(pname, pdef.get("default"))
            return _cast(raw, pdef.get("type"))
        if _INLINE_PLACEHOLDER.search(state):
            return _INLINE_PLACEHOLDER.sub(
                lambda mm: str(overrides.get(mm.group(1), params.get(mm.group(1), {}).get("default", ""))),
                state,
            )
    return state


def list_composite_specs() -> list[str]:
    """Return short names of every `*.composite.yaml` shipped in this package."""
    out: list[str] = []
    for path in sorted(_COMPOSITES_DIR.glob("*.composite.yaml")):
        out.append(path.name[: -len(".composite.yaml")])
    return out


def load_composite_spec(name: str) -> dict:
    """Load and parse a named composite spec. `name` is the stem (no suffix)."""
    path = _COMPOSITES_DIR / f"{name}.composite.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"composite spec not found: {path}")
    return yaml.safe_load(path.read_text())


def build_composite(name: str, *, overrides: dict | None = None, core=None):
    """Load a *.composite.yaml by name and instantiate process_bigraph.Composite.

    overrides: parameter overrides (keys must match spec.parameters)
    core:      optional pre-built core; otherwise register_mem3dg() is used
    """
    from process_bigraph import Composite

    spec = load_composite_spec(name)
    if not isinstance(spec, dict) or "state" not in spec or "name" not in spec:
        raise ValueError(f"composite '{name}' missing required keys (name, state)")

    if core is None:
        core = register_mem3dg()

    params = spec.get("parameters") or {}
    state = _substitute(spec.get("state") or {}, params, overrides or {})
    return Composite({"state": state}, core=core)
