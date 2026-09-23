<div align="center">
  <img src="assets/isimipclim-logo.png" alt="ISIMIP Climate Downloader logo" width="190">

# ISIMIP Climate Downloader

**Reliable regional downloads and validation for ISIMIP3b climate forcing data**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Data](https://img.shields.io/badge/Data-ISIMIP3b-007C91)](https://data.isimip.org/)
[![Format](https://img.shields.io/badge/Format-NetCDF-18A999)](https://www.unidata.ucar.edu/software/netcdf/)

</div>

Download daily, bias-adjusted ISIMIP3b atmospheric data by model, variable,
scenario, period, and geographic bounding box. Regional requests use the
official ISIMIP Files API, so a multi-gigabyte global file does not need to be
downloaded before cropping.

## Highlights

- Server-side regional cutouts through the official ISIMIP API
- Atomic outputs, with resume support for large full-file downloads
- Correct ISIMIP model names, ensemble members, and published time blocks
- Concurrent downloads with clear failure reporting
- NetCDF validation for dimensions, variables, dates, and spatial bounds
- Optional multi-file combination and climate4R NcML generation
- A non-interactive CLI suitable for scripts and scheduled jobs

## Quick start

Python 3.10 or newer is sufficient for downloading. From the repository root:

```bash
bash script/isimip.sh \
  -m GFDL-ESM4 \
  -v tas \
  -s historical \
  -x "12 12.6" \
  -y "41.5 42.1" \
  -b 2011 \
  -e 2014
```

This requests a small regional cutout, saves it under `isimip_data/`, and does
not prompt for a Conda environment.

> [!IMPORTANT]
> Omitting `-x` and `-y` downloads complete global files. A single ten-year
> daily file can be around 2 GB. Use a bounding box unless global data are
> genuinely required.

## Command-line options

```text
Usage: isimip.sh -m MODEL -v "VARIABLE [VARIABLE ...]" -s SCENARIO [OPTIONS]

Required:
  -m MODEL       GFDL-ESM4, MPI-ESM1-2-HR, IPSL-CM6A-LR, MRI-ESM2-0,
                 UKESM1-0-LL, or all
  -v VARIABLES   hurs huss pr prsn ps tas tasmax tasmin
  -s SCENARIO    historical, ssp126, ssp585, or all

Options:
  -x "WEST EAST" Longitude bounds
  -y "SOUTH NORTH" Latitude bounds
  -b YEAR         First requested year
  -e YEAR         Last requested year
  -o DIRECTORY    Output directory (default: isimip_data)
  -j WORKERS      Concurrent downloads/API jobs (default: 3)
  -c              Combine files after download
  -n ENVIRONMENT  Create NcML using this Conda environment
  -h              Show help
```

The year options select every published file block that overlaps the requested
range. For example, `-b 2011 -e 2011` downloads the published 2011–2014 block.
Without year options, this project defaults to 1971–2014 for `historical` and
2021–2100 for future scenarios. Set `-b 2015` to include the future-scenario
2015–2020 transition block.

## More examples

Download two variables for a European bounding box:

```bash
bash script/isimip.sh \
  -m MPI-ESM1-2-HR \
  -v "tas pr" \
  -s ssp126 \
  -x "-10 40" \
  -y "35 70" \
  -b 2021 \
  -e 2050 \
  -o climate_data
```

Download and combine the selected files. Run this in a Python environment that
contains `xarray`, `netCDF4`, and `dask`:

```bash
conda run -n isimipclim bash script/isimip.sh \
  -m GFDL-ESM4 \
  -v "tas pr" \
  -s historical \
  -x "12 13" \
  -y "41 42" \
  -b 2001 \
  -e 2014 \
  -c
```

Create optional NcML with an existing Conda environment containing R and the
climate4R `loadeR` package:

```bash
bash script/isimip.sh \
  -m GFDL-ESM4 \
  -v tas \
  -s historical \
  -x "12 13" \
  -y "41 42" \
  -b 2011 \
  -e 2014 \
  -n climate4R
```

The `-n` option is explicit and optional. It uses `conda run`; it does not
activate an environment or alter the current shell.

## Validate downloaded files

Validation requires `xarray` and a NetCDF backend such as `netCDF4`:

```bash
python script/isimip.py validate \
  isimip_data/GFDL-ESM4/historical/*_cropped.nc \
  -v tas \
  --bbox 12 12.6 41.5 42.1
```

The validator checks that the file has a NetCDF/HDF5 signature, non-empty
`time`, `lat`, and `lon` dimensions, the requested variable, and coordinates
inside the requested bounds. It prints a JSON summary for each file.

## Output layout

```text
isimip_data/
├── GFDL-ESM4/
│   └── historical/
│       └── gfdl-esm4_..._tas_global_daily_2011_2014_cropped.nc
├── combined/
│   └── historical/
│       └── GFDL-ESM4_combined.nc
└── ncml/
    └── historical/
        └── GFDL-ESM4_historical.ncml
```

Regional files receive the `_cropped.nc` suffix. Full global downloads retain
the original ISIMIP filename.

## Python API

The downloader can also be imported directly:

```python
from script.isimip import download_isimip_data

files = download_isimip_data(
    model_choices="GFDL-ESM4",
    variables=["tas", "pr"],
    scenario="historical",
    bbox=(12.0, 12.6, 41.5, 42.1),
    start_year=2011,
    end_year=2014,
    output_dir="isimip_data",
)
```

## Optional environments

Downloading and server-side subsetting use only the Python standard library.
Additional features need:

| Feature | Packages |
|---|---|
| Validate NetCDF | `xarray`, `netCDF4` |
| Combine files | `xarray`, `netCDF4`, `dask` |
| Generate NcML | Conda, R, climate4R `loadeR` |

For example, create a Python analysis environment with Mamba:

```bash
mamba create -n isimipclim -c conda-forge python=3.12 xarray netcdf4 dask
```

See the [climate4R documentation](https://github.com/SantanderMetGroup/climate4R)
for its current R installation instructions.

## Test the project

The standard-library regression suite needs no extra packages:

```bash
python -m unittest discover -s tests -v
```

To test a real download, use the small Quick start bounding box and then run
the validator in an environment containing `xarray` and `netCDF4`.

## Data source and citation

The file naming and directory structure follow the
[ISIMIP3 protocol](https://protocol.isimip.org/) and data are retrieved from the
[ISIMIP Repository](https://data.isimip.org/). Consult the repository entry for
the dataset version, license, and citation that apply to data used in research.

This project is a convenience client and is not an official ISIMIP product.
