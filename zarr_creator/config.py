# The "data collection" may contain multiple named parts (each will be put in its own zarr archive)
# Each part may contain multiple "level types" (e.g. heightAboveGround, etc)
# and a name-mapping may also be defined
from collections import OrderedDict

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
                    # "tcc", # not in DINI
                    # "icei", # not in DINI
                    # "lwavr", # not in DINI
                    "mld",
                    # "prtp", # not in DINI
                    # "psct", # not in DINI
                    # "pscw", # not in DINI
                    # "pstb", # not in DINI
                    # "pstbc", # not in DINI
                    # "sf", # not in DINI
                    # "swavr", # not in DINI
                    "vis",
                    # "xhail", # not in DINI
                    "lsm",
                ]
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
                for v in "z t u v r".split()
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
