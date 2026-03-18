import importlib.metadata
from pathlib import Path
from typing import Any

try:
    __version__ = importlib.metadata.version(__name__)
except importlib.metadata.PackageNotFoundError:
    __version__ = "unknown"


def open_intake_catalog(catalog_path: str | Path | None = None) -> Any:
    """Open the project's intake catalog."""
    try:
        import intake
    except ImportError as exc:
        raise ImportError(
            "intake is required to use open_intake_catalog(). "
            "Install dependency group 'intake-catalog'."
        ) from exc

    if catalog_path is None:
        catalog_path = Path(__file__).resolve().parent / "catalog" / "catalog.yml"
    else:
        catalog_path = Path(catalog_path)

    return intake.open_catalog(str(catalog_path))
