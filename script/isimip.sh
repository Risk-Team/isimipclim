#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: isimip.sh -m MODEL -v "VARIABLE [VARIABLE ...]" -s SCENARIO [OPTIONS]

Download ISIMIP3b daily, bias-adjusted climate data. A bounding box is cut out
on the ISIMIP server, so regional downloads do not require a global file first.

Required:
  -m MODEL       GFDL-ESM4, MPI-ESM1-2-HR, IPSL-CM6A-LR, MRI-ESM2-0,
                 UKESM1-0-LL, or all
  -v VARIABLES   One or more of: hurs huss pr prsn ps tas tasmax tasmin
  -s SCENARIO    historical, ssp126, ssp585, or all

Options:
  -x "WEST EAST" Longitude bounds (default: full globe)
  -y "SOUTH NORTH" Latitude bounds (default: full globe)
  -b YEAR         First requested year (whole overlapping file blocks are used)
  -e YEAR         Last requested year (whole overlapping file blocks are used)
  -o DIRECTORY    Output directory (default: isimip_data)
  -j WORKERS      Concurrent downloads/jobs (default: 3)
  -c              Combine downloaded files after completion (requires xarray)
  -n ENVIRONMENT  Create NcML using R/loadeR from this Conda environment
  -h              Show this help
EOF
}

model=""
variables=""
scenario=""
xlim=""
ylim=""
start_year=""
end_year=""
output_dir="isimip_data"
workers="3"
combine=false
conda_env=""

while getopts ":hm:v:s:x:y:b:e:o:j:cn:" option; do
    case "$option" in
        h) usage; exit 0 ;;
        m) model=$OPTARG ;;
        v) variables=$OPTARG ;;
        s) scenario=$OPTARG ;;
        x) xlim=$OPTARG ;;
        y) ylim=$OPTARG ;;
        b) start_year=$OPTARG ;;
        e) end_year=$OPTARG ;;
        o) output_dir=$OPTARG ;;
        j) workers=$OPTARG ;;
        c) combine=true ;;
        n) conda_env=$OPTARG ;;
        :) echo "Option -$OPTARG requires an argument." >&2; usage >&2; exit 2 ;;
        \?) echo "Unknown option: -$OPTARG" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ -z "$model" || -z "$variables" || -z "$scenario" ]]; then
    echo "Options -m, -v, and -s are required." >&2
    usage >&2
    exit 2
fi
if [[ -n "$xlim" && -z "$ylim" ]] || [[ -z "$xlim" && -n "$ylim" ]]; then
    echo "Both -x and -y are required for a regional cutout." >&2
    exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
python_command=${PYTHON:-python3}
command=("$python_command" "$script_dir/isimip.py" download -m "$model" -v)
read -r -a variable_array <<< "$variables"
command+=("${variable_array[@]}" -s "$scenario" -o "$output_dir" -j "$workers")

if [[ -n "$xlim" ]]; then
    read -r -a longitude_array <<< "$xlim"
    read -r -a latitude_array <<< "$ylim"
    if [[ ${#longitude_array[@]} -ne 2 || ${#latitude_array[@]} -ne 2 ]]; then
        echo "-x and -y each require exactly two quoted numbers." >&2
        exit 2
    fi
    command+=(--bbox "${longitude_array[@]}" "${latitude_array[@]}")
fi
[[ -n "$start_year" ]] && command+=(--start-year "$start_year")
[[ -n "$end_year" ]] && command+=(--end-year "$end_year")
[[ "$combine" == true ]] && command+=(--combine)
[[ -n "$conda_env" ]] && command+=(--conda-env "$conda_env")

printf 'Running:'
printf ' %q' "${command[@]}"
printf '\n'
"${command[@]}"
