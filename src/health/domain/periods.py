"""Period defaults and the Friday checklist state (PRD 3)."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

SATURDAY = 5
FRIDAY = 4
DEFAULT_LENGTH_DAYS = 7
MIN_LENGTH_DAYS = 5
MAX_LENGTH_DAYS = 14


def next_period_dates(previous_end: date | None, today: date, length: int = DEFAULT_LENGTH_DAYS) -> tuple[date, date]:
    """The next period starts the day after the last one ended, else the coming Saturday."""
    if previous_end is not None:
        start = previous_end + timedelta(days=1)
    else:
        start = today + timedelta(days=(SATURDAY - today.weekday()) % 7)
    return start, start + timedelta(days=length - 1)


def default_shopping_trips(start: date, end: date) -> list[date]:
    """A major trip on the first weekend day of the period, and a midweek top-up."""
    trips = [start]
    midweek = start + timedelta(days=4)
    if midweek <= end:
        trips.append(midweek)
    return trips


def default_training_days(start: date, end: date) -> list[date]:
    """Weekdays only. Weekends are never planned (PRD 6)."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def period_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def checklist_opens_at(active_end: date) -> date:
    """The last Friday on or before the period ends — noon that day is when nagging starts."""
    return active_end - timedelta(days=(active_end.weekday() - FRIDAY) % 7)


def is_planning_window(now: datetime, active_end: date | None) -> bool:
    """The checklist shows from Friday noon until the next period has been accepted.

    Without an active period there is nothing to wrap up, so planning is always open.
    """
    if active_end is None:
        return True
    opens = checklist_opens_at(active_end)
    return (now.date(), now.hour) >= (opens, 12)


@dataclass
class ChecklistItem:
    key: str
    label: str
    done: bool
    detail: str = ''


@dataclass
class Checklist:
    show: bool
    items: list[ChecklistItem]

    @property
    def complete(self) -> bool:
        return all(item.done for item in self.items)
