#!/usr/bin/env python3
"""Download, subset, combine, and validate ISIMIP3b climate files."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


LOGGER = logging.getLogger("isimipclim")
BASE_PATH = "ISIMIP3b/InputData/climate/atmosphere/bias-adjusted/global/daily"
FILES_URL = "https://files.isimip.org"
FILES_API_URL = f"{FILES_URL}/api/v2"

MODELS = {
    "GFDL-ESM4": ("gfdl-esm4", "r1i1p1f1"),
    "MPI-ESM1-2-HR": ("mpi-esm1-2-hr", "r1i1p1f1"),
    "IPSL-CM6A-LR": ("ipsl-cm6a-lr", "r1i1p1f1"),
    "MRI-ESM2-0": ("mri-esm2-0", "r1i1p1f1"),
    "UKESM1-0-LL": ("ukesm1-0-ll", "r1i1p1f2"),
}
VARIABLES = ("hurs", "huss", "pr", "prsn", "ps", "tas", "tasmax", "tasmin")
SCENARIOS = ("historical", "ssp126", "ssp585")


@dataclass(frozen=True)
class RemoteFile:
    """One published ISIMIP file and its repository location."""

    model: str
    scenario: str
    variable: str
    start_year: int
    end_year: int

    @property
    def name(self) -> str:
        model_slug, ensemble = MODELS[self.model]
        return (
            f"{model_slug}_{ensemble}_w5e5_{self.scenario}_{self.variable}_"
            f"global_daily_{self.start_year}_{self.end_year}.nc"
        )

    @property
    def path(self) -> str:
        return f"{BASE_PATH}/{self.scenario}/{self.model}/{self.name}"

    @property
    def url(self) -> str:
        return f"{FILES_URL}/{self.path}"


def available_periods(scenario: str) -> list[tuple[int, int]]:
    """Return the file blocks published for an ISIMIP3b scenario."""
    if scenario == "historical":
        return [
            (1850, 1850),
            *[(year, year + 9) for year in range(1851, 2011, 10)],
            (2011, 2014),
        ]
    return [(2015, 2020), *[(year, year + 9) for year in range(2021, 2100, 10)]]


def build_file_list(
    model_choices: str | Sequence[str],
    variables: str | Sequence[str],
    scenario: str,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[RemoteFile]:
    """Build the published file blocks overlapping the requested period.

    With no explicit period, historical requests use 1971–2014 and future
    scenarios use 2021–2100. ISIMIP files are indivisible time blocks, so a
    partial-period request selects the complete overlapping block.
    """
    models = (
        list(MODELS)
        if model_choices == "all" or model_choices == ["all"]
        else _as_list(model_choices)
    )
    variables_list = _as_list(variables)
    scenarios = list(SCENARIOS) if scenario == "all" else [scenario]

    unknown_models = sorted(set(models) - set(MODELS))
    unknown_variables = sorted(set(variables_list) - set(VARIABLES))
    unknown_scenarios = sorted(set(scenarios) - set(SCENARIOS))
    if unknown_models:
        raise ValueError(f"Unknown model(s): {', '.join(unknown_models)}")
    if unknown_variables:
        raise ValueError(f"Unknown variable(s): {', '.join(unknown_variables)}")
    if unknown_scenarios:
        raise ValueError(f"Unknown scenario(s): {', '.join(unknown_scenarios)}")
    if start_year is not None and end_year is not None and start_year > end_year:
        raise ValueError("start_year must be less than or equal to end_year")

    files: list[RemoteFile] = []
    for model in models:
        for scen in scenarios:
            default_start = 1971 if scen == "historical" else 2021
            first_year = default_start if start_year is None else start_year
            last_year = 2014 if scen == "historical" else 2100
            last_year = last_year if end_year is None else end_year
            periods = [
                period
                for period in available_periods(scen)
                if period[1] >= first_year and period[0] <= last_year
            ]
            if not periods:
                raise ValueError(
                    f"No published {scen} file blocks overlap {first_year}–{last_year}"
                )
            for variable in variables_list:
                files.extend(RemoteFile(model, scen, variable, *period) for period in periods)
    return files


def _as_list(value: str | Sequence[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def validate_bbox(bbox: tuple[float, float, float, float] | None) -> None:
    """Validate a ``(west, east, south, north)`` geographic bounding box."""
    if bbox is None:
        return
    west, east, south, north = bbox
    if not (-180 <= west < east <= 180):
        raise ValueError("bbox longitudes must satisfy -180 <= west < east <= 180")
    if not (-90 <= south < north <= 90):
        raise ValueError("bbox latitudes must satisfy -90 <= south < north <= 90")


def _request_json(url: str, *, data: dict | None = None, timeout: int = 120) -> dict:
    encoded = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json", "User-Agent": "isimipclim/1.0"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"ISIMIP request failed ({error.code}): {detail}") from error


def _download(url: str, destination: Path, timeout: int = 300) -> Path:
    """Stream a URL to a resumable .part file and atomically publish it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    headers = {"User-Agent": "isimipclim/1.0"}
    mode = "wb"
    if partial.exists() and partial.stat().st_size:
        headers["Range"] = f"bytes={partial.stat().st_size}-"
        mode = "ab"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if mode == "ab" and response.status != 206:
                mode = "wb"
            initial_size = partial.stat().st_size if mode == "ab" else 0
            total_size = _response_size(response, initial_size)
            if initial_size:
                LOGGER.info(
                    "Resuming %s at %s of %s",
                    destination.name,
                    _format_size(initial_size),
                    _format_size(total_size) if total_size else "unknown size",
                )
            elif total_size >= 50 * 1024 * 1024:
                LOGGER.info(
                    "Downloading %s (%s)",
                    destination.name,
                    _format_size(total_size),
                )
            with partial.open(mode) as stream:
                _copy_with_progress(response, stream, destination.name, initial_size, total_size)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Download failed for {url} ({error.code})") from error

    partial.replace(destination)
    return destination


