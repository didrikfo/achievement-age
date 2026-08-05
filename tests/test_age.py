from datetime import date

from core.age import age_breakdown, days_between_dates


def test_age_breakdown_straightforward():
    assert age_breakdown(date(1990, 1, 15), date(2020, 3, 20)) == (30, 2, 5)


def test_age_breakdown_same_day():
    assert age_breakdown(date(2000, 6, 15), date(2000, 6, 15)) == (0, 0, 0)


def test_age_breakdown_month_borrow_uses_leap_february():
    # Jan 20 -> Feb 20 is one month; Feb 20 -> Mar 5 borrows all of a leap Feb (29 days).
    assert age_breakdown(date(2000, 1, 20), date(2000, 3, 5)) == (0, 1, 14)


def test_age_breakdown_leap_day_birthday():
    # Someone born on Feb 29, 2000 turns "1 year" just shy of Feb 28, 2001 (no Feb 29 that year).
    assert age_breakdown(date(2000, 2, 29), date(2001, 2, 28)) == (0, 11, 30)


def test_days_between_dates():
    assert days_between_dates(date(2000, 2, 29), date(2001, 2, 28)) == 365
