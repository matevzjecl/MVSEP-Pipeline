from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from audio_analysis import (
    choose_audio,
    classify_backing_vocals,
    classify_lead_guitar,
    maybe_split_stem_lr,
    meaningful_stereo,
    residual_relative_db,
)
from config import MODELS
from file_utils import (
    copy_norm,
    download_best_audio_from_url,
    input_run_name,
    looks_like_url,
    write_json,
)
from mvsep_api import disable_premium_usage, run_mvsep_stage


def resolve_input_audio(
    *,
    input_raw: str,
    run_dir: Path,
    reuse_existing: bool,
) -> tuple[Path, dict[str, Any]]:
    if looks_like_url(input_raw):
        downloaded_audio = download_best_audio_from_url(
            url=input_raw,
            download_dir=run_dir / "input_download",
            reuse_existing=reuse_existing,
        )

        return downloaded_audio, {
            "input_type": "url",
            "source_url": input_raw,
            "downloaded_audio": str(downloaded_audio),
        }

    input_audio = Path(input_raw).expanduser().resolve()
    if not input_audio.exists():
        raise FileNotFoundError(input_audio)

    return input_audio, {
        "input_type": "file",
        "source_file": str(input_audio),
    }


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    api_token = args.api_token or os.environ.get("MVSEP_API_TOKEN")
    if not api_token:
        raise SystemExit("Missing API token. Set MVSEP_API_TOKEN in .env or pass --api-token.")

    input_raw = args.input.strip()
    root = Path(args.out).expanduser().resolve()
    run_dir = root / input_run_name(input_raw)

    final_dir = run_dir / "00_final"
    primary_dir = run_dir / "01_bs_roformer_sw"
    guitar_dir = run_dir / "02_guitar_lead_rhythm"
    vocals_dir = run_dir / "03_vocals_karaoke"
    satb_dir = run_dir / "04_satb"

    final_dir.mkdir(parents=True, exist_ok=True)

    input_audio, input_report = resolve_input_audio(
        input_raw=input_raw,
        run_dir=run_dir,
        reuse_existing=args.reuse_existing,
    )

    report: dict[str, Any] = {
        "input": input_raw,
        **input_report,
        "run_dir": str(run_dir),
        "primary_model": "BS Roformer SW",
        "primary_sep_type": MODELS["primary_stems"]["sep_type"],
        "stages": {},
        "final_files": {},
        "warnings": [],
    }

    if not args.allow_premium:
        disable_payload = disable_premium_usage(api_token)
        report["premium"] = {
            "allowed": False,
            "disable_response": disable_payload,
        }
    else:
        print("MVSEP premium usage was not disabled because --allow-premium was passed.")
        report["premium"] = {"allowed": True}

    primary_files = run_mvsep_stage(
        stage_name="bs_roformer_sw",
        input_audio=input_audio,
        api_token=api_token,
        stage_dir=primary_dir,
        model=MODELS["primary_stems"],
        poll_seconds=args.poll_seconds,
        reuse_existing=args.reuse_existing,
    )
    report["stages"]["bs_roformer_sw"] = [str(p) for p in primary_files]

    drums = choose_audio(primary_dir, include_any=("drum", "drums"))
    bass = choose_audio(primary_dir, include_all=("bass",), exclude_any=("double",))
    guitar = choose_audio(primary_dir, include_all=("guitar",), exclude_any=("lead", "rhythm"))
    vocals = choose_audio(
        primary_dir,
        include_any=("vocal", "vocals"),
        exclude_any=("lead", "back", "backing", "choir"),
    )
    other = choose_audio(primary_dir, include_any=("other",))
    piano = choose_audio(primary_dir, include_any=("piano",))

    main_stems = {
        "drums.wav": drums,
        "bass.wav": bass,
        "guitar.wav": guitar,
        "vocals.wav": vocals,
        "other.wav": other,
        "piano.wav": piano,
    }

    for dest_name, src in main_stems.items():
        copied = copy_norm(src, final_dir / dest_name)
        if copied:
            report["final_files"][dest_name] = str(copied)

    if guitar is None:
        report["warnings"].append("Could not find guitar stem from BS Roformer SW.")
    if vocals is None:
        report["warnings"].append("Could not find vocals stem from BS Roformer SW.")

    lead_guitar, rhythm_guitar = run_guitar_stage(
        guitar=guitar,
        final_dir=final_dir,
        guitar_dir=guitar_dir,
        api_token=api_token,
        poll_seconds=args.poll_seconds,
        reuse_existing=args.reuse_existing,
        always_split_lr=args.always_split_lr,
        report=report,
    )

    run_vocal_stage(
        vocals=vocals,
        final_dir=final_dir,
        vocals_dir=vocals_dir,
        api_token=api_token,
        poll_seconds=args.poll_seconds,
        reuse_existing=args.reuse_existing,
        report=report,
    )

    if args.satb and (final_dir / "vocals.wav").exists():
        run_satb_stage(
            final_vocals=final_dir / "vocals.wav",
            final_dir=final_dir,
            satb_dir=satb_dir,
            api_token=api_token,
            poll_seconds=args.poll_seconds,
            reuse_existing=args.reuse_existing,
            report=report,
        )

    write_json(run_dir / "report.json", report)
    write_json(final_dir / "report.json", report)

    print("\nDone.")
    print(f"Final stems: {final_dir}")
    print(f"Report:      {run_dir / 'report.json'}")

    return report


