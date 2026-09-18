"""Addressing one item inside a plan document, e.g. `days[3].dinner` or `sessions[0]`.

A revise call names a target. Only that target may change: replacements addressed
anywhere else are refused rather than applied (PRD 11).
"""
import re
from typing import Any

SEGMENT = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]')


def parse(path: str) -> list[str | int]:
    parts: list[str | int] = []
    position = 0
    for match in SEGMENT.finditer(path):
        if match.start() > position and path[position : match.start()] not in ('.', ''):
            raise ValueError(f'{path!r} is not a valid path')
        parts.append(match.group(1) if match.group(1) else int(match.group(2)))
        position = match.end()
    if position != len(path) or not parts:
        raise ValueError(f'{path!r} is not a valid path')
    return parts


def get(document: Any, path: str) -> Any:
    value = document
    for part in parse(path):
        value = value[part]
    return value


def set_value(document: Any, path: str, value: Any) -> None:
    parts = parse(path)
    target = document
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


def is_within(target: str, scope: str) -> bool:
    """True when `target` is the scope itself or something inside it."""
    if scope == 'plan' or target == scope:
        return True
    return target.startswith(scope) and target[len(scope)] in '.['


def exercise_target(document: dict, key: str) -> str | None:
    """Fitness revisions may name an exercise by key rather than by index."""
    for session_index, session in enumerate(document.get('sessions', [])):
        for exercise_index, exercise in enumerate(session.get('exercises', [])):
            if exercise.get('key') == key:
                return f'sessions[{session_index}].exercises[{exercise_index}]'
    return None
