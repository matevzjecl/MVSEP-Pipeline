#!/usr/bin/env python3
"""Print MVSEP separation types by sep_type/render_id.

Usage:
    python get_separation_types_by_id.py
    python get_separation_types_by_id.py --options
    python get_separation_types_by_id.py --all-fields
    python get_separation_types_by_id.py --omit-fields price_coefficient,active
    python get_separation_types_by_id.py --filter guitar
    python get_separation_types_by_id.py --json

Notes:
    MVSEP uses `render_id` from /api/app/algorithms as the `sep_type`
    value when creating a separation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional for this script
    load_dotenv = None

MVSEP_ALGORITHMS_URL = "https://mvsep.com/api/app/algorithms"

ALL_FIELDS = (
    "sep_type",
    "name",
    "group",
    "active",
    "price_coefficient",
    "description",
    "short_description",
    "long_description",
    "fields",
)

DEFAULT_FIELDS = (
    "sep_type",
    "name",
    "group",
    "active",
    "price_coefficient",
    "description",
)


@dataclass(frozen=True)
class SeparationType:
    sep_type: int
    name: str
    group: str
    active: bool
    price_coefficient: float | int | None
    description: str
    short_description: str
    long_description: str
    fields: list[dict[str, Any]]


def load_env() -> None:
    if load_dotenv is not None:
        load_dotenv()

def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

def request_json(url: str, params: dict[str, str] | None = None) -> Any:
    full_url = f"{url}?{urlencode(params)}" if params else url
    request = Request(full_url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MVSEP request failed: HTTP {exc.code}\n{body}") from exc
    except URLError as exc:
        raise RuntimeError(f"MVSEP request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("MVSEP returned invalid JSON.") from exc


def parse_options(raw_options: Any) -> dict[str, str]:
    if isinstance(raw_options, dict):
        return {str(key): str(value) for key, value in raw_options.items()}

    if not isinstance(raw_options, str) or not raw_options.strip():
        return {}

    try:
        parsed = json.loads(raw_options)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return {str(key): str(value) for key, value in parsed.items()}


def clean_text(value: Any) -> str:
    return str(value).strip() if value else ""


def get_localized_description(descriptions: Any, key: str, preferred_lang: str = "en") -> str:
    if not isinstance(descriptions, list):
        return ""

    preferred = next(
        (
            item
            for item in descriptions
            if isinstance(item, dict) and item.get("lang") == preferred_lang
        ),
        None,
    )

    fallback = next((item for item in descriptions if isinstance(item, dict)), None)
    selected = preferred or fallback or {}

    return clean_text(selected.get(key))


def normalize_algorithms(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]

    raise RuntimeError("Unexpected MVSEP algorithms response shape.")


def build_separation_types(algorithms: list[dict[str, Any]]) -> list[SeparationType]:
    result: list[SeparationType] = []

    for item in algorithms:
        sep_type = item.get("render_id")
        name = item.get("name")

        if sep_type is None or not name:
            continue

        group_obj = item.get("algorithm_group") or {}
        group = group_obj.get("name") or "Other"

        algorithm_descriptions = item.get("algorithm_descriptions")

        description = clean_text(item.get("description"))
        short_description = get_localized_description(
            algorithm_descriptions,
            "short_description",
        )
        long_description = get_localized_description(
            algorithm_descriptions,
            "long_description",
        )

        fields: list[dict[str, Any]] = []
        for field in item.get("algorithm_fields") or []:
            fields.append(
                {
                    "name": field.get("name", ""),
                    "text": field.get("text", ""),
                    "default_key": field.get("default_key"),
                    "required": bool(field.get("required")),
                    "options": parse_options(field.get("options")),
                }
            )

        result.append(
            SeparationType(
                sep_type=int(sep_type),
                name=str(name),
                group=str(group),
                active=bool(item.get("is_active")),
                price_coefficient=item.get("price_coefficient"),
                description=description,
                short_description=short_description,
                long_description=long_description,
                fields=fields,
            )
        )

    return sorted(result, key=lambda alg: (alg.group.lower(), alg.sep_type, alg.name.lower()))


def filter_types(types: list[SeparationType], query: str | None) -> list[SeparationType]:
    if not query:
        return types

    needle = query.lower().strip()
    return [
        item
        for item in types
        if needle in item.name.lower()
        or needle in item.group.lower()
        or needle in item.description.lower()
        or needle in item.short_description.lower()
        or needle in item.long_description.lower()
        or needle == str(item.sep_type)
    ]


def parse_field_list(value: str | None) -> set[str]:
    if not value:
        return set()

    fields = {field.strip() for field in value.split(",") if field.strip()}
    unknown_fields = fields - set(ALL_FIELDS)

    if unknown_fields:
        valid = ", ".join(ALL_FIELDS)
        unknown = ", ".join(sorted(unknown_fields))
        raise ValueError(f"Unknown field(s): {unknown}. Valid fields: {valid}")

    return fields


def resolve_output_fields(args: argparse.Namespace) -> set[str]:
    fields = set(ALL_FIELDS if args.all_fields else DEFAULT_FIELDS)

    # Backward-compatible shortcut.
    # --options means "also show algorithm fields/options".
    if args.options:
        fields.add("fields")

    omitted_fields = parse_field_list(args.omit_fields)
    fields -= omitted_fields

    return fields


def print_field_help() -> None:
    print("Available fields:")
    for field in ALL_FIELDS:
        print(f"  {field}")


def format_item_line(item: SeparationType, output_fields: set[str]) -> str:
    parts: list[str] = []

    if "sep_type" in output_fields:
        parts.append(f"{item.sep_type:>4}")

    if "name" in output_fields:
        parts.append(item.name)

    if "group" in output_fields:
        parts.append(f"group={item.group}")

    if "active" in output_fields and not item.active:
        parts.append("[inactive]")

    if (
        "price_coefficient" in output_fields
        and item.price_coefficient not in (None, "", 1, "1")
    ):
        parts.append(f"price x{item.price_coefficient}")

    return "  ".join(parts)


def print_text(types: list[SeparationType], output_fields: set[str]) -> None:
    print("MVSEP separation types by ID")
    print("Use the ID as sep_type when creating a separation.\n")

    if "group" in output_fields:
        by_group: dict[str, list[SeparationType]] = defaultdict(list)
        for item in types:
            by_group[item.group].append(item)

        for group in sorted(by_group, key=str.lower):
            print(group)
            print("-" * len(group))

            for item in by_group[group]:
                print_item(item, output_fields)

            print()
    else:
        for item in types:
            print_item(item, output_fields)


def print_item(item: SeparationType, output_fields: set[str]) -> None:
    line = format_item_line(item, output_fields)
    if line:
        print(line)

    if "description" in output_fields and item.description:
        print(f"      description: {item.description}")

    if "short_description" in output_fields and item.short_description:
        print(f"      short_description: {item.short_description}")

    if "long_description" in output_fields and item.long_description:
        print(f"      long_description: {item.long_description}")

    if "fields" not in output_fields or not item.fields:
        return

    for field in item.fields:
        required = "required" if field["required"] else "optional"
        default = field.get("default_key")
        default_text = f", default={default}" if default is not None else ""

        print(f"      {field['name']} - {field['text']} ({required}{default_text})")

        for key, value in field["options"].items():
            default_marker = "  <-- default" if str(key) == str(default) else ""
            print(f"        {key}: {value}{default_marker}")


def to_plain_dict(item: SeparationType, output_fields: set[str]) -> dict[str, Any]:
    full = {
        "sep_type": item.sep_type,
        "name": item.name,
        "group": item.group,
        "active": item.active,
        "price_coefficient": item.price_coefficient,
        "description": item.description,
        "short_description": item.short_description,
        "long_description": item.long_description,
        "fields": item.fields,
    }

    return {field: full[field] for field in ALL_FIELDS if field in output_fields}


def main() -> int:
    configure_output_encoding()

    parser = argparse.ArgumentParser(description="List MVSEP separation types by ID.")
    parser.add_argument("--filter", help="Filter by ID, name, group, or description.")
    parser.add_argument("--options", action="store_true", help="Also print add_opt fields and defaults.")
    parser.add_argument("--all-fields", action="store_true", help="Print/export every available field.")
    parser.add_argument(
        "--omit-fields",
        help=(
            "Comma-separated fields to omit. "
            "Valid fields: sep_type,name,group,active,price_coefficient,"
            "description,short_description,long_description,fields"
        ),
    )
    parser.add_argument("--list-fields", action="store_true", help="Show available output fields and exit.")
    parser.add_argument("--inactive", action="store_true", help="Include inactive algorithms.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    parser.add_argument("--url", default=MVSEP_ALGORITHMS_URL, help="Override MVSEP algorithms URL.")
    args = parser.parse_args()

    if args.list_fields:
        print_field_help()
        return 0

    try:
        output_fields = resolve_output_fields(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    load_env()

    # Usually not required for /api/app/algorithms, but harmless if MVSEP ever accepts it.
    api_token = os.getenv("MVSEP_API_TOKEN") or os.getenv("MVSEP_TOKEN")
    params = {"api_token": api_token} if api_token else None

    try:
        payload = request_json(args.url, params=params)
        algorithms = normalize_algorithms(payload)
        types = build_separation_types(algorithms)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not args.inactive:
        types = [item for item in types if item.active]

    types = filter_types(types, args.filter)

    if args.json:
        print(
            json.dumps(
                [to_plain_dict(item, output_fields) for item in types],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_text(types, output_fields)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())