# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.5.0]
### Added
- Keep local copy of most recent forecast in `/tmp/dini-recent`
  [\#12](https://github.com/dmidk/nwp-forecast-zarr-creator/pull/12),
  @leifdenby
- Containerise setup so we can use `eccodes==2.42.0` with `eccodeslib` for C
  library rather than rely on system `eccodes` C lib
  [\#11](https://github.com/dmidk/nwp-forecast-zarr-creator/pull/11), @leifdenby

---

## [v0.4.0]
### Added
- Land–sea mask extraction (`0cdf848`) and
  [\#9](https://github.com/dmidk/nwp-forecast-zarr-creator/pull/9), @leifdenby
- Add changelog file and use dynamic versioning
  [\#9](https://github.com/dmidk/nwp-forecast-zarr-creator/pull/9), @leifdenby
- Add wrapper for `gribscan-index` CLI to set local eccodes definitions path so
  that `gribscan` can pick up the WMO standard GRIB2 definitions that DMI uses.
  Also bundles these local definitions with the package.
  [\#9](https://github.com/dmidk/nwp-forecast-zarr-creator/pull/9), @leifdenby

---

## [0.3.0] – 2025-03-14
### Added
- Extraction of surface pressure as `pres0m` (`a13808c`)
- Add changelog file and versioning

### Changed
- Extended forecast to 36 hours (`eb45784`)

### Fixed
- Prevented automatic launch of `ipdb` on exceptions (`ddf93e2`)

---

## [0.2.0] – 2025-03-04
### Added
- Support for multiple entries per level-type in config with improved retry logic (`6b556bf`)
- Level-name mapping applied to non-level fields (#7, `f0cb23f`)

---

## [0.1.0] – 2025-03-04
### Added
- First working prototype (#6, `3e71183`):
  - Reads DINI GRIB files (`sf` and `pl`)
  - Creates `height_levels.zarr`, `pressure_levels.zarr`, `single_levels.zarr`
  - Writes outputs to an S3 bucket

---

## [0.0.1] – 2025-02-25
### Added
- Initial commits (`c8e9267`, `bf6427e`, `468c593`)

---

[Unreleased]: https://github.com/dmidk/nwp-forecast-zarr-creator/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/dmidk/nwp-forecast-zarr-creator/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dmidk/nwp-forecast-zarr-creator/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dmidk/nwp-forecast-zarr-creator/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dmidk/nwp-forecast-zarr-creator/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/dmidk/nwp-forecast-zarr-creator/releases/tag/v0.0.1
