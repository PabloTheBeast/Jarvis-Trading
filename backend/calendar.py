"""Nasdaq Stockholm cash-equity calendar, Europe/Stockholm.

Rules reviewed for 2025–2027 against Nasdaq Nordic Trading Hours / Holiday
Schedule: https://www.nasdaq.com/solutions/european-markets-trading-hours
Reference: https://github.com/gerrymanoim/exchange_calendars/blob/master/exchange_calendars/exchange_calendar_xsto.py\nOutside that range equity sessions fail closed until the calendar is reviewed.
Crypto has no exchange holidays. Times include the closing auction.
"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo

STOCKHOLM = ZoneInfo('Europe/Stockholm')
REVIEWED_YEARS = range(2025, 2028)


def easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19*a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2*e + 2*i - h - k) % 7
    m = (a + 11*h + 22*l) // 451
    n = h + l - 7*m + 114
    return date(year, n // 31, n % 31 + 1)


def session(day):
    """Return local opening/closing minutes, or None on a closed/unreviewed day."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    if day.year not in REVIEWED_YEARS or day.weekday() >= 5:
        return None
    year, spring = day.year, easter(day.year)
    midsummer = next(date(year, 6, d) for d in range(19, 26) if date(year, 6, d).weekday() == 4)
    closed = {date(year, m, d) for m, d in
              [(1, 1), (1, 6), (5, 1), (6, 6), (12, 24), (12, 25), (12, 26), (12, 31)]}
    closed.update([spring-timedelta(days=2), spring+timedelta(days=1),
                   spring+timedelta(days=39), midsummer])
    if day in closed:
        return None
    all_saints = next(date(year, 10, 31)+timedelta(days=i) for i in range(7)
                      if (date(year, 10, 31)+timedelta(days=i)).weekday() == 5)
    half_days = {date(year, 1, 5), spring-timedelta(days=3), date(year, 4, 30),
                 spring+timedelta(days=38), all_saints-timedelta(days=1)}
    return 540, 780 if day in half_days else 1050


def clock(now):
    local = now.astimezone(STOCKHOLM)
    hours = session(local.date())
    return local.date().isoformat(), bool(hours and hours[0] <= local.hour*60+local.minute < hours[1])
