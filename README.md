# NWP Forecast in zarr format

This repository contains the code to process DINI GRIB files into zarr format.

Currently, this writes all pressure-level fields to `pressure_levels.zarr`,
height-level fields to `height_levels.zarr` and everything else to
`single_levels.zarr`. We do not currently transfer and convert model-level
fields. These are written to

`s3://harmonie-zarr/dini/control/2025-03-03T060000Z/single_levels.zarr`

e.g. the prefix format is:

`s3://harmonie-zarr/{suite_name}/{member}/{analysis_time}/[part_id}.zarr`


NB: note that for DINI we have fewer height-levels and so I have only included `50m`, `100m`, `150m` and `250m`. In addition a number of variables aren't in DINI or at least I don't understand what the variables that are there all mean. To see what is included please have a look at [zarr_creator/config.py](zarr_creator/config.py).


## Usage

### Periodic running

For now running the conversion to zarr and writing to s3 the `run.sh` script
should be executed in for example a tmux session.

### Manually running

Running the conversion manually requires two steps:

1. Build GRIB indexes and and refs by calling GRIB scan directly:

```bash
./build_indexes_and_refs.sh 2025-02-27T15:00Z [tempdir]
```

This will write to `refs/` and optionally copy the source GRIB files to `tempdir` if provided

2. Read the refs, build the three datasets (height-levels, pressure-levels and single-levels) as `xr.Datasets` and write each to the target s3 bucket:

```bash
pdm run python -m zarr_creator --t_analysis 2025-02-27T15:00:00Z
```


# TODO

- most of the execution takes place in `run.sh` which orchestrates the retry if something goes wrong. This could be rewritten in python. I found it easier to start by prototyping this as a batch script.
- creation of GRIB indexes and refs from the indexes are done in a bash script `build_indexes_and_refs.sh` and is done by directly with gribscan (using the gribscan command-line interface) rather than using the dmi "data-catalog" python package `dmidc`. This was done because it turns out that DINI uses the special paramId for for example u-wind at 10m and 100m which is different from the parameter IDs for u-wind in general. This made calling the data-catalog cumbersome. Also, calling gribscan directly makes it more explicit how variables are mapped by level-type into the `height_levels.zarr`, `presure_levels.zarr` and `single_levels.zarr` more explicit. The bash script could be replaced with python code though.
- The writing of the output zarr datasets sometimes fails. I think this is due to issues in eccodes, but I am not sure. It could also be due to s3fs (FUSE) mounts being a bit brittle. In least in my experience copying the source GRIB files from the mounted s3 bucket avoid similar issues when creating the GRIB-indexes. Maybe something similar is needed when writing to the s3 bucket (i.e. create zarr then write to the bucket). I think it would better to use `fsspec`'s `s3` protocol implementation for reading and writing to/from s3 buckets instead of relying on s3fs
- the periodic running could be done in python too rather than relying on a do-loop in a bash script.
