# The "data collection" may contain multiple named parts (each will be put in its own zarr archive)
# Each part may contain multiple "level types" (e.g. heightAboveGround, etc)
# and a name-mapping may also be defined

DATA_COLLECTION = dict(
    description="All prognostic variables on all levels",
    # x and y chunksize are created so that domain is split into 3x2=6 roughly
    # equal chunks that we time chunksize of 256 gives ~100MB chunks for each
    # level/single-level field
    rechunk_to=dict(time=256, x=263, y=295, pressure=1, altitude=1),
    parts=dict(
        static_fields=dict(),
        height_levels=dict(
            heightAboveGround=dict(
                variables={
                    v: [30, 50, 75, 100, 150, 200, 250, 300, 500]
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
                    for v in "z t u v tw r ciwc cwat".split()
                }
            )
        ),
        single_levels=dict(
            heightAboveGround=dict(
                variables={
                    **{
                        v: [0]
                        for v in [
                            "hcc",
                            "lcc",
                            "mcc",
                            "tcc",
                            "icei",
                            "lwavr",
                            "mld",
                            "pres",
                            "prtp",
                            "psct",
                            "pscw",
                            "pstb",
                            "pstbc",
                            "sf",
                            "swavr",
                            "vis",
                            "xhail",
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
                variables={v: [0] for v in "pwat cape cb ct grpl".split()},
                level_name_mapping="{var_name}_column",
            ),
            heightAboveSea=dict(
                variables=dict(pres=[0]), level_name_mapping="{var_name}_seasurface"
            ),
            CONSTANTS=dict(variables=dict(lsm=[0], z=[0])),
        ),
    ),
)
