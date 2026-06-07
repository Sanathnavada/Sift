"""
Shared helpers to normalize inline and file-backed API input.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional


class InputResolutionError(ValueError):
    pass


def resolve_single_input(*, direct_value: Optional[str] = None,
                         input_file: Optional[str] = None) -> str:
    values = _resolve_values(direct_values=[direct_value] if direct_value else None, input_file=input_file)
    if len(values) != 1:
        raise InputResolutionError("Expected exactly one input value.")
    return values[0]


def resolve_multi_input(*, direct_values: Optional[Iterable[str]] = None,
                        input_file: Optional[str] = None) -> list[str]:
    return _resolve_values(direct_values=direct_values, input_file=input_file)


def _resolve_values(*, direct_values: Optional[Iterable[str]], input_file: Optional[str]) -> list[str]:
    has_direct = bool(direct_values)
    has_file = bool(input_file)
    if has_direct == has_file:
        raise InputResolutionError("Provide exactly one of direct input or input_file.")

    if has_direct:
        values = [value.strip() for value in direct_values if value and value.strip()]
    else:
        values = _read_input_file(Path(input_file).expanduser())

    if not values:
        raise InputResolutionError("Input source resolved to an empty list.")
    return values


def _read_input_file(path: Path) -> list[str]:
    if not path.exists():
        raise InputResolutionError(f"Input file not found: {path}")

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            raise InputResolutionError("JSON input_file must contain a list of strings.")
        return [item.strip() for item in data if item.strip()]

    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
