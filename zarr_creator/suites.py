"""Registry of the suites (source models) the zarr-creator can convert.

Each suite is a ``config_<name>.py`` module. The HARMONIE suites (``dini``,
``ig``) only define the projection and ``DATA_COLLECTION``; everything else
falls back to the HARMONIE defaults below. Other sources (``ifs``) override
the module attributes they need:

- ``get_grib_file_groups(t_analysis, max_hour, member_id)``: expected GRIB file
  names relative to the source root, grouped (each group is indexed and
  turned into refs separately)
- ``make_magician()``: the gribscan magician used to assemble the refs
- ``INDEX_NEXT_TO_SOURCE``: write the gribscan ``.index`` files next to the
  GRIB files (HARMONIE) or into the refs directory (for sources we must not
  write to, like the shared ECMWF cache)
- ``ANALYSIS_INTERVAL_HOURS`` / ``LAG_HOURS``: the cycle interval and delivery
  lag used to find the latest analysis time
- ``DEFAULT_MAX_HOUR`` / ``DEFAULT_MEMBER_ID``: used when ``MAX_HOUR`` /
  ``MEMBER_ID`` aren't set
- ``LEVEL_DIM_NAMES``: level type -> name of the output level dimension
- ``get_chunk_sizes(ds)``: output chunking of a converted part
- ``transform_part(ds, t_analysis)``: post-processing of a converted part
  before it is written
- ``clip_to_bounds(ds, bounds)``: clip a converted part to a lat/lon box
  (only for suites on a lat/lon grid)
"""

import datetime
import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Callable

import numpy as np
import xarray as xr

from .settings import DEFAULT_ANALYSIS_INTERVAL_HOURS as HARMONIE_INTERVAL_HOURS
from .settings import DEFAULT_ANALYSIS_LAG_HOURS as HARMONIE_LAG_HOURS
from .settings import DEFAULT_MAX_HOUR as HARMONIE_MAX_HOUR
from .settings import DEFAULT_MEMBER_ID as HARMONIE_MEMBER_ID
from .settings import SUITE_NAMES, expected_grib_filenames

HARMONIE_LEVEL_DIM_NAMES = {
    "isobaricInhPa": "pressure",
    "heightAboveGround": "altitude",
    "heightAboveSea": "altitude",
}


def _get_harmonie_grib_file_groups(
    t_analysis: datetime.datetime, max_hour: int, member_id: str
) -> dict[str, list[str]]:
    """Expected HARMONIE GRIB file names, grouped by file type.

    Parameters
    ----------
    t_analysis : datetime.datetime
        Analysis time, timezone-aware UTC.
    max_hour : int
        Last forecast hour (inclusive).
    member_id : str
        Ensemble member, part of the file names.

    Returns
    -------
    dict[str, list[str]]
        Mapping of file type (the suffix after the last ``_``, e.g. ``"sf"``,
        ``"pl"``) to the file names relative to the source root.
    """
    groups: dict[str, list[str]] = {}
    for name in expected_grib_filenames(t_analysis, max_hour, member_id):
        groups.setdefault(name.rsplit("_", 1)[-1], []).append(name)
    return groups


def _make_harmonie_magician():
    """Create the gribscan magician used to assemble the HARMONIE refs.

    Returns
    -------
    gribscan.magician.HarmonieMagician
        A new HARMONIE magician.
    """
    from gribscan.magician import HarmonieMagician

    return HarmonieMagician()


def _get_harmonie_chunk_sizes(ds: xr.Dataset) -> dict:
    """Output chunking of a converted HARMONIE part.

    Parameters
    ----------
    ds : xarray.Dataset
        The converted part, with ``x`` and ``y`` dims.

    Returns
    -------
    dict
        Chunk size per dimension: one time step and half of ``x`` and ``y``
        (rounded up) per chunk.
    """
    return dict(
        time=1,
        x=int(np.ceil(ds.x.size / 2)),
        y=int(np.ceil(ds.y.size / 2)),
    )


def _return_unchanged(ds: xr.Dataset, t_analysis: datetime.datetime) -> xr.Dataset:
    """Default part post-processing, which leaves the part unchanged.

    Parameters
    ----------
    ds : xarray.Dataset
        The converted part.
    t_analysis : datetime.datetime
        Analysis time; unused.

    Returns
    -------
    xarray.Dataset
        ``ds`` itself.
    """
    return ds


@dataclass(frozen=True)
class Suite:
    name: str
    data_collection: dict
    projection_identifier: str
    projection_wkt: str
    get_grib_file_groups: Callable[[datetime.datetime, int, str], dict[str, list[str]]]
    make_magician: Callable[[], object]
    index_next_to_source: bool
    analysis_interval_hours: int
    lag_hours: float
    default_max_hour: int
    default_member_id: str
    level_dim_names: dict[str, str]
    get_chunk_sizes: Callable[[xr.Dataset], dict]
    transform_part: Callable[[xr.Dataset, datetime.datetime], xr.Dataset]
    # None: the suite can't be clipped to a lat/lon box (e.g. projected grids)
    clip_to_bounds: Callable[[xr.Dataset, dict[str, float]], xr.Dataset] | None


def _make_suite_from_module(name: str, module: ModuleType) -> Suite:
    """Build a `Suite` from a ``config_<name>`` module.

    Attributes the module doesn't define fall back to the HARMONIE defaults
    (see the module docstring).

    Parameters
    ----------
    name : str
        Suite name.
    module : types.ModuleType
        The suite's config module.

    Returns
    -------
    Suite
        The suite description.
    """
    return Suite(
        name=name,
        data_collection=module.DATA_COLLECTION,
        projection_identifier=module.PROJECTION_IDENTIFIER,
        projection_wkt=module.PROJECTION_WKT,
        get_grib_file_groups=getattr(
            module, "get_grib_file_groups", _get_harmonie_grib_file_groups
        ),
        make_magician=getattr(module, "make_magician", _make_harmonie_magician),
        index_next_to_source=getattr(module, "INDEX_NEXT_TO_SOURCE", True),
        analysis_interval_hours=getattr(
            module, "ANALYSIS_INTERVAL_HOURS", HARMONIE_INTERVAL_HOURS
        ),
        lag_hours=getattr(module, "LAG_HOURS", HARMONIE_LAG_HOURS),
        default_max_hour=getattr(module, "DEFAULT_MAX_HOUR", HARMONIE_MAX_HOUR),
        default_member_id=getattr(module, "DEFAULT_MEMBER_ID", HARMONIE_MEMBER_ID),
        level_dim_names=getattr(module, "LEVEL_DIM_NAMES", HARMONIE_LEVEL_DIM_NAMES),
        get_chunk_sizes=getattr(module, "get_chunk_sizes", _get_harmonie_chunk_sizes),
        transform_part=getattr(module, "transform_part", _return_unchanged),
        clip_to_bounds=getattr(module, "clip_to_bounds", None),
    )


def get_suite(name: str) -> Suite:
    """Look up a suite by name (e.g. ``dini``, ``ig``, ``ifs``).

    Parameters
    ----------
    name : str
        Suite name, one of ``SUITE_NAMES``.

    Returns
    -------
    Suite
        The suite described by its ``config_<name>`` module.

    Raises
    ------
    ValueError
        If ``name`` is not a supported suite.
    """
    if name not in SUITE_NAMES:
        raise ValueError(
            f"Unsupported suite name: {name!r} (choose from {', '.join(SUITE_NAMES)})"
        )
    module = importlib.import_module(f".config_{name}", package=__package__)
    return _make_suite_from_module(name, module)
