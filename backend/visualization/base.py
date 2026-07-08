"""Abstract visualization backend. Swap implementations without changing callers."""
from abc import ABC, abstractmethod
from pathlib import Path


class VisualizationBackend(ABC):
    @abstractmethod
    def preview_geometry(self, stl_path: Path, label: str = "",
                         domain: dict | None = None, refbox: dict | None = None) -> bytes:
        """Render STL geometry with optional domain and refinementBox wireframes. Returns PNG bytes."""

    @abstractmethod
    def plot_residuals(self, log_path: Path, x_max: float | None = None) -> bytes:
        """Parse solver log and plot residual convergence. Returns PNG bytes.

        x_max, when given, fixes the x-axis upper bound to the planned endTime so
        the plot shows how far the run has progressed toward completion.
        """

    @abstractmethod
    def plot_force_coefficients(self, postproc_dir: Path, only_last_phase: bool = False,
                                phase_end: dict | None = None,
                                single_x_max: float | None = None,
                                single_x_title: str | None = None,
                                clamp_time: float | None = None) -> bytes:
        """Plot Cd/Cl/Cm time series from forceCoeffs postProcessing. Returns PNG bytes.

        only_last_phase drops inherited earlier phases (used for restart children).
        phase_end maps phase number -> planned endTime for the multi-phase (legacy)
        subplot layout. single_x_max / single_x_title fix the x-axis bound and label
        for the single-plot layout (the caller knows the unit: iterations for steady,
        seconds for an unsteady child's own stage). clamp_time drops rows with
        Time beyond it (stale inherited parent iterations on an unsteady child).
        """

    @abstractmethod
    def plot_cutting_plane(self, vtk_path: Path, field: str = "p",
                           geo_bounds: dict | None = None,
                           stl_path: Path | None = None) -> bytes:
        """Render cutting plane (VTK) for a given field. Returns PNG bytes."""

    @abstractmethod
    def plot_streamlines(self, vtk_paths: Path | list[Path], geo_bounds: dict | None = None,
                         stl_path: Path | None = None) -> bytes:
        """Render streamlines (VTK). vtk_paths may be a single path or a list (forward+backward).
        Returns PNG bytes."""