def _response_size(response, initial_size: int) -> int:
    content_range = response.headers.get("Content-Range")
    if content_range and "/" in content_range:
        total = content_range.rsplit("/", 1)[1]
        return int(total) if total.isdigit() else 0
    content_length = response.headers.get("Content-Length")
    return initial_size + int(content_length) if content_length else 0


def _copy_with_progress(source, target, name: str, initial_size: int, total_size: int) -> None:
    copied = initial_size
    next_percentage = 10
    while chunk := source.read(1024 * 1024):
        target.write(chunk)
        copied += len(chunk)
        if total_size >= 50 * 1024 * 1024:
            percentage = int(copied * 100 / total_size)
            if percentage >= next_percentage:
                LOGGER.info("%s: %d%%", name, min(percentage, 100))
                next_percentage = (percentage // 10 + 1) * 10


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _wait_for_job(job: dict, poll_seconds: float = 2.0, timeout: int = 1800) -> dict:
    deadline = time.monotonic() + timeout
    job_id = job.get("id", "<unknown>")
    last_status = None
    while job.get("status") in {"queued", "started"}:
        if job.get("status") != last_status:
            LOGGER.info("ISIMIP cutout job %s: %s", job_id, job.get("status"))
            last_status = job.get("status")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for ISIMIP job {job_id}")
        time.sleep(poll_seconds)
        job = _request_json(job["job_url"])
    if job.get("status") != "finished" or not job.get("file_url"):
        raise RuntimeError(f"ISIMIP subset job failed: {job}")
    LOGGER.info("ISIMIP cutout job %s: finished", job_id)
    return job


def _netcdf_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return [
        member
        for member in archive.infolist()
        if not member.is_dir() and Path(member.filename).suffix == ".nc"
    ]


def _download_subset(
    remote: RemoteFile,
    bbox: tuple[float, float, float, float],
    destination: Path,
) -> Path:
    west, east, south, north = bbox
    payload = {
        "paths": [remote.path],
        "operations": [{"operation": "cutout_bbox", "bbox": [west, east, south, north]}],
    }
    job = _wait_for_job(_request_json(FILES_API_URL, data=payload))
    with tempfile.TemporaryDirectory(prefix="isimipclim-") as temp_dir:
        archive_path = _download(job["file_url"], Path(temp_dir) / "subset.zip")
        with zipfile.ZipFile(archive_path) as archive:
            members = _netcdf_members(archive)
            if len(members) != 1:
                raise RuntimeError(
                    f"Expected one NetCDF result for {remote.name}, found {len(members)}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_suffix(destination.suffix + ".part")
            with archive.open(members[0]) as source, partial.open("wb") as target:
                shutil.copyfileobj(source, target)
            partial.replace(destination)
    return destination


def _has_netcdf_signature(path: Path) -> bool:
    with path.open("rb") as stream:
        signature = stream.read(8)
    return signature.startswith(b"CDF") or signature == b"\x89HDF\r\n\x1a\n"


def _download_one(
    remote: RemoteFile,
    output_dir: Path,
    bbox: tuple[float, float, float, float] | None,
) -> Path:
    suffix = "_cropped.nc" if bbox else ".nc"
    destination = (
        output_dir
        / remote.model
        / remote.scenario
        / f"{Path(remote.name).stem}{suffix}"
    )
    if destination.exists() and _has_netcdf_signature(destination):
        LOGGER.info("Already present: %s", destination)
        return destination

    LOGGER.info("Fetching %s", remote.name)
    result = (
        _download_subset(remote, bbox, destination)
        if bbox
        else _download(remote.url, destination)
    )
    if not _has_netcdf_signature(result):
        result.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file is not NetCDF/HDF5: {result}")
    LOGGER.info("Saved %s", result)
    return result


def download_isimip_data(
    model_choices: str | Sequence[str],
    variables: str | Sequence[str],
    scenario: str,
    bbox: tuple[float, float, float, float] | None = None,
    output_dir: str | Path = ".",
    max_workers: int = 3,
    combine_files: bool = False,
    conda_env: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[Path]:
    """Download full files or server-side spatial cutouts from ISIMIP3b.

    Downloads run concurrently up to ``max_workers``. Completed paths are
    returned in sorted order; any failed transfer makes the overall operation
    fail after the active tasks finish.
    """
    validate_bbox(bbox)
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    remotes = build_file_list(model_choices, variables, scenario, start_year, end_year)
    root = Path(output_dir)
    downloaded: list[Path] = []
    errors: list[str] = []

    request_type = "regional cutout" if bbox else "full global file"
    LOGGER.info(
        "Planned %d %s download(s) with %d worker(s); output: %s",
        len(remotes),
        request_type,
        max_workers,
        root.resolve(),
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_download_one, remote, root, bbox): remote for remote in remotes}
        for future in as_completed(futures):
            remote = futures[future]
            try:
                downloaded.append(future.result())
            except Exception as error:
                LOGGER.error("%s: %s", remote.name, error)
                errors.append(f"{remote.name}: {error}")
    if errors:
        raise RuntimeError(f"{len(errors)} download(s) failed; first error: {errors[0]}")

    if conda_env:
        create_ncml_files(model_choices, scenario, root, conda_env)
    if combine_files:
        combine_netcdf_files(model_choices, scenario, root)
    return sorted(downloaded)


def create_ncml_files(
    model_choices: str | Sequence[str], scenario: str, output_dir: str | Path, conda_env: str
) -> list[Path]:
    """Create one climate4R NcML catalog per model and scenario."""
    models = (
        list(MODELS)
        if model_choices == "all" or model_choices == ["all"]
        else _as_list(model_choices)
    )
    scenarios = list(SCENARIOS) if scenario == "all" else [scenario]
    outputs: list[Path] = []
    env_flag = "-p" if "/" in conda_env else "-n"
    for scen in scenarios:
        for model in models:
            source_dir = Path(output_dir).resolve() / model / scen
            if not any(source_dir.glob("*.nc")):
                LOGGER.warning("No NetCDF files in %s; skipping NcML", source_dir)
                continue
            target = Path(output_dir).resolve() / "ncml" / scen / f"{model}_{scen}.ncml"
            target.parent.mkdir(parents=True, exist_ok=True)
            r_script = (
                "library(loadeR); makeAggregatedDataset("
                f"source.dir={json.dumps(str(source_dir))}, ncml.file={json.dumps(str(target))})"
            )
            LOGGER.info("Creating NcML catalog %s using Conda environment %s", target, conda_env)
            subprocess.run(
                ["conda", "run", env_flag, conda_env, "Rscript", "-e", r_script],
                check=True,
            )
            outputs.append(target)
    return outputs


def combine_netcdf_files(
    model_choices: str | Sequence[str], scenario: str, output_dir: str | Path
) -> list[Path]:
    """Combine time blocks and variables into one NetCDF file per model/scenario."""
    try:
        import xarray as xr
    except ImportError as error:
        raise RuntimeError("Combining files requires xarray, netCDF4, and dask") from error

    models = (
        list(MODELS)
        if model_choices == "all" or model_choices == ["all"]
        else _as_list(model_choices)
    )
    scenarios = list(SCENARIOS) if scenario == "all" else [scenario]
    outputs: list[Path] = []
    for scen in scenarios:
        for model in models:
            source_dir = Path(output_dir) / model / scen
            sources = sorted(source_dir.glob("*.nc"))
            if not sources:
                LOGGER.warning("No NetCDF files in %s; skipping combination", source_dir)
                continue
            destination = Path(output_dir) / "combined" / scen / f"{model}_combined.nc"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".nc.part")
            LOGGER.info("Combining %d file(s) into %s", len(sources), destination)
            with xr.open_mfdataset(sources, combine="by_coords") as dataset:
                dataset.sortby("time").to_netcdf(temporary, format="NETCDF4")
            temporary.replace(destination)
            LOGGER.info("Saved %s", destination)
            outputs.append(destination)
    return outputs


def validate_netcdf(
    path: str | Path,
    expected_variable: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict:
    """Validate core ISIMIP coordinates and return a metadata summary.

    The check covers the file signature, required non-empty dimensions,
    chronological time order, optional variable name, and optional bounds.
    """
    try:
        import xarray as xr
    except ImportError as error:
        raise RuntimeError(
            "Validation requires xarray and a NetCDF backend such as netCDF4"
        ) from error

    path = Path(path)
    if not _has_netcdf_signature(path):
        raise ValueError(f"Not a NetCDF/HDF5 file: {path}")
    with xr.open_dataset(path) as dataset:
        missing_dimensions = {"time", "lat", "lon"} - set(dataset.dims)
        if missing_dimensions:
            raise ValueError(f"Missing dimensions: {', '.join(sorted(missing_dimensions))}")
        if expected_variable and expected_variable not in dataset.data_vars:
            raise ValueError(
                f"Expected variable {expected_variable!r}; "
                f"found {list(dataset.data_vars)}"
            )
        if dataset.sizes["time"] == 0 or dataset.sizes["lat"] == 0 or dataset.sizes["lon"] == 0:
            raise ValueError("time, lat, and lon dimensions must be non-empty")
        if not dataset.indexes["time"].is_monotonic_increasing:
            raise ValueError("Time coordinate must be monotonically increasing")
        if bbox:
            validate_bbox(bbox)
            west, east, south, north = bbox
            tolerance = 0.51
            lon_min, lon_max = float(dataset.lon.min()), float(dataset.lon.max())
            lat_min, lat_max = float(dataset.lat.min()), float(dataset.lat.max())
            if lon_min < west - tolerance or lon_max > east + tolerance:
                raise ValueError("Longitude coordinates fall outside the requested bbox")
            if lat_min < south - tolerance or lat_max > north + tolerance:
                raise ValueError("Latitude coordinates fall outside the requested bbox")
        variables = {
            name: {
                "dimensions": list(data.dims),
                "dtype": str(data.dtype),
                "units": data.attrs.get("units"),
                "standard_name": data.attrs.get("standard_name"),
                "compression": data.encoding.get("zlib"),
                "compression_level": data.encoding.get("complevel"),
            }
            for name, data in dataset.data_vars.items()
        }
        summary = {
            "path": str(path),
            "dimensions": dict(dataset.sizes),
            "variables": variables,
            "longitude": [float(dataset.lon.min()), float(dataset.lon.max())],
            "latitude": [float(dataset.lat.min()), float(dataset.lat.max())],
            "time": [str(dataset.time.values[0]), str(dataset.time.values[-1])],
        }
    return summary


def _parse_bbox(values: Sequence[float] | None) -> tuple[float, float, float, float] | None:
    return tuple(values) if values is not None else None  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download", help="download full files or bbox cutouts")
    download_parser.add_argument(
        "-m", "--model", nargs="+", required=True, choices=[*MODELS, "all"]
    )
    download_parser.add_argument("-v", "--variable", nargs="+", required=True, choices=VARIABLES)
    download_parser.add_argument("-s", "--scenario", required=True, choices=[*SCENARIOS, "all"])
    download_parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("WEST", "EAST", "SOUTH", "NORTH"),
    )
    download_parser.add_argument("--start-year", type=int)
    download_parser.add_argument("--end-year", type=int)
    download_parser.add_argument("-o", "--output-dir", default="isimip_data")
    download_parser.add_argument("-j", "--max-workers", type=int, default=3)
    download_parser.add_argument("--combine", action="store_true")
    download_parser.add_argument(
        "--conda-env", help="Conda environment containing R and loadeR for NcML"
    )

    validate_parser = subparsers.add_parser("validate", help="validate downloaded NetCDF files")
    validate_parser.add_argument("paths", nargs="+")
    validate_parser.add_argument("-v", "--variable", choices=VARIABLES)
    validate_parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("WEST", "EAST", "SOUTH", "NORTH"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "download":
            files = download_isimip_data(
                model_choices=args.model,
                variables=args.variable,
                scenario=args.scenario,
                bbox=_parse_bbox(args.bbox),
                output_dir=args.output_dir,
                max_workers=args.max_workers,
                combine_files=args.combine,
                conda_env=args.conda_env,
                start_year=args.start_year,
                end_year=args.end_year,
            )
            LOGGER.info("Completed %d file(s)", len(files))
        else:
            for file_path in args.paths:
                summary = validate_netcdf(
                    file_path, args.variable, _parse_bbox(args.bbox)
                )
                print(json.dumps(summary, indent=2))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
