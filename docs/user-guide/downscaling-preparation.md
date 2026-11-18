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

## Supply your own SIPNET output

Yang reads annual ensemble means from `sources.sipnet_means`. To exercise
the handoff, run the following from the repository root with a new output
directory. The first command uses base R. The second uses the preparation
environment's data.table, arrow and dplyr packages.

```bash
means=/path/to/workspace/russell-means
Rscript --vanilla examples/downscaling/annual_means.R examples/downscaling/members.csv 2016 "$means"
Rscript --vanilla examples/downscaling/check.R "$means"
```

The manifest groups two real Russell Ranch SIPNET runs as members of example
site 1. The check reads their 2,928 three-hour intervals through the Yang
reader and compares both fluxes to the original model output.

For your runs, supply one CSV row per `site_id,member_id,output,climate`.
Use the integer site IDs from the preparation site index and the same set of
SIPNET members at each site. File paths may be absolute or relative to the CSV.
Provide named `sipnet.out` columns and their matching three-hour `sipnet.clim`
forcing on a UTC clock. The Russell files declare a 0.125-day step but their
printed hours drift within each three-hour slot. The example uses the slot
start as its UTC timestamp, so 00:00 labels [00:00, 03:00) UTC. For runs on a
local clock, convert both output and forcing timestamps to UTC first.

| SIPNET output, per three-hour step | Annual RDS value | Sign |
| --- | --- | --- |
| `nee`, g C/m² | Divide by 1,000 and 10,800 to obtain kg C/m²/s. | Positive for net carbon release to the atmosphere. |
| `evapotranspiration`, cm of water | Multiply by 10 kg/m² per cm and the latent heat of vaporization, then divide by 10,800 to obtain W/m². | Positive for water loss to the atmosphere. |

LE follows the latent heat used by the [PEcAn SIPNET converter](https://github.com/PecanProject/pecan/blob/368c3b76086e2891cbd12fcebd9b3e422fb9d579/models/sipnet/R/model2netcdf.SIPNET.R).
For example, the two members' first NEE values are 0.070 and 0.092 g C/m²,
giving an annual-file entry of `7.5e-9` kg C/m²/s. Yang applies its NEE scale
(`1e8 / 1.157407`) on read and uses LE directly.

Run the writer once per input year and point `sources.sipnet_means` to the
resulting directory. Each `year=YYYY.rds` contains `site_ids`, UTC `time`,
and two site-by-time matrices, `nee` and `le`. Each entry averages the
available SIPNET members for that site, time and flux. Yang combines these
means with the 25 tower target members during site preparation.
