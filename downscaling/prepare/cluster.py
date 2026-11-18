"""SGE tasks for site preparation."""

import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree
import pandas as pd

from downscaling.artifacts import identity, read, sha, write
from downscaling.prepare.configuration import Preparation
from downscaling.prepare.points import extract_aqua, native_features, proxy_features
from downscaling.prepare.products import (
    batch_paths,
    finalize,
    prepare_shard,
    write_panel,
)
from downscaling.train.data import NATIVE_ARRAYS, PANEL_ARRAYS

# Slots, GB per slot, hours, simultaneous tasks, and upstream stages.
STAGES = {
    "aqua": (1, 6, 1, 1, ()),
    "features": (4, 6, 12, 8, ("aqua",)),
    "training": (8, 4, 8, 1, ("features",)),
    "fit": (8, 4, 12, 4, ("training",)),
    "proxy": (4, 4, 24, 4, ("features",)),
    "panel": (4, 4, 2, 1, ("proxy",)),
    "predict": (8, 3, 4, 32, ("fit",)),
    "shard": (4, 6, 4, 8, ("predict", "panel")),
    "finalize": (8, 3, 8, 1, ("shard",)),
}


def tasks(config, stage):
    if stage in ("features", "predict", "shard"):
        return len(config.partitions)
    if stage == "proxy":
        return len(config.chunks)
    if stage == "fit":
        return 50
    return 1


