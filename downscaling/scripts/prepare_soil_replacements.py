"""Register the three replacement GLDAS files from the original NASA downloads."""

import argparse
from pathlib import Path
import shutil
import h5py
import numpy as np
import pandas as pd

from downscaling.artifacts import sha, write
from downscaling.prepare.sources import GLDAS_FIELDS


REPLACEMENTS = {
    "2019-04-04T03:00:00Z": "9a01303cc341f1a8324a1e4f6660f50150fe58e2e5b06f5fba09c78762f67fa2",
    "2019-04-04T18:00:00Z": "8a457bc1efe1698416dcf671ea47ba9a8d7a3add12beb087cc9cbc9404367878",
    "2024-06-05T00:00:00Z": "a372bead3ac62b04c70b0241c9f9812aee83966ce699d33e730ceaf2cebabb46",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--downloads", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    for utc, expected in REPLACEMENTS.items():
        time = pd.Timestamp(utc)
        name = f"GLDAS_NOAH025_3H.A{time:%Y%m%d.%H}00.021.nc4"
        source = args.downloads / name
        if sha(source) != expected:
            raise ValueError(f"Unexpected NASA file: {source}")
        with h5py.File(source) as f:
            clock = f["time"]
            origin = pd.Timestamp(
                clock.attrs["units"].decode().split(" since ")[1], tz="UTC"
            )
            if origin + pd.Timedelta(minutes=float(clock[0])) != time:
                raise ValueError(f"Wrong valid time: {source}")
            for key in GLDAS_FIELDS:
                values = f[key][:]
                if (
                    values.shape != (1, len(f["lat"]), len(f["lon"]))
                    or not np.isfinite(values).all()
                ):
                    raise ValueError(f"Invalid {key}: {source}")
        rows.append(
            dict(
                utc=utc,
                replacement_path=name,
                sha256=expected,
                source_url=f"https://data.gesdisc.earthdata.nasa.gov/data/GLDAS/GLDAS_NOAH025_3H.2.1/{time:%Y/%j}/{name}",
            )
        )
    args.output.mkdir(parents=True, exist_ok=False)
    for row in rows:
        name = row["replacement_path"]
        shutil.copyfile(args.downloads / name, args.output / name)
    write(
        args.output / "registry.json",
        dict(schema="gldas-replacements-1", replacements=rows),
    )


if __name__ == "__main__":
    main()
