"""Site preparation inputs and the fixed three-hour and period calendars."""

from pathlib import Path
import pandas as pd
import yaml

from downscaling.artifacts import digest, identity, read, sha, write
from downscaling.contracts import unique


class Preparation:
    def __init__(self, config):
        self.config = Path(config).resolve()
        settings = yaml.safe_load(self.config.read_text())
        self.paths = {k: (self.config.parent / v).resolve() for k, v in settings["sources"].items()}
        self.output = (self.config.parent / settings["output"]).resolve()
        for path in [Path(__file__).resolve().parents[2], *self.paths.values()]:
            if self.output.is_relative_to(path) or path.is_relative_to(self.output):
                raise ValueError(f"Choose a separate preparation output directory: {self.output}")
        self.sites = pd.read_csv(self.paths["sites"]).sort_values("site_col")
        for key in ("index", "site_col"):
            unique(self.sites, [key])
        self.sites = self.sites.merge(pd.read_csv(self.paths["timezones"]), on="index", validate="one_to_one", how="left")
        if self.sites.tz.isna().any():
            raise ValueError("Missing site timezone")
        self.periods = pd.read_csv(self.paths["periods"])
        self.periods["start"] = pd.to_datetime(self.periods.start_date, utc=True)
        self.periods["stop"] = pd.to_datetime(self.periods.end_date, utc=True) + pd.Timedelta(days=1)
        first = pd.Timestamp(self.periods.raw_start_date.iloc[0], tz="UTC")
        history = []
        for lag in range(4, 0, -1):
            start = first - pd.Timedelta(days=16 * lag)
            history.append(dict(period_id=1-lag, period_key=str((start + pd.Timedelta(days=7)).date()),
                                start=start, stop=start + pd.Timedelta(days=16)))
        self.calendar = pd.concat([pd.DataFrame(history), self.periods], ignore_index=True)
        self.chunks = sorted(set(((self.sites["index"] - 1) // 500 + 1).tolist()))
        self.years = sorted(set(self.periods.start.dt.year) | set(self.periods.end_date.str[:4].astype(int)))
        self.partitions = [(year, chunk) for year in self.years for chunk in self.chunks]
        registry = self.paths["gldas_registry"]
        self.repairs = {}
        for row in read(registry)["replacements"]:
            path = (registry.parent / row["replacement_path"]).resolve()
            if sha(path) != row["sha256"]:
                raise ValueError(f"Changed GLDAS replacement: {path}")
            self.repairs[pd.Timestamp(row["utc"])] = path

    def soil_path(self, time):
        return self.repairs.get(time, self.paths["gldas"] / f"GLDAS_NOAH025_3H.A{time:%Y%m%d.%H}00.021.nc4.SUB.nc4")

    def annual(self, year):
        return self.paths["annual_covariates"] / f"covariates_{year}.tiff"

    def native(self, year):
        return self.paths["native_covariates"] / f"covariates_{year}.tiff"

    def aqua_files(self):
        return [self.paths["aqua_points"] / f"coord8000-{i}-MYD13Q1-061-results.csv" for i in range(1, 9)]

    def grid(self, year, chunk):
        return self.output / f"work/grid/{year}_{chunk:02d}.parquet"

    def preflight(self):
        sources = [path for path in self.paths.values() if not path.is_dir()]
        sources.extend(self.aqua_files())
        for year in self.years:
            sources.extend([self.paths["era"] / f"ERA5_{year}.nc", self.annual(year), self.native(year),
                            self.paths["sipnet_means"] / f"year={year}.rds"])
        towers = pd.read_csv(self.paths["matched_towers"]).sort_values("min_dist_m").drop_duplicates("index")
        for site in towers.loc[towers.min_dist_m <= 1000, "Site_ID"].unique():
            for target in ("NEE", "LE"):
                sources.append(self.paths["tower"] / f"target={target}/site={site}.parquet")
        times = pd.date_range(self.calendar.start.min(), self.calendar.stop.max(), freq="3h", inclusive="left")
        sources.extend(self.soil_path(t) for t in times)
        package = Path(__file__).resolve().parents[1]
        code = {str(p.relative_to(package)): sha(p) for p in sorted(package.rglob("*"))
                if p.suffix in {".R", ".py"} and "tests" not in p.parts}
        manifest = dict(sources=[identity(p) for p in sources], config=sha(self.config), code=code)
        path = self.output / "inputs.json"
        token = digest(manifest)
        if path.exists():
            if read(path) != dict(token=token, **manifest):
                raise ValueError("Preparation inputs or code changed; choose a new output")
        else:
            if self.output.exists() and any(self.output.iterdir()):
                raise ValueError("Choose an empty preparation output")
            write(path, dict(token=token, **manifest))
        return token

