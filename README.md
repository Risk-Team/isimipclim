# Easy Downloading and Processing of ISIMIP3a-b Climate Data
## What the script does
The script folder contains the function that needs to be called from a terminal to download and then aggregate (optional) the netCDF files into ncml files. The script allows you to easily download the ISIMIP 3a and b climate data for a specific region, variable, climate model, and scenario.

## Requirements
### Linux
Only wget and cdo are needed. These can be easily installed with:

```
sudo apt-get install wget
sudo apt-get install cdo
```
To process multiple files efficiently, install `parallel`
```
sudo apt update
sudo apt install parallel
```

For aggregating netCDF files into ncml files (optional), it is necessary to have a conda environment with R and climate4R packages installed. This can be done easily with:

```
conda create --name climate4R
conda activate climate4R
conda install -c conda-forge -c r -c defaults -c santandermetgroup climate4r
```
### Windows
**If you are on Windows, install the Windows Subsystem for Linux (WSL) first**, then follow the instructions for Linux OS.

## How to run it
Open a terminal, place the isimip.sh script anywhere and run:

```
bash isimip.sh
```
```
Download ISIMIP 3 climate data with specified parameters.

Options:
  -m   model_choices    Specify the model choices: GFDL-ESM4, MPI-ESM1-2-HR, IPSL-CM6A-LR, MRI-ESM2-0, UKESM1-0-LL, all
  -v   variable         Specify one or more variables separated by space: hurs, huss, pr, prsn, ps, tas, tasmax, tasmin. Enclose multiple variables in quotes separated by a space
  -s   scenario         Specify the scenario: historical, ssp126, ssp585, all
  -x   xmin             Specify the minimum longitude value for cropping (optional)
  -x   xmax             Specify the maximum longitude value for cropping (optional)
  -y   ymin             Specify the minimum latitude value for cropping (optional)
  -y   ymax             Specify the maximum latitude value for cropping (optional)


```
Example:
```
bash isimip.sh -m all -v tas -s historical -x "-120 -100" -y "30 40"

Please specify a conda environment to activate if ncml files are to be generated. Type 'no' if you only want to download the data. The required conda env installation instruction can be retrieved here https://github.com/SantanderMetGroup/climate4R


```

This would Download for all models and scenarios, the daily data for tas.
