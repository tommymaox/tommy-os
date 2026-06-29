from __future__ import annotations

import re
from datetime import datetime


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_24_RE = re.compile(r"^\d{1,2}:\d{2}$")
TIME_12_RE = re.compile(r"^\d{1,2}:\d{2}\s*(am|pm)$", re.IGNORECASE)
KB_KEY_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,120}$")


def parse_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError("Date must be YYYY-MM-DD")
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def parse_time(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    text = value.strip()
    if TIME_24_RE.match(text):
        dt = datetime.strptime(text.zfill(5), "%H:%M")
        return dt.strftime("%H:%M")
    if TIME_12_RE.match(text):
        dt = datetime.strptime(text.upper().replace(" ", ""), "%I:%M%p")
        return dt.strftime("%H:%M")
    raise ValueError("Time must be HH:MM or H:MMam/pm")


def trimmed(value: str | None, *, field_name: str, max_length: int, allow_blank: bool = False) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text and not allow_blank:
        raise ValueError(f"{field_name} must not be empty")
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be {max_length} characters or fewer")
    return text


def non_negative(value: float | int | None, *, field_name: str, default: float | None = None) -> float | None:
    if value is None:
        return default
    value = float(value)
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def bounded_positive(value: float | int | None, *, field_name: str, default: float, minimum: float = 0.01, maximum: float = 100000) -> float:
    if value is None:
        return default
    value = float(value)
    if value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")
    if value > maximum:
        raise ValueError(f"{field_name} must be {maximum} or less")
    return value
