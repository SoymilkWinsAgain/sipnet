# Find downscaling inputs

## Use the shared inputs on BU SCC

From the repository root, copy the configuration for your task to a writable
directory outside the checkout. The examples read from
`/projectnb/dietzelab/Julian_Zhu/shared/downscaling/` using your SCC account.

```bash
run_dir=/path/to/your/workspace
mkdir -p "$run_dir"
cp downscaling/config/prepare-scc.yaml "$run_dir/"
python -m downscaling.prepare preflight --config "$run_dir/prepare-scc.yaml"

cp downscaling/config/predict-scc.yaml "$run_dir/"
python -m downscaling.predict preflight --config "$run_dir/predict-scc.yaml"
```

Site preparation writes to `site-products/` beside its YAML. Map prediction writes
to `map-products/` beside its YAML. Use the same configuration for the subsequent
`run` command. See [site preparation](downscaling-preparation.md) and
[map prediction](downscaling.md) for installation and execution.

## Obtain the inputs elsewhere

The configuration keys below are under `sources` except for the top-level
`model_bundle`. For project products, request the listed files from the data
holder or copy them from the SCC locations in the example configurations.

| Input and configuration keys | Where to get it and what to supply |
| --- | --- |
| ERA5 meteorology · `era` | Download the three-hour ensemble members on single levels. Supply annual classic NetCDF files with the eight fields and axis order in the [map input contract](downscaling.md#configuration-and-inputs). [Copernicus Climate Data Store](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels) |
| GLDAS soil moisture · `gldas`, `gldas_registry` | Download GLDAS Noah 0.25° three-hourly 2.1 with all four soil moisture layers. Use the shared replacement registry with the shared archive. For a complete original archive, supply a registry with an empty replacement list. [NASA GES DISC](https://doi.org/10.5067/E7TYRXPJKWOQ) · [Replacement procedure](downscaling-preparation.md) |
| Aqua vegetation at sites · `aqua_points` | Request MYD13Q1.061 NDVI and EVI for the project site coordinates as point CSVs. The shared collection contains eight AppEEARS exports in physical index units. [AppEEARS](https://appeears.earthdatacloud.nasa.gov/) |
| Aqua vegetation on the grid · `aqua_catalog`, `aqua_receipts` | Download MYD13Q1.061 granules for the requested tiles and dates. The project collection pairs a catalog with the original HDFs, extracted packed NDVI/EVI rasters and per-granule file records. See the [file layout](downscaling.md#configuration-and-inputs). [NASA LP DAAC](https://doi.org/10.5067/MODIS/MYD13Q1.061) |
| Land surface · `native_covariates`, `annual_covariates`, `native_lc` | Obtain both project raster stacks and the native land-cover raster. The stacks have different band orders and year conventions. [Band definitions](downscaling.md#configuration-and-inputs) |
| Climate classes · `kgc`, `kgc_legend` | Obtain the project Köppen–Geiger raster together with its code-to-class CSV. |
| Station domain · `sites`, `timezones`, `folds`, `periods` | Obtain the project site index, site timezone lookup, fixed fold assignments and site period calendar. The site index supplies the IDs used in the annual SIPNET means. |
| Tower targets · `matched_towers`, `tower` | Obtain the project site-to-tower match table and gap-filled NEE and LE Parquet files with members `ens01`–`ens25`. |
| SIPNET fluxes · `sipnet_means` | Use the shared annual ensemble means or build `year=YYYY.rds` from your SIPNET runs. See the [SIPNET handoff](downscaling-preparation.md#supply-your-own-sipnet-output). |
| Map domain · `grid`, `periods`, `timezones` | Obtain the project reference GeoTIFF and map calendar. Supply pixel timezones for the requested land pixels or omit the lookup to derive timezones with R and lutz. |
| Prediction models · `model_bundle` | Use the shared bundle or export one with the [training workflow](downscaling-training.md). |

**Station coverage.** The shared site calendar covers 8,000 sites from
2012-07-16 through 2024-07-15 inclusive. Environmental predictors start on
2012-05-09 to cover the four preceding windows. Annual SIPNET means span the
label dates at three-hour intervals. Tower data are keyed by tower ID and UTC.

**Map coverage.** The shared map configuration selects period 139 and a 16 × 16
window at row 4864, column 8704. Its pixel timezone table covers that example.
For another request, select the corresponding grid cells, timezone coverage
and four preceding predictor windows. Include 64 days of Aqua context on
either side for gap filling and the ERA accumulation at the closing three-hour
boundary. The 2013–2024 annual rasters define the union land mask.
