"""Convert the refs of one analysis time to the output zarr datasets.

Kept out of ``__main__`` so the runner can import it: under
``python -m zarr_creator`` that module is loaded as ``__main__``, and
importing ``zarr_creator.__main__`` would load and run it a second time.
"""

import datetime

import xarray as xr
from loguru import logger

from . import __version__
from .grib_definitions import set_local_eccodes_definitions_path
from .read_source import read_level_type_data
from .settings import Settings, format_output_path, require_utc
from .suites import get_suite
from .write_zarr import write_output_zarrs

DEFAULT_FORECAST_DURATION = "PT3H"
DEFAULT_CHUNKING = dict(time=54, x=300, y=260)


def _rename_level_dim(
    ds: xr.Dataset, level_types: set[str], level_dim_names: dict[str, str]
) -> xr.Dataset:
    """Rename the ``level`` dim after the level types it came from.

    Level types without a mapping (e.g. ``entireAtmosphere`` fields that share
    the dim) don't take part, but at least one level type must be mapped and
    all mapped ones must agree.
    """
    names = {level_dim_names[lt] for lt in level_types if lt in level_dim_names}
    if not names:
        raise NotImplementedError(
            f"Level type(s) {sorted(level_types)} not implemented"
        )
    if len(names) > 1:
        raise ValueError(
            f"Level types {sorted(level_types)} map to different level dims "
            f"{sorted(names)} within one part"
        )
    (name,) = names
    if name == "level":
        return ds
    return ds.rename({"level": name})


def convert(t_analysis: datetime.datetime, settings: Settings) -> None:
    """Convert the refs of one analysis time to the output zarr datasets.

    Reads the refs from ``settings.refs_root_path`` and writes one zarr
    dataset per part of the suite's data collection (``settings.suite_name``)
    to ``settings.dst_zarr_output_path``. Logging is configured by the caller.
    """
    t_analysis = require_utc(t_analysis)
    set_local_eccodes_definitions_path()

    if "{t_analysis}" not in settings.dst_zarr_output_path:
        logger.info(
            "DST_ZARR_OUTPUT_PATH contains no {t_analysis}: "
            "each run overwrites the previous output."
        )

    suite = get_suite(settings.suite_name)

    parts = {}
    for part_id, part_details in suite.data_collection.items():
        ds_part = xr.Dataset()
        # level types of the variables that carry a `level` dim
        level_types_with_level_dim = set()
        for level_details in part_details:
            level_type = level_details["level_type"]
            variables = level_details["variables"]
            level_name_mapping = level_details.get("level_name_mapping", None)

            ds_level_type = read_level_type_data(
                t_analysis=t_analysis,
                level_type=level_type,
                projection_identifier=suite.projection_identifier,
                projection_wkt=suite.projection_wkt,
                refs_root_path=settings.refs_root_path,
                member_id=settings.member_id,
            )

            for var_name, levels in variables.items():
                if callable(levels):
                    da = levels(ds_level_type)
                    ds_part[var_name] = da
                    if "level" in da.dims:
                        level_types_with_level_dim.add(level_type)
                    if "grid_mapping" in da.attrs:
                        ds_part[da.attrs["grid_mapping"]] = ds_level_type[
                            da.attrs["grid_mapping"]
                        ]
                    continue

                da = ds_level_type[var_name]

                if levels is None:
                    if level_name_mapping is None:
                        new_name = var_name
                    else:
                        new_name = level_name_mapping.format(var_name=var_name)
                    ds_part[new_name] = da
                elif level_name_mapping is None:
                    # assuming we're just selecting levels and not changing the name
                    da = da.sel(level=levels)
                    ds_part[var_name] = da
                else:
                    # mapping each level to a new variable name
                    for level in levels:
                        da_level = da.sel(level=level)
                        new_name = level_name_mapping.format(
                            level=level, var_name=var_name
                        )
                        ds_part[new_name] = da_level

                if "level" in da.dims:
                    level_types_with_level_dim.add(level_type)
                if "grid_mapping" in da.attrs:
                    ds_part[da.attrs["grid_mapping"]] = ds_level_type[
                        da.attrs["grid_mapping"]
                    ]

        # rename the "level" dim per the suite, e.g. to "altitude" or "pressure"
        if "level" in ds_part.dims:
            ds_part = _rename_level_dim(
                ds_part, level_types_with_level_dim, suite.level_dim_names
            )

        # check if any of the coordinates don't have any variables, if so drop them
        for coord in ds_part.coords:
            if all(coord not in ds_part[v].coords for v in list(ds_part.data_vars)):
                ds_part = ds_part.drop_vars(coord)

        parts[part_id] = suite.transform_part(ds_part, t_analysis)

    for part_id, ds_part in parts.items():
        rechunk_to = suite.rechunk_to(ds_part)

        # set zarr-creator version
        ds_part.attrs["zarr_creator_version"] = __version__
        # set creation timestamp
        ds_part.attrs["zarr_creation_time"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        # add link to repo
        ds_part.attrs["zarr_creator_repo"] = (
            "https://github.com/dmidk/nwp-forecast-zarr-creator"
        )

        write_output_zarrs(
            ds=ds_part,
            output_path=format_output_path(
                settings.dst_zarr_output_path,
                suite_name=settings.suite_name,
                member="control",
                t_analysis=t_analysis,
                dataset_id=part_id,
            ),
            rechunk_to=rechunk_to,
            profile=settings.dst_aws_profile,
        )
