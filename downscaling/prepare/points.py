"""Point covariates for native training rows and their lagged period panels."""

import os
import h5py
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from downscaling.artifacts import require, receipt, valid
from downscaling.contracts import SOIL, aggregate_period, short_gaps, unique
from downscaling.prepare.features import FeatureStream, add_previous
from downscaling.prepare.sources import GLDAS_FIELDS, vi_calendar
from downscaling.prepare.vegetation import Vegetation


def save_frame(frame, path, token):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial.{os.getpid()}")
    frame.to_parquet(partial, index=False)
    partial.replace(path)
    receipt(path, token)


def extract_aqua(config, token):
    output = config.output / "work/aqua.parquet"
    if valid(output, token):
        return
    prefix = "MYD13Q1_061__250m_16_days_"
    columns = {
        "Latitude": "lat",
        "Longitude": "lon",
        "Date": "date",
        prefix + "EVI": "EVI",
        prefix + "NDVI": "NDVI",
    }
    lookup = cKDTree(config.sites[["lat", "lon"]])
    parts = []
    for path in config.aqua_files():
        frame = pd.read_csv(path, usecols=list(columns)).rename(columns=columns)
        coords = frame[["lat", "lon"]].drop_duplicates()
        distance, indices = lookup.query(coords)
        selected = distance <= 1e-8
        coords = coords.loc[selected].copy()
        coords["index"] = config.sites["index"].to_numpy()[indices[selected]]
        frame = frame.merge(coords, on=["lat", "lon"], validate="many_to_one")
        frame.date = pd.to_datetime(frame.date, utc=True)
        for name in ("EVI", "NDVI"):
            frame[name] = pd.to_numeric(frame[name], errors="coerce").where(
                lambda s: s.between(-0.2, 1)
            )
        parts.append(frame[["index", "date", "EVI", "NDVI"]])
    frame = pd.concat(parts, ignore_index=True)
    unique(frame, ["index", "date"])
    if set(frame["index"]) != set(config.sites["index"]):
        raise ValueError("Incomplete Aqua site inventory")
    save_frame(frame.sort_values(["index", "date"]), output, token)


class PointVegetation(Vegetation):
    def __init__(self, config, points, token):
        path = config.output / "work/aqua.parquet"
        require(path, token)
        ids = points["index"]
        frame = pd.read_parquet(path, filters=[("index", "in", ids.tolist())])
        self.dates = vi_calendar(
            config.calendar.start.min() - pd.Timedelta(days=64),
            config.calendar.stop.max() + pd.Timedelta(days=64),
        )
        sensor = np.repeat("Aqua", len(self.dates))
        self.values = {}
        self.filled = {}
        for name in ("NDVI", "EVI"):
            values = (
                frame.pivot(index="date", columns="index", values=name)
                .reindex(index=self.dates, columns=ids)
                .to_numpy(dtype=float, copy=True)
            )
            flags = np.zeros_like(values, dtype=np.uint8)
            for i in range(len(ids)):
                values[:, i], flags[:, i] = short_gaps(values[:, i], self.dates, sensor)
            self.values[name] = values
            self.filled[name] = flags


class PointSoil:
    def __init__(self, config, points):
        self.config = config
        self.points = points

    def at(self, time):
        path = self.config.soil_path(time)
        with h5py.File(path) as source:
            clock = source["time"]
            start = pd.Timestamp(
                clock.attrs["units"].decode().split(" since ")[1], tz="UTC"
            )
            if start + pd.Timedelta(minutes=float(clock[0])) != time:
                raise ValueError(f"GLDAS timestamp mismatch: {path}")
            lat, lon = source["lat"][:], source["lon"][:]
            rr = np.abs(lat[:, None] - self.points.lat.to_numpy()).argmin(axis=0)
            cc = np.abs(lon[:, None] - self.points.lon.to_numpy()).argmin(axis=0)
            inside = (
                self.points.lat.between(lat.min() - 0.125, lat.max() + 0.125)
                & self.points.lon.between(lon.min() - 0.125, lon.max() + 0.125)
            ).to_numpy()
            values = np.empty((len(rr), 4))
            for i, name in enumerate(GLDAS_FIELDS):
                field = source[name]
                if field.attrs["units"].decode() != "kg m-2":
                    raise ValueError(f"Unexpected soil units: {path}")
                block = field[0, rr.min() : rr.max() + 1, cc.min() : cc.max() + 1]
                v = block[rr - rr.min(), cc - cc.min()].astype(float)
                v[(v == np.asarray(field.attrs["_FillValue"]).item()) | ~inside] = (
                    np.nan
                )
                values[:, i] = v
            return values, lat[rr], lon[cc]


def stream(config, chunk, token):
    points = config.sites[(config.sites["index"] - 1) // 500 + 1 == chunk].sort_values(
        "index"
    )
    return FeatureStream(
        points,
        PointVegetation(config, points, token),
        PointSoil(config, points),
        config,
    )


def native_features(config, token, year, chunk):
    path = config.grid(year, chunk)
    if valid(path, token):
        return
    start = max(config.periods.start.min(), pd.Timestamp(f"{year}-01-01", tz="UTC"))
    stop = min(config.periods.stop.max(), pd.Timestamp(f"{year+1}-01-01", tz="UTC"))
    features = stream(config, chunk, token)
    try:
        frames = [
            features.at(t)
            for t in pd.date_range(start, stop, freq="3h", inclusive="left")
        ]
        save_frame(pd.concat(frames).sort_values(["index", "utc"]), path, token)
    finally:
        features.close()


def proxy_features(config, token, chunk):
    path = config.output / f"work/proxy/{chunk:02d}.parquet"
    if valid(path, token):
        return
    features = stream(config, chunk, token)
    history, result = {}, []
    try:
        for row in config.calendar.itertuples():
            if row.period_id < 1:
                panel = features.period(row)
            else:
                frames = []
                for year in range(
                    row.start.year, (row.stop - pd.Timedelta(seconds=1)).year + 1
                ):
                    grid = config.grid(year, chunk)
                    require(grid, token)
                    frames.append(
                        pd.read_parquet(
                            grid,
                            filters=[("utc", ">=", row.start), ("utc", "<", row.stop)],
                            columns=[
                                "index",
                                "utc",
                                "t2m",
                                "d2m",
                                "tp",
                                "ssrd",
                                "u10",
                                "v10",
                                "EVI",
                                "NDVI",
                                *SOIL,
                            ],
                        )
                    )
                panel = aggregate_period(pd.concat(frames), row.start, row.stop)
                panel = features.complete_period(panel, row)
                result.append(add_previous(panel, history))
            history[row.period_id] = panel
            history.pop(row.period_id - 4, None)
        save_frame(pd.concat(result), path, token)
    finally:
        features.close()
