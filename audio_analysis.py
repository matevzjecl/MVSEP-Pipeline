from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from config import EPS
from file_utils import audio_files


def choose_audio(
    folder: Path,
    *,
    include_all: tuple[str, ...] = (),
    include_any: tuple[str, ...] = (),
    exclude_any: tuple[str, ...] = (),
) -> Path | None:
    candidates: list[Path] = []

    for path in audio_files(folder):
        name = path.name.lower()

        if include_all and not all(term in name for term in include_all):
            continue
        if include_any and not any(term in name for term in include_any):
            continue
        if exclude_any and any(term in name for term in exclude_any):
            continue

        candidates.append(path)

    if not candidates:
        return None

    candidates.sort(key=lambda p: (len(p.name), p.name.lower()))
    return candidates[0]


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path), always_2d=True, dtype="float32")
    return audio, sr


def to_mono(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        return audio
    return audio.mean(axis=1)


def rms_db(audio: np.ndarray) -> float:
    value = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64) + EPS))
    return 20.0 * math.log10(max(value, EPS))


def relative_loudness_db(stem_path: Path, reference_path: Path) -> float:
    stem, _ = load_audio(stem_path)
    ref, _ = load_audio(reference_path)
    return rms_db(stem) - rms_db(ref)


def active_ratio(
    path: Path,
    *,
    frame_seconds: float = 0.05,
    relative_threshold_db: float = -40.0,
    absolute_threshold_db: float = -60.0,
) -> float:
    audio, sr = load_audio(path)
    mono = to_mono(audio)

    frame_size = max(1, int(sr * frame_seconds))
    frame_count = len(mono) // frame_size

    if frame_count <= 0:
        return 0.0

    frames = mono[: frame_count * frame_size].reshape(frame_count, frame_size)
    frame_rms = np.sqrt(np.mean(np.square(frames), axis=1) + EPS)
    frame_db = 20.0 * np.log10(np.maximum(frame_rms, EPS))

    max_db = float(np.max(frame_db))
    threshold = max(max_db + relative_threshold_db, absolute_threshold_db)

    return float(np.mean(frame_db > threshold))


def lr_correlation(path: Path) -> float | None:
    audio, _ = load_audio(path)

    if audio.shape[1] < 2:
        return None

    left = audio[:, 0].astype(np.float64)
    right = audio[:, 1].astype(np.float64)

    left = left - left.mean()
    right = right - right.mean()

    denom = np.sqrt(np.sum(left * left) * np.sum(right * right))
    if denom < EPS:
        return None

    return float(np.sum(left * right) / denom)


def side_mid_db(path: Path) -> float | None:
    audio, _ = load_audio(path)

    if audio.shape[1] < 2:
        return None

    left = audio[:, 0]
    right = audio[:, 1]

    mid = (left + right) * 0.5
    side = (left - right) * 0.5

    return rms_db(side) - rms_db(mid)


def meaningful_stereo(path: Path) -> dict[str, Any]:
    side_db = side_mid_db(path)
    corr = lr_correlation(path)

    if side_db is None or corr is None:
        return {
            "is_stereo": False,
            "meaningful": False,
            "side_db": side_db,
            "correlation": corr,
        }

    return {
        "is_stereo": True,
        "meaningful": (side_db > -22.0) or (corr < 0.95),
        "side_db": side_db,
        "correlation": corr,
    }


def split_lr(path: Path, out_dir: Path, base_name: str) -> dict[str, str]:
    audio, sr = load_audio(path)

    if audio.shape[1] < 2:
        raise ValueError(f"Cannot L/R split mono file: {path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    left_path = out_dir / f"{base_name}_L.wav"
    right_path = out_dir / f"{base_name}_R.wav"

    sf.write(str(left_path), audio[:, 0], sr, subtype="PCM_16")
    sf.write(str(right_path), audio[:, 1], sr, subtype="PCM_16")

    return {
        "left": str(left_path),
        "right": str(right_path),
    }


def mono_correlation(path_a: Path, path_b: Path) -> float | None:
    a, sr_a = load_audio(path_a)
    b, sr_b = load_audio(path_b)

    if sr_a != sr_b:
        return None

    a = to_mono(a).astype(np.float64)
    b = to_mono(b).astype(np.float64)

    n = min(len(a), len(b))
    if n <= 0:
        return None

    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()

    denom = np.sqrt(np.sum(a * a) * np.sum(b * b))
    if denom < EPS:
        return None

    return float(np.sum(a * b) / denom)


def match_channels(audio: np.ndarray, channels: int) -> np.ndarray:
    if audio.shape[1] == channels:
        return audio
    if audio.shape[1] == 1 and channels == 2:
        return np.repeat(audio, 2, axis=1)
    if audio.shape[1] == 2 and channels == 1:
        return audio.mean(axis=1, keepdims=True)
    return audio[:, :channels]


def residual_relative_db(
    original_path: Path,
    stem_a_path: Path,
    stem_b_path: Path,
) -> float | None:
    original, sr_o = load_audio(original_path)
    a, sr_a = load_audio(stem_a_path)
    b, sr_b = load_audio(stem_b_path)

    if sr_o != sr_a or sr_o != sr_b:
        return None

    n = min(len(original), len(a), len(b))
    if n <= 0:
        return None

    channels = original.shape[1]
    residual = original[:n] - match_channels(a[:n], channels) - match_channels(b[:n], channels)
    return rms_db(residual) - rms_db(original[:n])


def classify_lead_guitar(lead_path: Path, guitar_path: Path) -> dict[str, Any]:
    rel_db = relative_loudness_db(lead_path, guitar_path)
    active = active_ratio(lead_path)

    if rel_db > -18.0 and active > 0.02:
        status = "good"
    elif rel_db > -26.0 and active > 0.01:
        status = "maybe"
    else:
        status = "weak_check_manually"

    return {
        "status": status,
        "relative_db_to_guitar": rel_db,
        "active_ratio": active,
    }


def classify_backing_vocals(
    backing_path: Path,
    vocals_path: Path,
    lead_path: Path | None = None,
) -> dict[str, Any]:
    rel_db = relative_loudness_db(backing_path, vocals_path)
    active = active_ratio(backing_path)

    if rel_db > -20.0 and active > 0.02:
        status = "good"
    elif rel_db > -28.0 and active > 0.01:
        status = "maybe"
    else:
        status = "weak_check_manually"

    result: dict[str, Any] = {
        "status": status,
        "relative_db_to_vocals": rel_db,
        "active_ratio": active,
    }

    if lead_path is not None:
        corr = mono_correlation(lead_path, backing_path)
        result["lead_back_correlation"] = corr

        if corr is not None and corr > 0.85:
            result["split_warning"] = "lead_and_backing_are_very_similar"

    return result


def maybe_split_stem_lr(
    *,
    stem_path: Path | None,
    final_dir: Path,
    base_name: str,
    always_split_lr: bool,
) -> dict[str, Any] | None:
    if stem_path is None:
        return None

    stereo = meaningful_stereo(stem_path)
    should_split = always_split_lr or bool(stereo["meaningful"])

    result: dict[str, Any] = {
        "file": str(stem_path),
        **stereo,
        "split_lr": should_split,
    }

    if should_split and stereo["is_stereo"]:
        result["outputs"] = split_lr(stem_path, final_dir, base_name)

    return result
