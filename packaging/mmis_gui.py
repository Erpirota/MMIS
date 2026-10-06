"""Launcher for the MMIS Desktop GUI executable."""

import argparse
import sys

from mmis.ui.app import launch_gui


def main() -> None:
    parser = argparse.ArgumentParser(description="Minecraft Mod Intelligence Studio")
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        help="Path to custom MMIS data directory",
    )
    args, _ = parser.parse_known_args()
    sys.exit(launch_gui(data_dir=args.data_dir))


if __name__ == "__main__":
    main()