def products(config, stage, task):
    work = config.output / "work"
    prepared = config.output / "prepared_data"
    if stage in ("features", "predict", "shard"):
        year, chunk = config.partitions[task - 1]
        name = f"{year}_{chunk:02d}"
        if stage == "features":
            return [config.grid(year, chunk)]
        if stage == "predict":
            return [p for pair in batch_paths(config, year, chunk) for p in pair]
        return [
            work / f"sums/{name}.npz",
            prepared / f"native/{name}.json",
            *[prepared / f"native/{name}.{key}.npy" for key in NATIVE_ARRAYS],
        ]
    if stage == "aqua":
        return [work / "aqua.parquet"]
    if stage == "proxy":
        return [work / f"proxy/{config.chunks[task-1]:02d}.parquet"]
    if stage == "training":
        matches = pd.read_csv(config.paths["matched_towers"])
        matched = matches.loc[
            (matches.min_dist_m <= 1000) & matches["index"].isin(config.sites["index"]),
            "index",
        ]
        chunks = set((matched - 1) // 500 + 1)
        return [
            work / "levels.rds",
            *[
                work / f"training/{target}/{year}_{chunk:02d}.parquet"
                for year, chunk in config.partitions
                if chunk in chunks
                for target in ("NEE", "LE")
            ],
        ]
    if stage == "fit":
        target = "NEE" if task <= 25 else "LE"
        return [work / f"models/{target}_ens{(task-1)%25+1:02d}.rds"]
    if stage == "panel":
        return [
            prepared / "folds.csv",
            prepared / "periods.csv",
            *[prepared / f"panel/{k}.npy" for k in PANEL_ARRAYS if k != "target_mean"],
        ]
    return [
        prepared / "manifest.json",
        prepared / "panel/target_mean.npy",
        config.output / "targets/nee.h5",
        config.output / "targets/le.h5",
    ]


def checkpoint(config, stage, task):
    return config.output / f"work/completed/{stage}/{task}.json"


def complete(config, token, stage, task):
    path = checkpoint(config, stage, task)
    if not path.exists():
        return False
    record = read(path)
    if record["token"] != token:
        raise ValueError(f"Stale task: {path}")
    files = products(config, stage, task)
    return all(p.is_file() for p in files) and record["files"] == [
        identity(p) for p in files
    ]


def worker(config, token, stage, task):
    manifest = read(config.output / "inputs.json")
    if manifest["token"] != token or sha(config.config) != manifest["config"]:
        raise ValueError("Queued request changed")
    package = Path(__file__).resolve().parents[1]
    for name, expected in manifest["code"].items():
        if sha(package / name) != expected:
            raise ValueError(f"Queued source code changed: {name}")
    if complete(config, token, stage, task):
        return
    for parent in STAGES[stage][-1]:
        predecessors = (
            [task]
            if parent == "predict" and stage == "shard"
            else range(1, tasks(config, parent) + 1)
        )
        for i in predecessors:
            if not complete(config, token, parent, i):
                raise ValueError(f"Missing upstream output: {parent} {i}")
    if stage == "aqua":
        extract_aqua(config, token)
    elif stage == "features":
        native_features(config, token, *config.partitions[task - 1])
    elif stage == "proxy":
        proxy_features(config, token, config.chunks[task - 1])
    elif stage in ("training", "fit", "predict"):
        args = []
        if stage == "fit":
            args = ["NEE" if task <= 25 else "LE", f"ens{(task-1)%25+1:02d}"]
        if stage == "predict":
            args = [str(task)]
        subprocess.run(
            [
                "Rscript",
                "--vanilla",
                str(Path(__file__).with_name("yang.R")),
                str(config.output / "work/yang.json"),
                stage,
                *args,
            ],
            check=True,
        )
    elif stage == "panel":
        write_panel(config, token)
    elif stage == "shard":
        prepare_shard(config, token, *config.partitions[task - 1])
    else:
        if config.preflight() != token:
            raise ValueError("Preparation inputs changed")
        finalize(config, token)
    files = products(config, stage, task)
    write(
        checkpoint(config, stage, task),
        dict(token=token, files=[identity(p) for p in files]),
    )


def submit(config, token, resume):
    log = config.output / "logs"
    log.mkdir(exist_ok=True)
    (config.output / "tmp").mkdir(exist_ok=True)
    with (config.output / "submission.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _submit(config, token, resume, log)


def _submit(config, token, resume, log):
    journal = config.output / "jobs.json"
    jobs = read(journal) if journal.exists() else []
    if jobs and not resume:
        raise ValueError("Use run --resume for an existing submission")
    current = ElementTree.fromstring(
        subprocess.check_output(["qstat", "-xml", "-u", os.environ["USER"]])
    )
    live_names = {
        node.findtext("JB_name"): node.findtext("JB_job_number")
        for node in current.iter("job_list")
    }
    work = config.output / "work"
    write(
        work / "yang.json",
        dict(
            work=work,
            paths=config.paths,
            partitions=[
                dict(year=y, chunk=c, name=f"{y}_{c:02d}", path=config.grid(y, c))
                for y, c in config.partitions
            ],
        ),
    )
    submitted = {}
    for stage, (slots, mem, hours, width, depends) in STAGES.items():
        submitted[stage] = []
        pending = [
            i
            for i in range(1, tasks(config, stage) + 1)
            if not complete(config, token, stage, i)
        ]
        previous = [job for dep in depends for job in submitted[dep]]
        # Retain fixed wave boundaries across interrupted submissions.
        for start in range(1, tasks(config, stage) + 1, width):
            end = min(start + width - 1, tasks(config, stage))
            missing = [i for i in pending if start <= i <= end]
            if not missing:
                continue
            name = f"ds_{token[:8]}_{stage}_{start}"
            if name in live_names:
                job = live_names[name]
            else:
                # A wave may contain completed tasks on resume. Each worker checks
                # its receipt before computing, including singleton tasks.
                args = [
                    "qsub",
                    "-terse",
                    "-cwd",
                    "-N",
                    name,
                    "-j",
                    "y",
                    "-o",
                    str(log),
                    "-pe",
                    "omp",
                    str(slots),
                    "-l",
                    f"mem_per_core={mem}G,h_rt={hours:02d}:00:00",
                    "-t",
                    f"{start}-{end}",
                    "-tc",
                    str(width),
                ]
                project = os.environ.get("PREP_SGE_PROJECT")
                if project:
                    args += ["-P", project]
                if previous:
                    args += ["-hold_jid", ",".join(previous)]
                args += [
                    str(Path(__file__).with_name("worker.sh")),
                    sys.executable,
                    str(Path(__file__).resolve().parents[2]),
                    str(config.config),
                    token,
                    stage,
                    str(config.output / "tmp"),
                ]
                result = subprocess.check_output(args, text=True).strip()
                job = result.split(".")[0]
                if not job.isdigit():
                    raise ValueError(f"Unexpected qsub response: {result}")
                jobs.append(
                    dict(job=job, name=name, stage=stage, first=start, last=end)
                )
                write(journal, jobs)
                print(f"{stage} {start}-{end}: job {job}", flush=True)
            submitted[stage].append(job)
            previous = [job]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("token")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("task", type=int)
    args = parser.parse_args()
    worker(Preparation(args.config), args.token, args.stage, args.task)
