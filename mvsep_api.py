from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from config import CREATE_URL, DISABLE_PREMIUM_URL, GET_URL
from file_utils import audio_files, looks_like_url, safe_filename, unique_path, write_json


def disable_premium_usage(api_token: str) -> dict[str, Any]:
    """Disable MVSEP premium usage for this account before creating jobs.

    This is intentionally fail-closed: if MVSEP does not confirm success, the
    pipeline stops instead of accidentally using premium processing.
    """
    response = requests.post(
        DISABLE_PREMIUM_URL,
        data={"api_token": api_token},
        timeout=(30, 120),
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to disable MVSEP premium usage: HTTP {response.status_code}\n"
            f"{response.text[:2000]}"
        )

    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(
            "Failed to disable MVSEP premium usage:\n"
            f"{json.dumps(payload, indent=2)}"
        )

    print("MVSEP premium usage disabled for this run.")
    return payload


def create_separation(
    *,
    api_token: str,
    audio_path: Path,
    sep_type: int,
    output_format: int = 1,
    add_opt1: str | None = None,
    add_opt2: str | None = None,
    add_opt3: str | None = None,
    is_demo: bool = False,
) -> dict[str, Any]:
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    data: dict[str, str] = {
        "api_token": api_token,
        "sep_type": str(sep_type),
        "output_format": str(output_format),
        "is_demo": "1" if is_demo else "0",
    }

    for key, value in {
        "add_opt1": add_opt1,
        "add_opt2": add_opt2,
        "add_opt3": add_opt3,
    }.items():
        if value is not None:
            data[key] = str(value)

    print(f"\nCreating MVSEP job: sep_type={sep_type}, input={audio_path.name}")

    with audio_path.open("rb") as f:
        response = requests.post(
            CREATE_URL,
            data=data,
            files={"audiofile": (audio_path.name, f, "application/octet-stream")},
            timeout=(30, 600),
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"MVSEP create failed: HTTP {response.status_code}\n{response.text[:2000]}"
        )

    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(f"MVSEP create failed:\n{json.dumps(payload, indent=2)}")

    job_hash = payload.get("data", {}).get("hash")
    if not job_hash:
        raise RuntimeError(f"MVSEP response did not contain data.hash:\n{payload}")

    print(f"Created job hash: {job_hash}")
    return payload


def poll_separation(
    *,
    job_hash: str,
    api_token: str | None = None,
    poll_seconds: int = 30,
) -> dict[str, Any]:
    while True:
        params = {"hash": job_hash}
        if api_token:
            params["api_token"] = api_token

        response = requests.get(GET_URL, params=params, timeout=(30, 120))

        if response.status_code >= 400:
            raise RuntimeError(
                f"MVSEP get failed: HTTP {response.status_code}\n{response.text[:2000]}"
            )

        payload = response.json()
        status = payload.get("status")
        data = payload.get("data") or {}
        message = data.get("message", "")

        queue_info = ""
        if "current_order" in data:
            queue_info += f", order={data.get('current_order')}"
        if "queue_count" in data:
            queue_info += f", queue={data.get('queue_count')}"

        print(f"Status: {status}{queue_info} {message}".strip())

        if status == "done":
            return payload

        if status in {"failed", "not_found", "error"}:
            raise RuntimeError(f"MVSEP job failed:\n{json.dumps(payload, indent=2)}")

        time.sleep(poll_seconds)


def extract_download_urls(obj: Any, hint: str | None = None) -> list[tuple[str | None, str]]:
    """MVSEP data.files can vary by algorithm, so collect URLs recursively."""
    results: list[tuple[str | None, str]] = []

    if isinstance(obj, dict):
        local_hint = hint

        for hint_key in ("name", "filename", "title", "stem", "label", "type"):
            value = obj.get(hint_key)
            if isinstance(value, str) and not looks_like_url(value):
                local_hint = value
                break

        for key, value in obj.items():
            if isinstance(value, str) and looks_like_url(value):
                results.append((local_hint or key, value))
            else:
                results.extend(extract_download_urls(value, local_hint or key))

    elif isinstance(obj, list):
        for item in obj:
            results.extend(extract_download_urls(item, hint))

    elif isinstance(obj, str) and looks_like_url(obj):
        results.append((hint, obj))

    return results


def filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None

    match = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", header, re.I)
    if not match:
        return None

    return safe_filename(match.group(1).strip().strip('"'))


def guess_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name or "download.wav"
    return safe_filename(name)


def download_file(url: str, out_dir: Path, name_hint: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    with requests.get(url, stream=True, timeout=(30, 600)) as response:
        response.raise_for_status()

        filename = filename_from_content_disposition(
            response.headers.get("content-disposition")
        ) or guess_filename_from_url(url)

        if name_hint and (filename.startswith("download") or "." not in filename):
            suffix = Path(filename).suffix or ".wav"
            filename = f"{safe_filename(name_hint)}{suffix}"

        dest = unique_path(out_dir / filename)

        with dest.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    print(f"Downloaded: {dest.name}")
    return dest


def download_outputs(result_payload: dict[str, Any], out_dir: Path) -> list[Path]:
    files_obj = (result_payload.get("data") or {}).get("files")

    if files_obj is None:
        write_json(out_dir / "debug_no_files_payload.json", result_payload)
        raise RuntimeError("MVSEP result had no data.files. Wrote debug_no_files_payload.json")

    seen: set[str] = set()
    unique_urls: list[tuple[str | None, str]] = []

    for hint, url in extract_download_urls(files_obj):
        if url not in seen:
            seen.add(url)
            unique_urls.append((hint, url))

    if not unique_urls:
        write_json(out_dir / "debug_no_download_urls.json", result_payload)
        raise RuntimeError("Could not find download URLs in data.files.")

    downloaded: list[Path] = []
    for hint, url in unique_urls:
        try:
            downloaded.append(download_file(url, out_dir, hint))
        except Exception as exc:
            print(f"WARNING: failed to download {url}: {exc}")

    return downloaded


def run_mvsep_stage(
    *,
    stage_name: str,
    input_audio: Path,
    api_token: str,
    stage_dir: Path,
    model: dict[str, Any],
    poll_seconds: int,
    reuse_existing: bool,
) -> list[Path]:
    stage_dir.mkdir(parents=True, exist_ok=True)

    existing = audio_files(stage_dir)
    if reuse_existing and existing:
        print(f"\nReusing existing stage: {stage_name}")
        for path in existing:
            print(f"  {path.name}")
        return existing

    create_payload = create_separation(
        api_token=api_token,
        audio_path=input_audio,
        sep_type=int(model["sep_type"]),
        output_format=int(model.get("output_format", 1)),
        add_opt1=model.get("add_opt1"),
        add_opt2=model.get("add_opt2"),
        add_opt3=model.get("add_opt3"),
    )
    write_json(stage_dir / "create_response.json", create_payload)

    result_payload = poll_separation(
        job_hash=create_payload["data"]["hash"],
        api_token=api_token,
        poll_seconds=poll_seconds,
    )
    write_json(stage_dir / "result_response.json", result_payload)

    return download_outputs(result_payload, stage_dir)
