"""
This module provides the path to the local GRIB definitions that include the
WMO standard GRIB2 definitions that DMI uses and that overwrite the bundled
with eccodes.
"""

import importlib.resources as pkg_resources

import eccodes

# pkg_resources.path requires that we provide the path to a file, so we give a
# temporary filename (pkg_resources.path() will create and immediately delete
# the file, because the python object representing the path is immediately
# deleted once the with-block is exited)
with pkg_resources.path("zarr_creator.eccodes_definitions", "__tmp__") as p_temp:
    LOCAL_GRIB_DEFNS_PATH = p_temp.parent


def set_local_eccodes_definitions_path():
    # set the eccodes definitions path to use the definitions included with this
    # repo. We need to do this because for example for land-sea mask
    # (discipline=2,parameterCategory=0,parameterNumber=0) although this
    # set of values is the WMO GRIB2 standard this isn't set as land-sea mask in
    # all versions of eccodes. In our case we call this `lsm` as the short name.
    # XXX: eventually we should completely remove the use of short-names and instead
    # map from discipline,parameterCategory,parameterNumber to cf standard names)

    # We can just call `eccodes.codes_set_definitions_path(...)` with our own
    # local definitions paths, because:
    # 1. The `eccodes` python packages requires that you to include the default
    #    path too (well older versions anyway according to Kasper Hintz),
    #    otherwise it has no definitions at all and will crash and burn
    # 2. The default used by the `eccodes` python package (which you can see if
    #    you run `python -m eccodes selfcheck`) actually uses something called
    #    "MEMFS (MEMory FileSystem)" (see
    #    https://github.com/conda-forge/eccodes-feedstock/issues/26) which
    #    treats paths starting with `/MEMFS/` specially, actually storing the
    #    definitions in memory (so this works even though the `/MEMFS/` path
    #    doesn't actually exist). So we need to add that prefix to our path.

    # doesn't work, see above
    # eccodes.codes_set_definitions_path(LOCAL_GRIB_DEFNS_PATH)

    # works, lol
    eccodes.codes_set_definitions_path(f"{LOCAL_GRIB_DEFNS_PATH}:/MEMFS/definitions")


if __name__ == "__main__":
    print(LOCAL_GRIB_DEFNS_PATH)
