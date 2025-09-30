import importlib.resources as pkg_resources

# pkg_resources.path requires that we provide the path to a file, so we give a
# temporary filename (pkg_resources.path() will create and immediately delete
# the file, because the python object representing the path is immediately
# deleted once the with-block is exited)
with pkg_resources.path("zarr_creator.eccodes_definitions", "__tmp__") as p_temp:
    LOCAL_GRIB_DEFNS_PATH = p_temp.parent

if __name__ == "__main__":
    print(LOCAL_GRIB_DEFNS_PATH)
