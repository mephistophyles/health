"""Strict shapes for every LLM response (PRD 11).

The model never writes to the database. Output is parsed into these models, then run
through `validation.py`, and only then stored. Anything that fails either gate is a
failed loop, not a partial write.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DinnerMode = Literal['cook', 'leftovers', 'takeout', 'eating_out', 'away']
LunchMode = Literal['none', 'leftovers', 'prep']
Perishability = Literal['fresh', 'keeps', 'pantry']
AlternativeReason = Literal['equipment', 'joint-friendly', 'easier', 'harder']
Intent = Literal['Build', 'Repeat', 'Deload']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


# --- fitness.generate -------------------------------------------------------------


class Alternative(Strict):
    name: str
    reason: AlternativeReason
    sets: int | None = None
    reps: int | None = None
    weight_lb: float | None = None


class Exercise(Strict):
    key: str
    name: str
    sets: int | None = None
    reps: int | None = None
    duration_s: int | None = None
    distance_mi: float | None = None
    pace: str | None = None
    weight_lb: float | None = None
    rest_s: int | None = None
    rpe: float | None = None
    cues: str = ''
    alternatives: list[Alternative] = Field(default_factory=list)


class Session(Strict):
    date: str
    focus: str
    duration_min: int
    warmup: list[str] = Field(default_factory=list)
    cooldown: list[str] = Field(default_factory=list)
    exercises: list[Exercise]


class TrainingSummary(Strict):
    """The only fitness output the meal loop ever sees. No weights, no metrics."""

    training_days: list[str] = Field(default_factory=list)
    notes: str = ''


class FitnessPlan(Strict):
    intent: Intent
    summary: str
    summary_for_meals: TrainingSummary
    sessions: list[Session]


# --- meals.generate ---------------------------------------------------------------


class DinnerEntry(Strict):
    mode: DinnerMode
    recipe_key: str | None = None
    leftover_of: str | None = None


class LunchEntry(Strict):
    mode: LunchMode = 'none'
    recipe_key: str | None = None
    leftover_of: str | None = None


class PlanDay(Strict):
    date: str
    dinner: DinnerEntry
    lunch: LunchEntry = Field(default_factory=LunchEntry)


class Feeds(Strict):
    date: str
    meal: Literal['dinner', 'lunch']
    servings: float


class Ingredient(Strict):
    name: str
    qty: float | None = None
    unit: str = ''
    category: str = 'Other'
    perishability: Perishability = 'keeps'


class Step(Strict):
    text: str
    make_ahead: bool = False


class Portion(Strict):
    member: str
    guidance: str


class KidPlate(Strict):
    member: str
    serve: str
    fallback: str = ''


class Recipe(Strict):
    key: str
    library_id: str | None = None
    title: str
    description: str = ''
    active_min: int
    total_min: int
    servings: float
    feeds: list[Feeds] = Field(default_factory=list)
    ingredients: list[Ingredient]
    steps: list[Step]
    portions: list[Portion] = Field(default_factory=list)
    kid_plates: list[KidPlate] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ProfileSuggestion(Strict):
    member: str
    change: str
    evidence: str = ''


class ProfileSuggestions(Strict):
    profile_suggestions: list[ProfileSuggestion] = Field(default_factory=list)


class MealPlan(Strict):
    summary: str
    days: list[PlanDay]
    recipes: list[Recipe] = Field(default_factory=list)
    profile_suggestions: list[ProfileSuggestion] = Field(default_factory=list)


# --- revise -----------------------------------------------------------------------


class Replacement(Strict):
    target: str
    value: Any


class ReviseResult(Strict):
    note: str = ''
    replacements: list[Replacement] = Field(default_factory=list)


PLAN_MODEL = {'meals': MealPlan, 'fitness': FitnessPlan}


def json_schema_for(kind: str) -> dict[str, Any]:
    """The schema handed to OpenRouter structured outputs, or pasted into the prompt."""
    return PLAN_MODEL[kind].model_json_schema()
