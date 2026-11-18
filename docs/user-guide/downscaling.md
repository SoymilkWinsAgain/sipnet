# SIPNET Downscaling

Produce period maps of net ecosystem exchange (NEE) and latent heat flux (LE)
from environmental covariates. Each of three folds combines a proxy prediction,
a period residual forest and a monthly spatial correction. Start with the shared model below, or
[train a model from SIPNET and tower data](#train-from-sipnet-and-tower-data).

## Make your first map

The BU SCC example predicts period 139 (13–28 July 2018 UTC) at grid row 4864,
column 8704. Ask the project data holder for read access to the
`period_models` bundle and map inputs in `downscaling/config/predict-scc.yaml`,
including the files referenced by the source ledgers. With access, you can read
them in place. For other installations, obtain the bundle from the data holder
and set the paths in `downscaling/config/example.yaml` using the
[data sources guide](downscaling-data.md).

Run the commands from the root of the SIPNET checkout. On SCC, run
`module load python3/3.12.4`. Choose a new writable directory for the
environment and map products. This small example runs on CPU.

```bash
run_dir=../downscaling-first-map
mkdir -p "$run_dir"
python3.12 -m venv "$run_dir/env"
source "$run_dir/env/bin/activate"
python -m pip install 'torch==2.12.1+cpu' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r downscaling/requirements.txt
```

On SCC, copy the shared configuration with its device set to CPU.

```bash
sed 's/device: cuda/device: cpu/' downscaling/config/predict-scc.yaml > "$run_dir/predict.yaml"
```

For your own inputs, save your edited `example.yaml` as `$run_dir/predict.yaml`
with `output: map-products`. Run the prediction from the same shell.

```bash
python -m downscaling.predict preflight --config "$run_dir/predict.yaml"
python -m downscaling.predict run --config "$run_dir/predict.yaml"
python -m downscaling.predict report --config "$run_dir/predict.yaml"
```

For the SCC example, the report lists `NEE_139.tif` and `LE_139.tif` under
`map-products/products/period/` beside the YAML. Open either GeoTIFF in a GIS
or read it in Python. Band 1 holds the three-fold mean prediction for the
period. Band 6 marks eligible pixels with 1.

```bash
python - "$run_dir/map-products/products/period" <<'PYTHON'
from pathlib import Path
import sys
import rasterio

for target in ("NEE", "LE"):
    with rasterio.open(Path(sys.argv[1]) / f"{target}_139.tif") as src:
        mean = src.read(1, masked=True)
        eligible = (src.read(6) == 1).sum()
        print(target, src.shape, "eligible pixels:", eligible, "window mean:", mean.mean())
PYTHON
```

## Train from SIPNET and tower data

Keep the preparation, training and prediction YAMLs together in your run
directory so the following relative paths connect the stages.

1. **Prepare.** Use the [SIPNET handoff](downscaling-preparation.md#supply-your-own-sipnet-output)
   to write annual ensemble means, or use the shared means on SCC. Follow the
   [preparation guide](downscaling-preparation.md) with `prepare-scc.yaml` on
   SCC or `prepare.yaml` with your input paths and `output: site-products`.
   The preparation command submits Grid Engine
   jobs. Once finalization completes, `site-products/targets/nee.h5` and
   `le.h5` hold the 25-member targets and `site-products/prepared_data/`
   contains the training arrays.
2. **Train.** Copy `downscaling/config/train.yaml` to the run directory. Set
   `prepared_data: site-products/prepared_data` and `output: training`. Run the
   [training commands](downscaling-training.md#run-training) to produce
   `training/model_bundle/`.
3. **Predict.** In a copy of your prediction YAML, set
   `model_bundle: training/model_bundle` and `output: trained-maps`. Remove
   `sources.spatial_cache` if present so the spatial correction comes from your
   trained bundle. Run the same prediction commands above with this YAML.

Follow the [small workflow example](downscaling-example.md) to run the SIPNET handoff and exercise training and prediction on one period.

## Installation and devices

For larger requests, use a dedicated Python 3.12 environment with one visible
GPU and the CUDA packages:

```bash
python -m pip install 'torch==2.12.1' --index-url https://download.pytorch.org/whl/cu130
python -m pip install -r downscaling/requirements-cuda.txt
```

Set `resources.device: cuda` (the default in `predict-scc.yaml`). Set
`workers` and `cpu_threads` for your allocation, reserving one process for
model evaluation. The supplied SCC point blocks include timezones. To derive
timezones from coordinates, install R with the `lutz` package and put
`Rscript` on `PATH`.

## Configuration and inputs

Find official downloads, project rasters and a ready-to-use SCC configuration in
[Downscaling data sources](downscaling-data.md).

Copy `downscaling/config/example.yaml` to a writable workspace and fill in the
paths. Relative paths resolve from the configuration file's directory.
`model_bundle`, all `sources` and `output` must be disjoint. The input locations
are read-only. Output must be a new directory for a new request.

The request contains `window: {row, col, height, width}` in zero-based original
grid coordinates and a list of `period_ids`. The grid must be a north-up
EPSG:4326 raster. The period calendar supplies the time interpretation;
arbitrary temporal cuts are not accepted. `resources.max_output_bytes` limits
run storage. Device and source identities are bound to the run. Scientific
feature order and preprocessing belong to the model bundle and cannot be
changed through YAML overrides.

| Input | Contents |
| --- | --- |
| `model_bundle` | Portable training export with three proxies, six residual forests, three monthly spatial models and a manifest of file hashes. |
| `grid`, `periods` | Original EPSG:4326 reference grid and CSV of integer period ID, period key, UTC start and exclusive stop. Include the four preceding windows. |
| `era` | Annual classic NetCDF ensemble files `ERA5_YEAR.nc` with t2m, sp, d2m, u10, v10, tp, ssrd and strd. Accumulations end at the next three-hour boundary. |
| `soil_cache` | One `gldas_NNN.npy` and its JSON metadata per period. Axes are three-hour time, latitude, longitude and four soil layers. Metadata identifies the array, sources, coordinates and UTC boundaries. |
| `aqua_ledger` | Dated MYD13Q1 tile inventory with original HDF, NDVI/EVI layer identities and acquisition receipts. Include 64 days around the predictor windows for short-gap interpolation. |
| `annual_covariates`, `native_covariates` | Annual proxy raster bands LC=1, disturbance age=2, biomass=3, pH=8, N=9, SOC=10, sand=11, elevation=15. Native bands are age=2, biomass=3, wetness=4, pH=9, N=10, SOC=11 and sand=12. Annual proxy rasters for 2013–2024 define the land union. |
| `native_lc`, `kgc`, `kgc_legend` | Native land-cover raster, climate-class raster and its code-to-KGC CSV. |
| `domain` | Optional directory with the land-union raster `domain.tif` and its annual-source manifest `domain.json`. Full-grid requests can also use its `modis_tiles.json` inventory. |
| `timezones` or `point_blocks` | Optional pixel timezone table (index, lat, lon, tz), or the shared directory of verified 256 × 256 point blocks. Without either source the run uses R and lutz. |
| `spatial_cache` | Optional shared production directory with the model manifest and precomputed monthly spatial corrections under `continental_cache/spatial/`. Omit for newly trained models. |

## Run and inspect products

To continue an interrupted prediction, run
`python -m downscaling.predict run --resume --config "$run_dir/predict.yaml"`.

Group consecutive period IDs in one request to reuse the four preceding panels
in each block. Set the optional top-level `panel_cache` to the same writable
directory across requests to continue reuse between runs. Panels depend on the
period, pixel coordinates, covariate sources and sampling recipe. Changing the
model or device allows the same panels to be reused in a new output directory.
Resume verifies the frozen request and completed blocks.

Each period produces `products/period/NEE_NNN.tif` and `LE_NNN.tif`.
Bands 1–2 give the ensemble mean and population standard deviation; bands 3–5
give the individual folds. Band 6 is eligibility (1 or 0 on land). Band 7
records missing-reason bits: 1 for proxy/context, 2 for native land cover or
climate class, and 4 for timezone. Water is NoData. LE is in W/m² and NEE uses
the Yang target convention. Raster tags record UTC coverage and model identity.

To check a window against published grids on the same device, supply a directory
with reference files such as `p003/NEE_003.tif` and `p003/LE_003.tif`.

```bash
python -m downscaling.scripts.validate --config /path/to/run.yaml --reference /path/to/continental
```
