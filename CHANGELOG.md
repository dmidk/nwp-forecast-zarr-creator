# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## unreleased

### Changed

- add extraction of land-sea mask. Introduce eccodes grib2 definitions to ensure that land-sea mask (WMO GRIB2, discipline=2,parameterCategory=0,parameterNumber=0) is mapped to standard name `lsm` since this variable is not included in all versions of eccodes
