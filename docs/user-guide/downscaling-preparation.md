# Prepare site training data and ensemble targets

Start with the [downscaling Python environment](downscaling.md) and R 4.4 with
data.table, arrow, dplyr, jsonlite, caret and xgboost (1.7.8.1). Copy
`downscaling/config/prepare.yaml` and point it to the shared inputs and a new
output directory. Relative paths resolve beside the configuration.

| Input | Contents |
|---|---|
| Sites, timezones and folds | Site IDs, site column order and coordinates; IANA timezone by site; fixed site position, land cover, tower flag, outer and inner memberships. |
| Period calendar | Period IDs and keys, raw and clipped start dates, end dates, expected three-hour steps, hydrological year and full-window fraction. |
| Tower and SIPNET data | Nearest tower matches and distances in metres, gap-filled NEE/LE Parquet tables by tower with UTC and ens01–ens25, annual SIPNET RDS files with site IDs, time, NEE and LE ensemble means. |
| Environmental data | Annual ERA files, three-hour GLDAS soil files and the replacement registry, eight Aqua MYD13Q1 point CSVs, native and proxy annual rasters, native land cover, climate classes and their CSV legend. |

From the repository root on a Grid Engine cluster:

```bash
python -m downscaling.prepare preflight --config /path/to/prepare.yaml
python -m downscaling.prepare run --config /path/to/prepare.yaml
python -m downscaling.prepare run --resume --config /path/to/prepare.yaml
```

Set `PREP_SGE_PROJECT` if the cluster requires a project account. The run command
submits CPU jobs. Job IDs are recorded in `jobs.json`, and output from each task
goes to `logs/`. Resume checks completed partitions against their output records.
The SCC worker uses the Python 3.12.4 and R 4.4.3 modules.

Pass `prepared_data/` to the [training command](downscaling-training.md). The
proxy label is the period mean of the raw SIPNET ensemble mean. The three-hour
training labels are the means of all 25 routed NEE or LE members.

Read `targets/nee.h5` and `targets/le.h5` independently. In Python the axes are
period, member, site. Read the matching labels from `period_keys`, `ensemble_ids`
and `site_ids`. NEE uses the training flux convention and LE uses W/m².

* `values` contains the period mean of finite three-hour values for each member.
* `support_counts` gives the number of contributing steps. Zero support gives a missing value.
* `route_codes` gives the most frequent contributing route. Ties follow tower (1), Yang residual (2), then raw SIPNET (3). Zero means no support.
* `expected_available_steps` and `full_window_fraction` describe the actual period window.

Existing shared soil replacements can be used directly. To reconstruct their
registry from the three original NASA downloads, run
`python -m downscaling.scripts.prepare_soil_replacements --downloads /path/to/downloads --output /path/to/replacements`.
