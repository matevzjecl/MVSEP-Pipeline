from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "MVSEP pipeline: URL/local input -> BS Roformer SW -> Lead/Rhythm Guitar "
            "-> Karaoke lead/back -> local checks"
        )
    )

    parser.add_argument(
        "input",
        help='Input audio file or URL, for example "song.wav" or "https://www.youtube.com/watch?v=..."',
    )
    parser.add_argument(
        "--out",
        default="mvsep_pipeline_output",
        help="Output root folder",
    )
    parser.add_argument(
        "--api-token",
        default=None,
        help="MVSEP API token. Prefer env var MVSEP_API_TOKEN in .env.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="How often to poll MVSEP job status",
    )
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Reuse existing downloaded/stage files instead of creating new jobs",
    )
    parser.add_argument(
        "--allow-premium",
        action="store_true",
        help="Do not disable MVSEP premium usage before creating jobs",
    )
    parser.add_argument(
        "--always-split-lr",
        action="store_true",
        help="Always export L/R for stereo guitar stems, even if stereo width looks weak",
    )
    parser.add_argument(
        "--satb",
        action="store_true",
        help="Also run optional SATB choir model on vocals.wav",
    )

    return parser.parse_args()


def main() -> None:
    load_dotenv()
    run_pipeline(parse_args())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
