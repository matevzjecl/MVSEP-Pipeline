from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import imageio_ffmpeg
from yt_dlp import YoutubeDL

from config import AUDIO_EXTS


def safe_filename(name: str) -> str:
    name = unquote(name)
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1F]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] or "file"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for i in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}_{i:03d}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not create unique filename for {path}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def audio_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []

    return sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )


def copy_norm(src: Path | None, dest: Path) -> Path | None:
    if src is None:
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def input_run_name(input_value: str) -> str:
    if not looks_like_url(input_value):
        return safe_filename(Path(input_value).stem)

    parsed = urlparse(input_value)

    if "youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc:
        if "youtu.be" in parsed.netloc:
            video_id = parsed.path.strip("/")
        else:
            video_id = parse_qs(parsed.query).get("v", ["youtube_url"])[0]

        return safe_filename(f"youtube_{video_id}")

    netloc = parsed.netloc.replace("www.", "")
    path_name = Path(parsed.path).stem

    if path_name:
        return safe_filename(f"{netloc}_{path_name}")

    return safe_filename(netloc or "url_input")


def download_best_audio_from_url(
    *,
    url: str,
    download_dir: Path,
    reuse_existing: bool,
) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)

    existing = audio_files(download_dir)
    if reuse_existing and existing:
        existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"\nReusing existing downloaded audio: {existing[0]}")
        return existing[0]

    # Use the FFmpeg executable bundled with imageio-ffmpeg.
    # No system-wide installation or PATH modification is required.
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    before = {p.resolve() for p in audio_files(download_dir)}

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(download_dir / "%(title).180s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "ffmpeg_location": ffmpeg_path,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "0",
            }
        ],
    }

    print(f"\nDownloading best audio from URL:\n{url}")

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        if isinstance(info, dict) and "entries" in info and info["entries"]:
            info = info["entries"][0]

        prepared = Path(ydl.prepare_filename(info))
        expected_wav = prepared.with_suffix(".wav")

    if expected_wav.exists():
        print(f"Extracted audio: {expected_wav}")
        return expected_wav.resolve()

    new_files = list({p.resolve() for p in audio_files(download_dir)} - before)
    if new_files:
        new_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"Extracted audio: {new_files[0]}")
        return new_files[0]

    candidates = audio_files(download_dir)
    if candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"Extracted audio: {candidates[0]}")
        return candidates[0]

    raise RuntimeError("yt-dlp finished, but no extracted audio file was found.")
