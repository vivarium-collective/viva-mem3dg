"""Mem3DG Process wrapper for process-bigraph.

Wraps the Mem3DG membrane mechanics simulator as a time-driven Process
using the bridge pattern. The internal Mem3DG System and Euler integrator
are lazily initialized on first update() call.
"""

import os
import tempfile
import numpy as np
from process_bigraph import Process


def _write_ply(vertex, face, path):
    """Write vertex/face arrays to an ASCII PLY file."""
    with open(path, 'w') as f:
        f.write('ply\nformat ascii 1.0\n')
        f.write(f'element vertex {vertex.shape[0]}\n')
        f.write('property float x\nproperty float y\nproperty float z\n')
        f.write(f'element face {face.shape[0]}\n')
        f.write('property list uchar int vertex_indices\n')
        f.write('end_header\n')
        for v in vertex:
            f.write(f'{v[0]} {v[1]} {v[2]}\n')
        for fc in face:
            f.write(f'3 {fc[0]} {fc[1]} {fc[2]}\n')


class Mem3DGProcess(Process):
    """Bridge Process wrapping Mem3DG membrane mechanics simulation.

    Simulates lipid membrane dynamics on a triangulated surface mesh
    using discrete differential geometry. On each update(), advances
    the Mem3DG Euler integrator by the requested time interval using
    manual stepping (march()), then returns updated geometry and energy
    as deltas or overwrites.

    Config:
        mesh_type: Initial mesh shape ('icosphere', 'hexagon', 'cylinder')
        radius: Mesh radius
        subdivision: Mesh subdivision level
        Kbc: Bending rigidity coefficient
        H0c: Spontaneous curvature coefficient
        tension_modulus: Surface tension modulus (harmonic model)
        preferred_area: Preferred surface area (0 = auto from initial mesh)
        osmotic_strength: Osmotic pressure strength (harmonic model)
        preferred_volume_fraction: Fraction of initial volume as preferred
        Kse, Ksl, Kst: Spring regularization constants
        characteristic_timestep: Euler integrator base timestep
        tolerance: Convergence tolerance
        shape_variation: Enable shape evolution
        protein_variation: Enable protein density evolution
    """

    config_schema = {
        # Mesh initialization
        'mesh_type': {'_type': 'string', '_default': 'icosphere'},
        'radius': {'_type': 'float', '_default': 1.0},
        'subdivision': {'_type': 'integer', '_default': 3},
        'axial_subdivision': {'_type': 'integer', '_default': 0},
        # Bending
        'Kbc': {'_type': 'float', '_default': 8.22e-5},
        'H0c': {'_type': 'float', '_default': 0.0},
        # Tension (harmonic preferred-area model)
        'tension_modulus': {'_type': 'float', '_default': 0.1},
        'preferred_area': {'_type': 'float', '_default': 0.0},
        'preferred_area_scale': {'_type': 'float', '_default': 1.0},
        # Osmotic pressure
        'osmotic_model': {'_type': 'string', '_default': 'preferred_volume'},
        'osmotic_strength': {'_type': 'float', '_default': 0.02},
        'osmotic_pressure': {'_type': 'float', '_default': 0.0},
        'preferred_volume_fraction': {'_type': 'float', '_default': 0.7},
        # Spring regularization
        'Kse': {'_type': 'float', '_default': 0.0},
        'Ksl': {'_type': 'float', '_default': 0.0},
        'Kst': {'_type': 'float', '_default': 0.0},
        # Boundary conditions
        'boundary_condition': {'_type': 'string', '_default': 'none'},
        # Integrator
        'characteristic_timestep': {'_type': 'float', '_default': 2.0},
        'tolerance': {'_type': 'float', '_default': 1e-11},
        # Variation control
        'shape_variation': {'_type': 'boolean', '_default': True},
        'protein_variation': {'_type': 'boolean', '_default': False},
    }

    def __init__(self, config=None, core=None):
        super().__init__(config=config, core=core)
        self._system = None
        self._geometry = None  # keep reference (tracks System state in-place)
        self._integrator = None
        self._output_dir = None
        self._ply_path = None
        self._converged = False
        # Track the osmotic-strength offset currently baked into the System.
        # When the input port reports a new value, _maybe_rebuild() snapshots
        # the current vertex positions, destroys the System, and rebuilds it
        # with the new effective osmotic_strength.
        self._current_osmotic_offset = 0.0

    def inputs(self):
        # `osmotic_strength_offset` is a runtime modulator added on top of
        # the cfg['osmotic_strength'] baked in at construction. A sibling
        # process (e.g. an actin simulator computing how hard filaments are
        # pushing on the membrane) can write here to drive bulge dynamics.
        # Treated as 0.0 when missing, so existing demos keep working
        # unchanged.
        return {'osmotic_strength_offset': 'float'}

    def outputs(self):
        return {
            'vertex_positions': 'overwrite[list]',
            'faces': 'overwrite[list]',
            'mean_curvatures': 'overwrite[list]',
            'total_energy': 'overwrite[float]',
            'bending_energy': 'overwrite[float]',
            'surface_energy': 'overwrite[float]',
            'pressure_energy': 'overwrite[float]',
            'surface_area': 'overwrite[float]',
            'volume': 'overwrite[float]',
            'converged': 'overwrite[boolean]',
        }

    def _read_state(self):
        """Read current geometry and energy from the Mem3DG System."""
        energy = self._system.getEnergy()
        vertex = self._geometry.getVertexMatrix()
        H = self._geometry.getVertexMeanCurvatures()
        # Face connectivity is invariant for the lifetime of a Mem3DG
        # geometry (no remeshing in this wrapper), but emitting it on
        # every step keeps the downstream wires (e.g. 3D mesh viewer)
        # self-contained — they don't need a separate one-shot capture.
        faces = self._geometry.getFaceMatrix().tolist()
        return {
            'vertex_positions': vertex.tolist(),
            'faces': faces,
            'mean_curvatures': H.tolist(),
            'total_energy': float(energy.totalEnergy),
            'bending_energy': float(energy.spontaneousCurvatureEnergy),
            'surface_energy': float(energy.surfaceEnergy),
            'pressure_energy': float(energy.pressureEnergy),
            'surface_area': float(self._geometry.getSurfaceArea()),
            'volume': float(self._geometry.getVolume()),
            'converged': self._converged,
        }

    def initial_state(self):
        self._build_system()
        return self._read_state()

    def get_faces(self):
        """Return the face connectivity array as a list of [i, j, k] triples.

        Must be called after initial_state() or update() has triggered
        system initialization.
        """
        self._build_system()
        return self._geometry.getFaceMatrix().tolist()

    def _build_system(self, override_vertex=None, override_face=None,
                      osmotic_offset=0.0):
        """Initialize (or rebuild) the Mem3DG System and Euler integrator.

        On first call: generates the configured mesh and builds System with
        cfg['osmotic_strength'].

        On subsequent calls (rebuild path used by the runtime
        osmotic_strength_offset input): the caller supplies the current
        vertex/face matrices and a new effective osmotic_strength, and a
        fresh System is built around them. The previous System and integrator
        are discarded.
        """
        if self._system is not None and override_vertex is None:
            return

        import pymem3dg as dg
        import pymem3dg.boilerplate as dgb
        from functools import partial

        cfg = self.config

        if override_vertex is not None and override_face is not None:
            face = np.asarray(override_face, dtype=np.int64)
            vertex = np.asarray(override_vertex, dtype=np.float64)
        elif cfg['mesh_type'] == 'icosphere':
            face, vertex = dg.getIcosphere(
                radius=cfg['radius'],
                subdivision=cfg['subdivision'])
        elif cfg['mesh_type'] == 'hexagon':
            face, vertex = dg.getHexagon(
                radius=cfg['radius'],
                subdivision=cfg['subdivision'])
        elif cfg['mesh_type'] == 'cylinder':
            axial = cfg['axial_subdivision'] or cfg['subdivision'] * 2
            face, vertex = dg.getCylinder(
                radius=cfg['radius'],
                radialSubdivision=cfg['subdivision'],
                axialSubdivision=axial)
        else:
            raise ValueError(
                f"Unknown mesh_type: {cfg['mesh_type']}. "
                f"Use 'icosphere', 'hexagon', or 'cylinder'.")

        # Write PLY (workaround for Geometry array constructor segfault).
        # Same path is used for both first build and rebuild — pymem3dg's
        # Geometry constructor accepts only a file, not direct arrays.
        if self._ply_path and os.path.exists(self._ply_path):
            os.unlink(self._ply_path)
        self._ply_path = tempfile.mktemp(suffix='.ply')
        _write_ply(vertex, face, self._ply_path)
        geo = dg.Geometry(self._ply_path)
        self._geometry = geo  # keep reference; tracks System state in-place

        # Parameters
        p = dg.Parameters()
        p.bending.Kbc = cfg['Kbc']
        p.bending.H0c = cfg['H0c']
        p.spring.Kse = cfg['Kse']
        p.spring.Ksl = cfg['Ksl']
        p.spring.Kst = cfg['Kst']
        p.variation.isShapeVariation = cfg['shape_variation']
        p.variation.isProteinVariation = cfg['protein_variation']

        # Tension model
        sa = geo.getSurfaceArea()
        if cfg['preferred_area'] > 0:
            preferred_area = cfg['preferred_area']
        else:
            preferred_area = sa * cfg['preferred_area_scale']
        p.tension.form = partial(
            dgb.preferredAreaSurfaceTensionModel,
            modulus=cfg['tension_modulus'],
            preferredArea=preferred_area)

        # Osmotic model. The runtime input port `osmotic_strength_offset`
        # is a polymorphic modulator that adds to either `osmotic_strength`
        # (preferred_volume model) or `osmotic_pressure` (constant model).
        # We install a CLOSURE on `p.osmotic.form` that reads the live
        # value of self._current_osmotic_offset on every integrator step
        # — that way a sibling process can drive the offset every PBG
        # update without forcing a costly System+Euler rebuild. Existing
        # callers that don't drive the input get the same defaults.
        self._current_osmotic_offset = float(osmotic_offset)
        vol = geo.getVolume()
        if cfg['osmotic_model'] == 'constant':
            baseline_pressure = cfg['osmotic_pressure']

            def _osmotic_constant(volume):
                return dgb.constantOsmoticPressureModel(
                    volume=volume,
                    pressure=baseline_pressure + self._current_osmotic_offset,
                )
            # Strong refs prevent the C++ PyCapsule from outliving the closure.
            self._osmotic_form_callback = _osmotic_constant
            p.osmotic.form = _osmotic_constant
        else:
            baseline_strength = cfg['osmotic_strength']
            preferred_vol = cfg['preferred_volume_fraction'] * vol

            def _osmotic_preferred(volume):
                return dgb.preferredVolumeOsmoticPressureModel(
                    volume=volume,
                    preferredVolume=preferred_vol,
                    reservoirVolume=0,
                    strength=baseline_strength + self._current_osmotic_offset,
                )
            self._osmotic_form_callback = _osmotic_preferred
            p.osmotic.form = _osmotic_preferred

        # Boundary conditions
        if cfg['boundary_condition'] != 'none':
            p.boundary.shapeBoundaryCondition = cfg['boundary_condition']

        # Discard any prior output dir (rebuild path) before allocating new.
        if self._output_dir and os.path.exists(self._output_dir):
            import shutil
            shutil.rmtree(self._output_dir, ignore_errors=True)

        # Build System
        self._system = dg.System(geometry=geo, parameters=p)
        self._system.initialize()

        # Build Euler integrator for manual stepping
        self._output_dir = tempfile.mkdtemp(prefix='mem3dg_')
        self._integrator = dg.Euler(
            system=self._system,
            characteristicTimeStep=cfg['characteristic_timestep'],
            tolerance=cfg['tolerance'],
            outputDirectory=self._output_dir)
        # Convergence flag must be cleared on rebuild — a previously
        # converged integrator on the old osmotic strength may not be
        # converged on the new one.
        self._converged = False

    def update(self, state, interval):
        self._build_system()

        # Runtime osmotic-strength input. The closure installed on
        # p.osmotic.form during _build_system reads self._current_osmotic_offset
        # live on every integrator step, so we just stash the new value
        # here — no System rebuild needed. We DO rebuild the Euler
        # integrator (cheap — System and Geometry are preserved), because
        # its EXIT flag is read-only and gets latched True after the first
        # convergence; without resetting it, subsequent offset changes
        # would never trigger fresh integration. The geometry's current
        # vertex positions ARE the new starting state for the new
        # integrator, so this is a "warm restart" rather than a true rebuild.
        import pymem3dg as dg
        new_offset = float(state.get('osmotic_strength_offset', 0.0))
        if abs(new_offset - self._current_osmotic_offset) > 1e-12:
            self._current_osmotic_offset = new_offset
            self._converged = False
            self._integrator = dg.Euler(
                system=self._system,
                characteristicTimeStep=self.config['characteristic_timestep'],
                tolerance=self.config['tolerance'],
                outputDirectory=self._output_dir,
            )

        if self._converged:
            return {}

        # Advance by interval using manual stepping
        target_time = self._system.time + interval
        dt = self.config['characteristic_timestep']
        while self._system.time < target_time - 1e-12:
            self._integrator.status()
            if self._integrator.EXIT:
                self._converged = True
                break
            self._integrator.march()

        return self._read_state()

    def __del__(self):
        """Clean up temporary files."""
        try:
            import shutil
            if self._ply_path and os.path.exists(self._ply_path):
                os.unlink(self._ply_path)
            if self._output_dir and os.path.exists(self._output_dir):
                shutil.rmtree(self._output_dir, ignore_errors=True)
        except (ImportError, TypeError):
            pass  # interpreter shutting down
