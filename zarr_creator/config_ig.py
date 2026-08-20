# The "data collection" may contain multiple named parts (each will be put in its own zarr archive)
# Each part may contain multiple "level types" (e.g. heightAboveGround, etc)
# and a name-mapping may also be defined
from collections import OrderedDict

from .transforms import derive_orography_from_geopotential

PROJECTION_IDENTIFIER = "ig_projection"
# Note this WKT string gives consistent 0.2 meter difference in y coordinate wrt the GRIB data when transforming lat/lon to x/y 
PROJECTION_WKT = """
PROJCRS["DMI HARMONIE IG lambert projection",
    BASEGEOGCRS["DMI HARMONIE IG lambert CRS",
        DATUM["DMI HARMONIE IG lambert datum",
            ELLIPSOID["Sphere", 6371229, 0,
                LENGTHUNIT["metre",1,
                    ID["EPSG",9001]
                ]
            ]
        ],
        PRIMEM["Greenwich",0,
            ANGLEUNIT["degree",0.0174532925199433],
            ID["EPSG",8901]
        ]
    ],
    CONVERSION["Lambert Conic Conformal (2SP)",
        METHOD["Lambert Conic Conformal (2SP)",
            ID["EPSG",9802]
        ],
        PARAMETER["Latitude of false origin",72,
            ANGLEUNIT["degree",0.0174532925199433],
            ID["EPSG",8821]
        ],
        PARAMETER["Longitude of false origin",324,
            ANGLEUNIT["degree",0.0174532925199433],
            ID["EPSG",8822]
        ],
        PARAMETER["Latitude of 1st standard parallel",72,
            ANGLEUNIT["degree",0.0174532925199433],
            ID["EPSG",8823]
        ],
        PARAMETER["Latitude of 2nd standard parallel",72,
            ANGLEUNIT["degree",0.0174532925199433],
            ID["EPSG",8824]
        ],
        PARAMETER["Easting at false origin",0,
            LENGTHUNIT["metre",1],
            ID["EPSG",8826]
        ],
        PARAMETER["Northing at false origin",0,
            LENGTHUNIT["metre",1],
            ID["EPSG",8827]
        ]
    ],
    CS[Cartesian,2],
    AXIS["(E)",east,
        ORDER[1],
        LENGTHUNIT["metre",1,
            ID["EPSG",9001]
        ]
    ],
    AXIS["(N)",north,
        ORDER[2],
        LENGTHUNIT["metre",1,
            ID["EPSG",9001]
        ]
    ]
    USAGE[
        AREA["Greenland and surrounding regions"],
        BBOX[55, 250, 87, -398],
        SCOPE["IG Harmonie forecast projection"]
    ]
]
""".strip()

DATA_COLLECTION = OrderedDict(
    single_levels=[
        dict(
            level_type="heightAboveGround",
            variables={
                v: None
                for v in [
                    "hcc",
                    "lcc",
                    "mcc",
                    # "total cloud cover" is present in DINI but as `cc`
                    # shortname (according to kah@dmi.dk), but using the WMO
                    # standard units of "percent" rather than a fraction
                    # "tcc",
                    # "icei", # not in DINI
                    "mld",
                    # "prtp", # not in DINI
                    # "psct", # not in DINI
                    # "pscw", # not in DINI
                    # "pstb", # not in DINI
                    # "pstbc", # not in DINI
                    # "sf", # not in DINI
                    # "xhail", # not in DINI
                    "lsm",
                ]
            },
        ),
        # we include these separately from the other heightAboveGround
        # variables because we want to include the `0m`-suffix in the variable
        # name, to make it clear that these are surface variables
        dict(
            level_type="heightAboveGround",
            variables={
                # "swavr": None,
                "swavr_accum": None,
                # "lwavr": None,
                "lwavr_accum": None,
                "vis": None,
            },
            level_name_mapping="{var_name}0m",
        ),
        dict(
            level_type="heightAboveGround",
            variables={
                "orography": lambda ds: derive_orography_from_geopotential(ds["z"]),
            },
        ),
        dict(
            level_type="heightAboveGround",
            variables={
                "t": [0, 2],
                "pres": [0],
                "r": [2],
                "u": [10],
                "v": [10],
            },
            level_name_mapping="{var_name}{level:d}m",
        ),
        dict(
            level_type="entireAtmosphere",
            variables={v: None for v in ["cape"]},  # pwat, cb, ct, grpl not in DINI
            level_name_mapping="{var_name}_column",
        ),
        dict(
            level_type="heightAboveSea",
            variables=dict(pres=None),
            level_name_mapping="{var_name}_seasurface",
        ),
    ],
    pressure_levels=[
        dict(
            level_type="isobaricInhPa",
            variables={
                v: [
                    1000,
                    950,
                    925,
                    900,
                    850,
                    800,
                    700,
                    600,
                    500,
                    400,
                    300,
                    250,
                    200,
                    100,
                ]
                # for v in "z t u v tw r ciwc cwat".split()  # variables in DANRA
                for v in "z t u v r tw".split()
            },
        )
    ],
    height_levels=[
        dict(
            level_type="heightAboveGround",
            variables={
                # v: [30, 50, 75, 100, 150, 200, 250, 300, 500]  # levels in DANRA
                v: [50, 100, 150, 250]  # only these in DINI
                for v in "t r u v".split()
            },
        )
    ],
)
