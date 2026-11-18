"""Prepared training arrays and member-wise period products."""

import h5py
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from downscaling.artifacts import receipt, require, save_npz, write
from downscaling.contracts import KGC, NATIVE, NUMERIC, TARGETS, predictors
from downscaling.train.data import period_position, publish, NATIVE_ARRAYS
from downscaling.train.splits import declare_nested


def write_panel(config, token):
    root = config.output / "prepared_data"
    panel = root / "panel"
    panel.mkdir(exist_ok=True, parents=True)
    inventory = declare_nested(
        pd.read_csv(config.paths["folds"])
        .sort_values("site_position")
        .reset_index(drop=True)
    )
    folds = config.sites.merge(inventory, on="index", validate="one_to_one", how="left")
    if folds.fold.isna().any():
        raise ValueError("Missing fixed fold membership")
    folds.to_csv(root / "folds.csv", index=False)
    periods = config.periods.drop(columns=["start", "stop"])
    periods.to_csv(root / "periods.csv", index=False)
    ids = pd.Index(folds["index"])
    shape = (len(periods), len(ids))
    numeric = np.empty((*shape, len(NUMERIC)), np.float32)
    lc = np.zeros(shape, np.int64)
    complete = np.zeros(shape, bool)
    for chunk in config.chunks:
        source = config.output / f"work/proxy/{chunk:02d}.parquet"
        require(source, token)
        frame = pd.read_parquet(source).set_index(["period_id", "index"])
        sites = ids[(ids - 1) // 500 + 1 == chunk]
        keys = pd.MultiIndex.from_product(
            [periods.period_id, sites], names=frame.index.names
        )
        if (
            not frame.index.is_unique
            or len(frame) != len(keys)
            or not keys.isin(frame.index).all()
        ):
            raise ValueError(f"Incomplete proxy partition: {source}")
        x, classes, keep = predictors(frame.loc[keys].reset_index())
        cols = ids.get_indexer(sites)
        numeric[:, cols] = x.reshape(len(periods), len(cols), -1)
        lc[:, cols] = np.nan_to_num(classes).reshape(len(periods), -1)
        complete[:, cols] = keep.reshape(len(periods), -1)
    for key, value in dict(
        numeric=numeric.reshape(-1, len(NUMERIC)),
        complete=complete.ravel(),
        lc=lc.ravel(),
        site_ids=ids.to_numpy(),
        years=np.repeat(pd.to_datetime(periods.period_key).dt.year, len(ids)),
    ).items():
        np.save(panel / f"{key}.npy", value)


def batch_paths(config, year, chunk):
    n = pq.ParquetFile(config.grid(year, chunk)).metadata.num_rows
    folder = config.output / f"work/members/{year}_{chunk:02d}"
    return [
        [folder / f"{target}_{i:07d}.parquet" for target in TARGETS]
        for i in range(1, n + 1, 100000)
    ]


def prepare_shard(config, token, year, chunk):
    key = f"{year}_{chunk:02d}"
    root = config.output / "prepared_data"
    dest = root / "native"
    dest.mkdir(parents=True, exist_ok=True)
    sites = pd.Index(config.sites["index"])
    local_sites = sites[(sites - 1) // 500 + 1 == chunk]
    periods = config.periods
    active = (periods.start.dt.year <= year) & (
        (periods.stop - pd.Timedelta(seconds=1)).dt.year >= year
    )
    period_ids = np.flatnonzero(active)
    shape = (len(period_ids), len(local_sites), 2)
    sums = np.zeros((*shape, 25))
    route_counts = np.zeros((*shape, 25, 3), np.uint16)
    raw_sums = np.zeros(shape)
    raw_counts = np.zeros(shape, np.uint16)
    complete = np.load(root / "panel/complete.npy", mmap_mode="r")
    grid = pd.read_parquet(
        config.grid(year, chunk), columns=["index", "utc", *NATIVE, "KGC", "LC"]
    ).set_index(["index", "utc"])
    if not grid.index.is_unique:
        raise ValueError("Duplicate native input keys")
    members = [f"ens{i:02d}" for i in range(1, 26)]
    routes = [f"route{i:02d}" for i in range(1, 26)]
    parts = {k: [] for k in NATIVE_ARRAYS}
    seen = 0
    for pair in batch_paths(config, year, chunk):
        tables = [pd.read_parquet(p).set_index(["index", "utc"]) for p in pair]
        index = tables[0].index
        # The production writer emits both targets in grid order.
        if not index.equals(grid.index[seen : seen + len(index)]) or not index.equals(
            tables[1].index
        ):
            raise ValueError(f"Misaligned grid or target keys in {pair}")
        data = grid.iloc[seen : seen + len(index)]
        seen += len(index)
        ids, times = index.get_level_values("index"), index.get_level_values("utc")
        positions, valid = period_position(times, periods)
        if not valid.all():
            raise ValueError("Three-hour step outside target calendar")
        local_cols = local_sites.get_indexer(ids)
        if (local_cols < 0).any():
            raise ValueError("Unexpected site in partition")
        rows = positions * len(sites) + sites.get_indexer(ids)
        keys = (positions - period_ids[0]) * len(local_sites) + local_cols
        starts = np.r_[0, np.flatnonzero(np.diff(keys)) + 1]
        bins = np.unravel_index(keys[starts], shape[:2])
        y = np.empty((len(data), 2), np.float32)
        fractions = np.empty((len(data), 2, 3), np.float32)
        for t, frame in enumerate(tables):
            values = frame[members].to_numpy(dtype=float)
            route = frame[routes].to_numpy()
            finite = np.isfinite(values)
            if not np.isin(route, [0, 1, 2, 3]).all() or not np.array_equal(
                route != 0, finite
            ):
                raise ValueError("Inconsistent member route and support")
            y[:, t] = values.mean(axis=1)
            raw = frame.raw.to_numpy()
            raw_valid = np.isfinite(raw)
            np.add.at(
                raw_sums[:, :, t],
                bins,
                np.add.reduceat(np.where(raw_valid, raw, 0), starts),
            )
            np.add.at(raw_counts[:, :, t], bins, np.add.reduceat(raw_valid, starts))
            np.add.at(
                sums[:, :, t],
                bins,
                np.add.reduceat(np.where(finite, values, 0), starts, axis=0),
            )
            for j in range(3):
                use = route == j + 1
                fractions[:, t, j] = use.mean(axis=1)
                np.add.at(
                    route_counts[:, :, t, :, j],
                    bins,
                    np.add.reduceat(use, starts, axis=0),
                )
        kg = data.KGC.map({name: i for i, name in enumerate(KGC)}).to_numpy(float)
        if (data.KGC.notna() & ~np.isfinite(kg)).any():
            raise ValueError("Unknown climate class")
        lc = data.LC.to_numpy(float)
        good = (
            complete[rows]
            & np.isfinite(kg)
            & np.isfinite(lc)
            & np.isfinite(y).all(axis=1)
        )
        step = (times - periods.start.iloc[0]).total_seconds().to_numpy() / 10800
        if (step != np.floor(step)).any():
            raise ValueError("Misaligned three-hour step")
        arrays = dict(
            x=np.column_stack([data[NATIVE], kg, lc]).astype(np.float32),
            y=y,
            row=rows.astype(np.int32),
            step=step.astype(np.int32),
            route=fractions,
        )
        for name, value in arrays.items():
            parts[name].append(value[good])
    if seen != len(grid):
        raise ValueError("Missing native target rows")
    for name, values in parts.items():
        np.save(dest / f"{key}.{name}.npy", np.concatenate(values))
    write(dest / f"{key}.json", dict(name=key, rows=sum(map(len, parts["row"]))))
    save_npz(
        config.output / f"work/sums/{key}.npz",
        token,
        sites=local_sites,
        positions=period_ids,
        sums=sums,
        route_counts=route_counts,
        raw_sums=raw_sums,
        raw_counts=raw_counts,
    )


def finalize(config, token):
    from contextlib import ExitStack

    root = config.output / "prepared_data"
    periods = config.periods
    sites = pd.Index(config.sites["index"])
    n, p = len(sites), len(periods)
    target_root = config.output / "targets"
    target_root.mkdir(exist_ok=True)
    raw_mean = np.full((p, n, 2), np.nan, np.float32)
    expected = periods.expected_3hour_steps.to_numpy()
    eligible = np.load(root / "panel/complete.npy").reshape(p, n)
    with ExitStack() as stack:
        files = []
        for target in TARGETS:
            file = stack.enter_context(
                h5py.File(target_root / f"{target.lower()}.partial.h5", "w")
            )
            files.append(file)
            for name, dtype in [
                ("values", "<f4"),
                ("support_counts", "<u2"),
                ("route_codes", "u1"),
            ]:
                file.create_dataset(
                    name,
                    (p, 25, n),
                    dtype=dtype,
                    chunks=(1, 25, min(512, n)),
                    compression="gzip",
                    compression_opts=4,
                )
            for name, labels in dict(
                period_keys=periods.period_key,
                site_ids=sites,
                ensemble_ids=[f"ens{i:02d}" for i in range(1, 26)],
            ).items():
                file[name] = np.asarray(
                    list(map(str, labels)), dtype=h5py.string_dtype()
                )
            file["expected_available_steps"] = expected
            file["full_window_fraction"] = periods.full_window_fraction
            file.attrs.update(
                target=target,
                axis_order="period,ensemble,site",
                manifest_id=token,
                window_convention="UTC [t,t+3h)",
                route_legend="0=missing,1=direct_ec,2=residual_model,3=raw_sipnet",
            )
        for chunk in config.chunks:
            local = sites[(sites - 1) // 500 + 1 == chunk]
            cols = sites.get_indexer(local)
            shape = (p, len(local), 2)
            sums = np.zeros((*shape, 25))
            counts = np.zeros((*shape, 25, 3), np.uint16)
            rs = np.zeros(shape)
            rc = np.zeros(shape, np.uint16)
            for year in config.years:
                path = config.output / f"work/sums/{year}_{chunk:02d}.npz"
                require(path, token)
                with np.load(path) as data:
                    np.testing.assert_array_equal(data["sites"], local)
                    pos = data["positions"]
                    sums[pos] += data["sums"]
                    counts[pos] += data["route_counts"]
                    rs[pos] += data["raw_sums"]
                    rc[pos] += data["raw_counts"]
            if np.any(eligible[:, cols, None] & (rc != expected[:, None, None])):
                raise ValueError("Eligible proxy row has incomplete raw SIPNET support")
            raw_mean[:, cols] = np.divide(
                rs, rc, out=np.full_like(rs, np.nan), where=rc != 0
            )
            support = counts.sum(axis=-1)
            if np.any(support > expected[:, None, None, None]):
                raise ValueError("Member support exceeds period length")
            values = np.divide(
                sums, support, out=np.full_like(sums, np.nan), where=support != 0
            )
            route = np.where(support, 1 + counts.argmax(axis=-1), 0)
            col_index = (
                slice(cols[0], cols[-1] + 1) if np.all(np.diff(cols) == 1) else cols
            )
            for t, file in enumerate(files):
                for name, array in [
                    ("values", values),
                    ("support_counts", support),
                    ("route_codes", route),
                ]:
                    file[name][:, :, col_index] = array[:, :, t].transpose(0, 2, 1)
        for file in files:
            stats = {
                name: []
                for name in ("finite_count", "value_min", "value_max", "value_mean")
            }
            for frame in file["values"]:
                finite = frame[np.isfinite(frame)].astype(float)
                stats["finite_count"].append(len(finite))
                for stat in ("min", "max", "mean"):
                    stats["value_" + stat].append(
                        getattr(finite, stat)() if len(finite) else np.nan
                    )
            for name, values in stats.items():
                file[name] = values
    for target in TARGETS:
        path = target_root / f"{target.lower()}.h5"
        with h5py.File(target_root / f"{target.lower()}.partial.h5") as file:
            if file["values"].shape != (p, 25, n) or file.attrs["target"] != target:
                raise ValueError(f"Incomplete {target} product")
        (target_root / f"{target.lower()}.partial.h5").replace(path)
        receipt(path, token)
    np.save(root / "panel/target_mean.npy", raw_mean.reshape(-1, 2))
    return publish(
        root,
        [f"{y}_{c:02d}" for y, c in config.partitions],
        dict(preparation=token),
        str(periods.start.iloc[0]),
    )
