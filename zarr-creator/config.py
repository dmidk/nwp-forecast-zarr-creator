from pathlib import Path

# The "data collection" may contain multiple named parts (each will be put in its own zarr archive)
# Each part may contain multiple "level types" (e.g. heightAboveGround, etc)
# and a name-mapping may also be defined

DATA_COLLECTION = dict(
    description=f"All prognostic variables on all levels",
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
                            # TODO: `rain`, `snow`, `snsub`, `nlwrs`, `nswrs`, `lhsub`,
                            # `lhe`, `grpl`, `dni`, `grad`, `sshf`, `wevap`, `vflx`, `uflx`, `tp`
                            # use time-range indicator 4, but dmidc currently assumes 0
                            # "rain",
                            # "snow",
                            # "snsub",
                            # "nlwrs",
                            # "nswrs",
                            # "lhsub",
                            # "lhe",
                            # "grpl",
                            # "dni",
                            # "grad",
                            # "sshf",
                            # "wevap",
                            # "vflx",
                            # "uflx",
                            # "tp",
                        ]
                    },
                    **{
                        "t": [0, 2],
                        # TODO: `tmin` and `tmax` use time-range indicator 2, but dmidc currently assumes 0
                        # "tmin": [2],
                        # "tmax": [2],
                        "r": [2],
                        "u": [10],
                        "v": [10],
                        # TODO: in next version add ugst and vgst, but dmidc needs to be updated first
                        # to change timerange_indicator to 10 (rather than 0 by default), need to find
                        # out what value 10 means too..
                        # "ugst": [10],
                        # "vgst": [10],
                    },
                },
                level_name_mapping="{var_name}{level:d}m",
            ),
            entireAtmosphere=dict(
                variables={v: [0] for v in "pwat cape cb ct grpl".split()},
                level_name_mapping="{var_name}_column",
            ),
            # the variables `nswrt` and `nlwrt` are not available with timeRangeIndicator==0
            # for all source tar-files, and so I am excluding them for now
            # nominalTop=dict(
            #     variables=dict(nswrt=[0], nlwrt=[0]), level_name_mapping="{var_name}_toa"
            # ),
            heightAboveSea=dict(
                variables=dict(pres=[0]), level_name_mapping="{var_name}_seasurface"
            ),
            # This level-type named "CONSTANTS" is used to indicate we want
            # constant fields (land-sea mask and surface geopotential)
            CONSTANTS=dict(variables=dict(lsm=[0], z=[0])),
        ),
    ),
)

