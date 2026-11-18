"""Fit the shared 500-site sample and predict its period on a small map window."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

import h5py
import numpy as np
import rasterio
import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sample", type=Path, help="Directory with prepared_data/ and targets/"
    )
    parser.add_argument(
        "output", type=Path, help="New run directory outside the checkout"
    )
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()

    prepared = args.sample.resolve() / "prepared_data"
    provenance = json.loads((prepared / "manifest.json").read_text())["provenance"]
    sites = np.load(prepared / "panel/site_ids.npy")
    for target in ("nee", "le"):
        with h5py.File(args.sample / "targets" / f"{target}.h5") as f:
            assert f.attrs["target"] == target.upper()
            assert f.attrs["manifest_id"] == provenance["preparation"]
            np.testing.assert_array_equal(f["site_ids"].asstr()[:], sites.astype(str))
            np.testing.assert_array_equal(f["period_keys"].asstr()[:], ["2012-07-19"])
            np.testing.assert_array_equal(
                f["ensemble_ids"].asstr()[:], [f"ens{i:02d}" for i in range(1, 26)]
            )
            values = f["values"][:]
            assert values.shape == (1, 25, 500) and np.isfinite(values).all()
            assert (f["support_counts"][:] == 96).all()
            assert np.isin(f["route_codes"][:], [1, 2, 3]).all()

    output = args.output.resolve()
    output.mkdir(parents=True)
    train = dict(
        prepared_data=str(prepared),
        output="training",
        resources=dict(device=args.device, cpu_threads=1),
    )
    predict = yaml.safe_load(Path("downscaling/config/predict-scc.yaml").read_text())
    predict["model_bundle"] = "training/model_bundle"
    del predict["sources"]["spatial_cache"]
    predict["request"]["period_ids"] = [1]
    predict["resources"].update(device=args.device, workers=1)

    for stage, config in [("train", train), ("predict", predict)]:
        path = output / f"{stage}.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        cli = [sys.executable, "-m", f"downscaling.{stage}"]
        for command in ("preflight", "run", "report"):
            subprocess.run([*cli, command, "--config", str(path)], check=True)

    maps = output / predict["output"]
    model = json.loads((output / "training/COMPLETE.json").read_text())["model_id"]
    assert json.loads((maps / "manifest.json").read_text())["model_id"] == model
    for target in ("NEE", "LE"):
        with rasterio.open(maps / "products/period" / f"{target}_001.tif") as src:
            assert src.shape == (16, 16) and src.count == 7
            assert (src.read(6) == 1).all() and np.isfinite(src.read(1)).all()
    print(f"Verified both member targets and both maps from model {model}.")


if __name__ == "__main__":
    main()
