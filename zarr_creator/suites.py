"""Registry of the suites (source models) the zarr-creator can convert.

Each suite is a ``config_<name>.py`` module. The HARMONIE suites (``dini``,
``ig``) only define the projection and ``DATA_COLLECTION``; everything else
falls back to the HARMONIE defaults below. Other sources (``ifs``) override
the module attributes they need:

- ``grib_file_groups(t_analysis, max_hour, member_id)``: expected GRIB file
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
- ``rechunk_to(ds)``: output chunking of a converted part
- ``transform_part(ds, t_analysis)``: post-processing of a converted part
  before it is written
"""

import datetime
import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Callable

import numpy as np
import xarray as xr

from .settings import DEFAULT_MAX_HOUR as HARMONIE_MAX_HOUR
from .settings import DEFAULT_MEMBER_ID as HARMONIE_MEMBER_ID
from .settings import expected_grib_filenames

SUITE_NAMES = ("dini", "ig", "ifs")

HARMONIE_LEVEL_DIM_NAMES = {
    "isobaricInhPa": "pressure",
    "heightAboveGround": "altitude",
    "heightAboveSea": "altitude",
}


def _harmonie_grib_file_groups(
    t_analysis: datetime.datetime, max_hour: int, member_id: str
) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for name in expected_grib_filenames(t_analysis, max_hour, member_id):
        groups.setdefault(name.rsplit("_", 1)[-1], []).append(name)
    return groups


def _harmonie_magician():
    from gribscan.magician import HarmonieMagician

    return HarmonieMagician()


def _harmonie_rechunk_to(ds: xr.Dataset) -> dict:
    return dict(
        time=1,
        x=int(np.ceil(ds.x.size / 2)),
        y=int(np.ceil(ds.y.size / 2)),
    )


def _no_transform(ds: xr.Dataset, t_analysis: datetime.datetime) -> xr.Dataset:
    return ds


@dataclass(frozen=True)
class Suite:
    name: str
    data_collection: dict
    projection_identifier: str
    projection_wkt: str
    grib_file_groups: Callable[[datetime.datetime, int, str], dict[str, list[str]]]
    make_magician: Callable[[], object]
    index_next_to_source: bool
    analysis_interval_hours: int
    lag_hours: float
    default_max_hour: int
    default_member_id: str
    level_dim_names: dict[str, str]
    rechunk_to: Callable[[xr.Dataset], dict]
    transform_part: Callable[[xr.Dataset, datetime.datetime], xr.Dataset]


def _from_module(name: str, module: ModuleType) -> Suite:
    return Suite(
        name=name,
        data_collection=module.DATA_COLLECTION,
        projection_identifier=module.PROJECTION_IDENTIFIER,
        projection_wkt=module.PROJECTION_WKT,
        grib_file_groups=getattr(
            module, "grib_file_groups", _harmonie_grib_file_groups
        ),
        make_magician=getattr(module, "make_magician", _harmonie_magician),
        index_next_to_source=getattr(module, "INDEX_NEXT_TO_SOURCE", True),
        analysis_interval_hours=getattr(module, "ANALYSIS_INTERVAL_HOURS", 3),
        lag_hours=getattr(module, "LAG_HOURS", 2),
        default_max_hour=getattr(module, "DEFAULT_MAX_HOUR", HARMONIE_MAX_HOUR),
        default_member_id=getattr(module, "DEFAULT_MEMBER_ID", HARMONIE_MEMBER_ID),
        level_dim_names=getattr(module, "LEVEL_DIM_NAMES", HARMONIE_LEVEL_DIM_NAMES),
        rechunk_to=getattr(module, "rechunk_to", _harmonie_rechunk_to),
        transform_part=getattr(module, "transform_part", _no_transform),
    )


def get_suite(name: str) -> Suite:
    """Look up a suite by name (e.g. ``dini``, ``ig``, ``ifs``)."""
    if name not in SUITE_NAMES:
        raise ValueError(
            f"Unsupported suite name: {name!r} (choose from {', '.join(SUITE_NAMES)})"
        )
    module = importlib.import_module(f".config_{name}", package=__package__)
    return _from_module(name, module)
