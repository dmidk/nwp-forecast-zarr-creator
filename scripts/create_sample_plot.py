#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "xarray",
#   "zarr",
#   "cf_xarray",
#   "matplotlib",
#   "cartopy",
# ]
# ///
"""
Create a sample plot from the zarr datasets produced by `nwp-forecast-zarr-creator`.

The purpose of this script is to easy to visually assess whether the dataset
was created correctly, by plotting a variable from the dataset on a map both
from the lat/lon coordinates in the dataset, and with the dataset's native
projection as defined by the grid mapping variable and its associated WKT
string.
"""

import argparse
from pathlib import Path

import cartopy.crs as ccrs
import cf_xarray  # noqa: F401  # enables .cf accessor
import matplotlib.pyplot as plt
import xarray as xr


def _cartopy_projection_from_wkt(wkt: str):
    return ccrs.Projection(wkt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a sample map plot from a zarr dataset."
    )
    parser.add_argument(
        "--zarr-path",
        default="tmp/dini-recent/height_levels.zarr",
        help="Input zarr dataset path.",
    )
    parser.add_argument(
        "--variable",
        default="t",
        help="Variable name to plot.",
    )
    parser.add_argument(
        "--time-index",
        type=int,
        default=0,
        help="Time index.",
    )
    parser.add_argument(
        "--altitude-index",
        type=int,
        default=0,
        help="Altitude index.",
    )
    parser.add_argument(
        "--output",
        default="tmp/sample_plot.png",
        help="Output image path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    ds = xr.open_zarr(args.zarr_path)
    da = ds[args.variable].isel(time=args.time_index, altitude=args.altitude_index)

    lon = ds.cf["longitude"]
    lat = ds.cf["latitude"]

    grid_mapping_name = da.attrs.get("grid_mapping")
    if grid_mapping_name is None:
        raise RuntimeError(
            f"Variable '{args.variable}' has no 'grid_mapping' attribute."
        )
    if grid_mapping_name not in ds:
        raise RuntimeError(
            f"grid_mapping variable '{grid_mapping_name}' not found in dataset."
        )
    wkt = ds[grid_mapping_name].attrs.get("crs_wkt")
    if not wkt:
        raise RuntimeError(
            f"grid_mapping variable '{grid_mapping_name}' does not contain 'crs_wkt'."
        )
    data_crs = _cartopy_projection_from_wkt(wkt)
    plate_carree = ccrs.PlateCarree()

    fig = plt.figure(figsize=(18, 7))
    ax1 = fig.add_subplot(1, 2, 1, projection=plate_carree)
    ax2 = fig.add_subplot(1, 2, 2, projection=data_crs)

    # Existing subplot: standard lon/lat PlateCarree view.
    da.plot(
        ax=ax1,
        x=lon.name,
        y=lat.name,
        transform=plate_carree,
        add_colorbar=True,
    )
    ax1.gridlines(
        draw_labels=True,
        dms=True,
        x_inline=False,
        y_inline=False,
        color="black",
        linestyle="--",
    )
    ax1.coastlines()
    ax1.set_title(
        f"{args.variable} PlateCarree at time={args.time_index}, altitude={args.altitude_index}"
    )

    # New subplot: use the dataset's native projection for the axis.
    da.plot(
        ax=ax2,
        x=lon.name,
        y=lat.name,
        transform=plate_carree,
        add_colorbar=True,
    )
    ax2.gridlines(
        draw_labels=True,
        dms=True,
        x_inline=False,
        y_inline=False,
        color="black",
        linestyle="--",
    )
    ax2.coastlines()
    ax2.set_title(
        f"{args.variable} native projection at time={args.time_index}, altitude={args.altitude_index}"
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Wrote sample plot to {output_path}")


if __name__ == "__main__":
    main()
