"""Utilities for working with age calculations."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal


def days_between_dates(start: date, end: date) -> int:
    """Return the number of days between two dates."""
    return (end - start).days


def age_breakdown(start: date, end: date) -> tuple[int, int, int]:
    """Return the (years, months, days) elapsed from start to end."""
    years = end.year - start.year
    months = end.month - start.month
    days = end.day - start.day

    if days < 0:
        months -= 1
        previous_month_end = end.replace(day=1) - timedelta(days=1)
        days += previous_month_end.day

    if months < 0:
        years -= 1
        months += 12

    return years, months, days


Tense = Literal["past", "today", "future"]


def tense_for(day: date, today: date) -> Tense:
    """Whether day is before, on, or after today, from the viewer's perspective."""
    if day < today:
        return "past"
    if day > today:
        return "future"
    return "today"
