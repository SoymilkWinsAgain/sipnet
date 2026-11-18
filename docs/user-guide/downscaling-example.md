# Check the downscaling workflow

Run both checks from the repository root. Use a writable workspace outside the
checkout and activate the [downscaling Python environment](downscaling.md#make-your-first-map).

## Read annual SIPNET output through Yang

The two Russell Ranch runs and their member list are included in the repository.
Use R 4.4 with data.table, arrow and dplyr.

```bash
workspace=../downscaling-check
mkdir -p "$workspace"
Rscript --vanilla examples/downscaling/annual_means.R examples/downscaling/members.csv 2016 "$workspace/russell-means"
Rscript --vanilla examples/downscaling/check.R "$workspace/russell-means"
```

The check compares annual NEE and LE with both source runs and reads all 2,928
three-hour timestamps through Yang.

## Train on one period and make two maps

This check uses a separate BU SCC sample. Obtain read access to
`site-sample/` under the [shared downscaling collection](downscaling-data.md)
and the map covariates listed in `downscaling/config/predict-scc.yaml`.
The sample contains the prepared arrays and Yang target products for sites
3501–4000 in period 1, covering 16–27 July 2012 UTC.

```bash
sample=/projectnb/dietzelab/Julian_Zhu/shared/downscaling/site-sample
python examples/downscaling/check_sample.py "$sample" "$workspace/site-run"
```

Use a fresh output directory for each fit. The script checks the site order,
25 members and 96 three-hour steps of support in both target files, runs
training on CPU, then uses the exported bundle on a 16 × 16 map window. The
training preflight reports 500 sites, 488 eligible proxy rows and 46,848 native
rows. The prediction report lists `NEE_001.tif` and `LE_001.tif` in
`site-run/map-products/products/period/`. Both maps have 256 eligible pixels.

To exercise CUDA, use the [CUDA environment](downscaling.md#installation-and-devices)
with one allocated GPU and pass `--device cuda` with a new output directory.
