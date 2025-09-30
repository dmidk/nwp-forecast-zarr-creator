"""
This module is a wrapper around the gribscan `build-index` command line tool
that enables us to se the local GRIB definitions path that includes the DMI
definitions (these are needed because the bundled GRIB2 definitions with
eccodes doesn't follow WMO standards, whereas the DMI definitions do and for
example land-sea mask is therefore not recognized correctly without the DMI
definitions).
"""

import gribscan.tools

from .grib_definitions import set_local_eccodes_definitions_path

if __name__ == "__main__":
    set_local_eccodes_definitions_path()
    gribscan.tools.create_index()