def run_guitar_stage(
    *,
    guitar: Path | None,
    final_dir: Path,
    guitar_dir: Path,
    api_token: str,
    poll_seconds: int,
    reuse_existing: bool,
    always_split_lr: bool,
    report: dict[str, Any],
) -> tuple[Path | None, Path | None]:
    final_guitar = final_dir / "guitar.wav"
    if guitar is None or not final_guitar.exists():
        return None, None

    guitar_files = run_mvsep_stage(
        stage_name="lead_rhythm_guitar",
        input_audio=final_guitar,
        api_token=api_token,
        stage_dir=guitar_dir,
        model=MODELS["lead_rhythm_guitar"],
        poll_seconds=poll_seconds,
        reuse_existing=reuse_existing,
    )
    report["stages"]["lead_rhythm_guitar"] = [str(p) for p in guitar_files]

    lead_guitar = choose_audio(guitar_dir, include_all=("lead",), include_any=("guitar", "lead"))
    rhythm_guitar = choose_audio(guitar_dir, include_all=("rhythm",), include_any=("guitar", "rhythm"))

    lead_guitar = copy_final_stem(lead_guitar, final_dir, "lead_guitar.wav", report)
    rhythm_guitar = copy_final_stem(rhythm_guitar, final_dir, "rhythm_guitar.wav", report)

    if lead_guitar is not None:
        report["lead_guitar_check"] = classify_lead_guitar(lead_guitar, final_guitar)

    report["guitar_stereo"] = meaningful_stereo(final_guitar)
    report["rhythm_guitar_lr"] = maybe_split_stem_lr(
        stem_path=rhythm_guitar,
        final_dir=final_dir,
        base_name="rhythm_guitar",
        always_split_lr=always_split_lr,
    )

    lead_status = report.get("lead_guitar_check", {}).get("status")
    if lead_guitar is not None and (always_split_lr or lead_status in {"good", "maybe"}):
        report["lead_guitar_lr"] = maybe_split_stem_lr(
            stem_path=lead_guitar,
            final_dir=final_dir,
            base_name="lead_guitar",
            always_split_lr=always_split_lr,
        )

    return lead_guitar, rhythm_guitar


def run_vocal_stage(
    *,
    vocals: Path | None,
    final_dir: Path,
    vocals_dir: Path,
    api_token: str,
    poll_seconds: int,
    reuse_existing: bool,
    report: dict[str, Any],
) -> tuple[Path | None, Path | None]:
    final_vocals = final_dir / "vocals.wav"
    if vocals is None or not final_vocals.exists():
        return None, None

    vocals_files = run_mvsep_stage(
        stage_name="karaoke_lead_back_vocals",
        input_audio=final_vocals,
        api_token=api_token,
        stage_dir=vocals_dir,
        model=MODELS["karaoke"],
        poll_seconds=poll_seconds,
        reuse_existing=reuse_existing,
    )
    report["stages"]["karaoke_lead_back_vocals"] = [str(p) for p in vocals_files]

    lead_vocals = choose_audio(vocals_dir, include_all=("lead",), include_any=("vocal", "vocals", "lead"))
    backing_vocals = choose_audio(
        vocals_dir,
        include_any=("back", "backing"),
        exclude_any=("instrumental", "instrum"),
    )

    lead_vocals = copy_final_stem(lead_vocals, final_dir, "lead_vocals.wav", report)
    backing_vocals = copy_final_stem(backing_vocals, final_dir, "backing_vocals.wav", report)

    if backing_vocals is not None:
        report["backing_vocals_check"] = classify_backing_vocals(
            backing_path=backing_vocals,
            vocals_path=final_vocals,
            lead_path=lead_vocals,
        )

    if lead_vocals is not None and backing_vocals is not None:
        residual_db = residual_relative_db(final_vocals, lead_vocals, backing_vocals)
        report["vocal_split_reconstruction"] = {
            "residual_relative_db_to_vocals": residual_db,
            "warning": (
                "high_residual_split_may_be_weak"
                if residual_db is not None and residual_db > -20.0
                else None
            ),
        }

    return lead_vocals, backing_vocals


def run_satb_stage(
    *,
    final_vocals: Path,
    final_dir: Path,
    satb_dir: Path,
    api_token: str,
    poll_seconds: int,
    reuse_existing: bool,
    report: dict[str, Any],
) -> None:
    satb_files = run_mvsep_stage(
        stage_name="satb",
        input_audio=final_vocals,
        api_token=api_token,
        stage_dir=satb_dir,
        model=MODELS["satb"],
        poll_seconds=poll_seconds,
        reuse_existing=reuse_existing,
    )
    report["stages"]["satb"] = [str(p) for p in satb_files]

    for label in ("soprano", "alto", "tenor", "bass"):
        copy_final_stem(
            choose_audio(satb_dir, include_any=(label,)),
            final_dir,
            f"{label}.wav",
            report,
        )


def copy_final_stem(
    src: Path | None,
    final_dir: Path,
    dest_name: str,
    report: dict[str, Any],
) -> Path | None:
    copied = copy_norm(src, final_dir / dest_name)
    if copied:
        report["final_files"][dest_name] = str(copied)
    return copied
