from types import SimpleNamespace

import h5py
import numpy as np
import pandas as pd

from downscaling.contracts import NATIVE
from downscaling.prepare.products import finalize, prepare_shard
from downscaling.tests.test_training_data import make_dataset


def test_member_support_and_training_labels(tmp_path):
    reference = make_dataset(tmp_path / "fixture")
    root = tmp_path / "prepared_data"
    (root / "panel").mkdir(parents=True)
    for path in [*reference.glob("*.csv"), *reference.glob("panel/*.npy")]:
        (root / path.relative_to(reference)).write_bytes(path.read_bytes())
    periods = pd.read_csv(root / "periods.csv")
    periods["start"] = pd.to_datetime(periods.start_utc, utc=True)
    periods["stop"] = pd.to_datetime(periods.end_date, utc=True) + pd.Timedelta(days=1)
    periods["full_window_fraction"] = periods.expected_3hour_steps / 128
    sites = pd.read_csv(root / "folds.csv")
    grid_file = tmp_path / "grid.parquet"
    config = SimpleNamespace(output=tmp_path, periods=periods, sites=sites, years=[2020],
                             chunks=[1], partitions=[(2020,1)], grid=lambda y,c: grid_file)
    times = pd.date_range(periods.start.iloc[0],periods.stop.iloc[-1],freq="3h",inclusive="left")
    grid = pd.MultiIndex.from_product([sites["index"],times],names=["index","utc"]).to_frame(index=False)
    for field in NATIVE:
        grid[field] = 1.
    grid["LC"],grid["KGC"] = 1,"ET"
    grid.to_parquet(grid_file)
    folder = tmp_path / "work/members/2020_01"
    folder.mkdir(parents=True)
    for t,target in enumerate(["NEE","LE"]):
        frame = grid[["index","utc"]].copy()
        frame["raw"] = 6.+t
        for member in range(1,26):
            frame[f"ens{member:02d}"] = float(member+t)
            frame[f"route{member:02d}"] = np.where(frame.utc.dt.hour<12,1,2)
        frame.loc[:15,"ens01"] = np.nan
        frame.loc[:15,"route01"] = 0
        frame.loc[16,"ens02"] = np.nan
        frame.loc[16,"route02"] = 0
        frame.to_parquet(folder / f"{target}_0000001.parquet")
    prepare_shard(config,"test",2020,1)
    finalize(config,"test")
    for t,target in enumerate(["nee","le"]):
        with h5py.File(tmp_path / f"targets/{target}.h5") as f:
            assert np.isnan(f["values"][0,0,0])
            assert f["support_counts"][0,0,0] == f["route_codes"][0,0,0] == 0
            assert f["support_counts"][1,1,0] == 7
            assert f["route_codes"][0,1,0] == 1
            assert f["values"][1,1,0] == 2+t
    np.testing.assert_equal(np.load(root/"native/2020_01.y.npy")[0],[13.,14.])
    assert len(np.load(root/"native/2020_01.y.npy")) == len(grid)-17
    np.testing.assert_equal(np.load(root/"panel/target_mean.npy")[0],[6.,7.])

