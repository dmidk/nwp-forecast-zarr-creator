# The "data collection" may contain multiple named parts (each will be put in its own zarr archive)
# Each part may contain multiple "level types" (e.g. heightAboveGround, etc)
# and a name-mapping may also be defined

DATA_COLLECTION = dict(
    parts=dict(
        height_levels=dict(
            heightAboveGround=dict(
                variables={
                    # v: [30, 50, 75, 100, 150, 200, 250, 300, 500]  # levels in DANRA
                    v: [50, 100, 150, 250]  # only these in DINI
                    for v in "t r u v".split()
                }
            )
        ),
        pressure_levels=dict(
            isobaricInhPa=dict(
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
                }
            )
        ),
        single_levels=dict(
            heightAboveGround=dict(
                variables={
                    **{
                        v: None
                        for v in [
                            "hcc",
                            "lcc",
                            "mcc",
                            # "tcc", # not in DINI
                            # "icei", # not in DINI
                            # "lwavr", # not in DINI
                            "mld",
                            "pres",
                            # "prtp", # not in DINI
                            # "psct", # not in DINI
                            # "pscw", # not in DINI
                            # "pstb", # not in DINI
                            # "pstbc", # not in DINI
                            # "sf", # not in DINI
                            # "swavr", # not in DINI
                            "vis",
                            # "xhail", # not in DINI
                        ]
                    },
                    **{
                        "t": [0, 2],
                        "r": [2],
                        "u": [10],
                        "v": [10],
                    },
                },
                level_name_mapping="{var_name}{level:d}m",
            ),
            entireAtmosphere=dict(
                # variables={v: [0] for v in "pwat cape cb ct grpl".split()},
                variables={v: None for v in ["cape"]},  # pwat, cb, ct, grpl not in DINI
                level_name_mapping="{var_name}_column",
            ),
            heightAboveSea=dict(
                variables=dict(pres=None), level_name_mapping="{var_name}_seasurface"
            ),
        ),
    ),
)
