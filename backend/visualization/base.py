"""Abstract visualization backend. Swap implementations without changing callers."""
from abc import ABC, abstractmethod
from pathlib import Path


class VisualizationBackend(ABC):
    @abstractmethod
    def preview_geometry(self, stl_path: Path, label: str = "",
                         domain: dict | None = None, refbox: dict | None = None) -> bytes:
        """Render STL geometry with optional domain and refinementBox wireframes. Returns PNG bytes."""

    @abstractmethod
    def plot_residuals(self, log_path: Path) -> bytes:
        """Parse solver log and plot residual convergence. Returns PNG bytes."""

    @abstractmethod
    def plot_force_coefficients(self, postproc_dir: Path) -> bytes:
        """Plot Cd/Cl/Cm time series from forceCoeffs postProcessing. Returns PNG bytes."""

    @abstractmethod
    def plot_cutting_plane(self, vtk_path: Path, field: str = "p") -> bytes:
        """Render cutting plane (VTK) for a given field. Returns PNG bytes."""

    @abstractmethod
    def plot_streamlines(self, vtk_path: Path) -> bytes:
        """Render streamlines (VTK). Returns PNG bytes."""
