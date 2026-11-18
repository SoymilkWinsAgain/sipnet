"""Prepare training arrays and ensemble flux targets."""

import argparse
from downscaling.prepare.configuration import Preparation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "run"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = Preparation(args.config)
    if args.command == "run" and (config.output / "jobs.json").exists() and not args.resume:
        parser.error("Use run --resume for an existing submission")
    token = config.preflight()
    print(f"Verified preparation inputs: {token}", flush=True)
    if args.command == "run":
        from downscaling.prepare.cluster import submit
        submit(config, token, args.resume)


if __name__ == "__main__":
    main()
