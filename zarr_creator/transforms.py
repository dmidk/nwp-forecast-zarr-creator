from collections.abc import Iterable

import xarray as xr
from loguru import logger


def resolve_variable_transforms(
    ds: xr.Dataset,
    rename_map: dict[str, str] | None = None,
    scale_map: dict[str, float] | None = None,
    attrs_map: dict[str, dict[str, str]] | None = None,
    drop_time_dimension_for: Iterable[str] | None = None,
) -> tuple[dict[str, str], dict[str, float], dict[str, dict[str, str]], list[str]]:
    """Resolve variable transforms against the variables present in a dataset."""

    rename_map = rename_map or {}
    active_rename_map = {
        old_name: new_name
        for old_name, new_name in rename_map.items()
        if old_name in ds.data_vars
    }
    scale_map = scale_map or {}
    active_scale_map = {
        var_name: factor
        for var_name, factor in scale_map.items()
        if var_name in ds.data_vars
    }
    attrs_map = attrs_map or {}
    active_attrs_map = {
        active_rename_map.get(var_name, var_name): attrs
        for var_name, attrs in attrs_map.items()
        if var_name in ds.data_vars
    }

    resolved_drop_time_dimension_for: list[str] = []
    renamed_variables = set(active_rename_map.values())

    for var_name in drop_time_dimension_for or []:
        if var_name in ds.data_vars:
            resolved_drop_time_dimension_for.append(var_name)
        elif var_name in active_rename_map:
            resolved_drop_time_dimension_for.append(active_rename_map[var_name])
        elif var_name in renamed_variables:
            resolved_drop_time_dimension_for.append(var_name)

    resolved_drop_time_dimension_for = list(
        dict.fromkeys(resolved_drop_time_dimension_for)
    )
    return (
        active_rename_map,
        active_scale_map,
        active_attrs_map,
        resolved_drop_time_dimension_for,
    )


def apply_variable_transforms(
    ds: xr.Dataset,
    rename_map: dict[str, str] | None = None,
    scale_map: dict[str, float] | None = None,
    attrs_map: dict[str, dict[str, str]] | None = None,
    drop_time_dimension_for: Iterable[str] | None = None,
) -> xr.Dataset:
    """Apply variable-level transforms to a dataset.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset to transform.
    rename_map : dict[str, str] | None
        Mapping from old variable names to new variable names.
    scale_map : dict[str, float] | None
        Mapping from variable names to multiplicative scale factors.
    attrs_map : dict[str, dict[str, str]] | None
        Mapping from variable names to attribute updates that should be applied
        after scaling, renaming and optional time-dimension dropping.
    drop_time_dimension_for : Iterable[str] | None
        Variable names (after renaming) for which a time dimension should be
        removed by selecting the first timestep.
    """

    out = ds

    scale_map = scale_map or {}
    for var_name, factor in scale_map.items():
        if var_name not in out.data_vars:
            logger.warning(
                f"Variable scaling requested for `{var_name}`, but variable is not present"
            )
            continue

        out[var_name] = out[var_name] * factor

    rename_map = rename_map or {}
    if rename_map:
        missing = sorted(name for name in rename_map if name not in out.data_vars)
        for var_name in missing:
            logger.warning(
                f"Variable rename requested for `{var_name}`, but variable is not present"
            )

        effective_rename_map = {
            old_name: new_name
            for old_name, new_name in rename_map.items()
            if old_name in out.data_vars
        }

        for old_name, new_name in effective_rename_map.items():
            if new_name in out.data_vars and new_name not in effective_rename_map:
                raise ValueError(
                    f"Cannot rename `{old_name}` to `{new_name}` because `{new_name}` "
                    "already exists"
                )

        if effective_rename_map:
            out = out.rename(effective_rename_map)

    for var_name in drop_time_dimension_for or []:
        if var_name not in out.data_vars:
            logger.warning(
                f"Dropping `time` requested for `{var_name}`, but variable is not present"
            )
            continue

        if "time" in out[var_name].dims:
            out[var_name] = out[var_name].isel(time=0, drop=True)
        else:
            logger.info(
                f"Skipping drop of `time` for `{var_name}` because it has no time dimension"
            )

    for var_name, attrs in (attrs_map or {}).items():
        if var_name not in out.data_vars:
            logger.warning(
                f"Attribute updates requested for `{var_name}`, but variable is not present"
            )
            continue
        out[var_name].attrs.update(attrs)

    return out
