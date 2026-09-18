"""Library verdicts and the compact digest the meal loop is given instead of the library."""
from dataclasses import dataclass, field


@dataclass
class RecipeStats:
    times_served: int = 0
    average_enjoyment: float | None = None
    repeat_yes: int = 0
    repeat_with_tweaks: int = 0
    repeat_no: int = 0
    kid_ate_rate: float | None = None


def derive_verdict(stats: RecipeStats, staple: bool = False) -> str:
    """The verdict a recipe earns from its feedback. Always overridable by hand (PRD 8)."""
    if staple:
        return 'staple'
    if stats.times_served == 0 or stats.average_enjoyment is None:
        return 'untried'
    if stats.repeat_no > stats.repeat_yes + stats.repeat_with_tweaks:
        return 'didnt_work'
    if stats.average_enjoyment < 3:
        return 'didnt_work'
    if stats.average_enjoyment >= 4 and stats.repeat_yes > 0 and stats.repeat_no == 0:
        return 'worked'
    return 'needs_tweak'


@dataclass
class DigestEntry:
    title: str
    tags: list[str] = field(default_factory=list)
    average_enjoyment: float | None = None
    last_served: str | None = None
    tweak_notes: str = ''
    reason: str = ''


@dataclass
class LibraryDigest:
    """What the meal loop sees: staples, the best of what worked, and what to avoid."""

    staples: list[DigestEntry] = field(default_factory=list)
    worked: list[DigestEntry] = field(default_factory=list)
    didnt_work: list[DigestEntry] = field(default_factory=list)
    recent_dinners: list[str] = field(default_factory=list)
    rejections: list[DigestEntry] = field(default_factory=list)

    def as_dict(self) -> dict:
        def entries(items: list[DigestEntry]) -> list[dict]:
            return [{k: v for k, v in entry.__dict__.items() if v not in (None, '', [])}
                    for entry in items]

        return {
            'staples': entries(self.staples),
            'worked': entries(self.worked),
            'didnt_work': entries(self.didnt_work),
            'recent_dinners': self.recent_dinners,
            'rejections': entries(self.rejections),
        }


WORKED_LIMIT = 15
DIDNT_WORK_LIMIT = 10
