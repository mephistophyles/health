"""Members, goals, equipment profiles and profile suggestions.

Profiles are data, not code: everything here is editable and a goal change opens a new
goal record rather than overwriting the old one (PRD 2).
"""
import json
from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import Connection
from pydantic import BaseModel, Field

from health.api.common import one
from health.deps import get_connection, get_identity
from health.identity import Identity
from health.settings import Settings, get_settings

router = APIRouter(prefix='/api', tags=['members'])


class PresentationRules(BaseModel):
    toggles: list[str] = Field(default_factory=list)
    notes: str = ''


class MemberOut(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    birth_month: date | None = None
    notes: str = ''
    presentation_rules: PresentationRules = Field(default_factory=PresentationRules)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    sort_order: int = 0
    is_user: bool = False


class MemberUpdate(BaseModel):
    name: str | None = None
    birth_month: date | None = None
    notes: str | None = None
    presentation_rules: PresentationRules | None = None
    likes: list[str] | None = None
    dislikes: list[str] | None = None
    exclusions: list[str] | None = None


class GoalIn(BaseModel):
    kind: Literal['meal', 'fitness']
    statement: str
    target: str | None = None
    target_date: date | None = None
    constraints: str = ''


class EquipmentIn(BaseModel):
    items: list[str] = Field(default_factory=list)
    notes: str = ''


class Me(BaseModel):
    auth_user: str
    display_name: str
    member_id: UUID
    member_slug: str
    is_admin: bool
    auth_mode: str


@router.get('/me', response_model=Me)
def me(
    identity: Identity = Depends(get_identity), settings: Settings = Depends(get_settings)
) -> Me:
    return Me(
        auth_user=identity.auth_user,
        display_name=identity.display_name,
        member_id=identity.member_id,
        member_slug=identity.member_slug,
        is_admin=identity.is_admin,
        auth_mode=settings.auth_mode,
    )


@router.get('/members', response_model=list[MemberOut])
def list_members(connection: Connection = Depends(get_connection)) -> list[MemberOut]:
    rows = connection.execute(
        """SELECT m.*, EXISTS (SELECT 1 FROM app_user u WHERE u.member_id = m.id) AS is_user
             FROM member m ORDER BY m.sort_order, m.name"""
    ).fetchall()
    return [MemberOut(**row) for row in rows]


@router.patch('/members/{member_id}', response_model=MemberOut)
def update_member(
    member_id: UUID,
    update: MemberUpdate,
    connection: Connection = Depends(get_connection),
) -> MemberOut:
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Nothing to update')
    if 'presentation_rules' in fields:
        fields['presentation_rules'] = json.dumps(fields['presentation_rules'])
    assignments = ', '.join(f'{name} = %s' for name in fields)
    row = one(
        connection,
        f'UPDATE member SET {assignments}, updated_at = now() WHERE id = %s'
        """ RETURNING *, EXISTS (SELECT 1 FROM app_user u WHERE u.member_id = member.id) AS is_user""",
        (*fields.values(), member_id),
        'No such member',
    )
    return MemberOut(**row)


@router.get('/members/{member_id}/goals')
def list_goals(
    member_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> list[dict[str, Any]]:
    """Meal goals are shared. Another member's fitness goals are never returned."""
    return connection.execute(
        """SELECT * FROM goal
            WHERE member_id = %s AND (kind = 'meal' OR member_id = %s)
            ORDER BY active_from DESC, created_at DESC LIMIT 100""",
        (member_id, identity.member_id),
    ).fetchall()


@router.post('/members/{member_id}/goals', status_code=status.HTTP_201_CREATED)
def set_goal(
    member_id: UUID,
    goal: GoalIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    """A goal change closes the current record and opens a new one; history stays."""
    if goal.kind == 'fitness':
        identity.require_owner(member_id)
    connection.execute(
        'UPDATE goal SET active_to = CURRENT_DATE'
        ' WHERE member_id = %s AND kind = %s AND active_to IS NULL',
        (member_id, goal.kind),
    )
    return one(
        connection,
        """INSERT INTO goal (household_id, member_id, kind, statement, target, target_date,
                             constraints)
           SELECT household_id, id, %s, %s, %s, %s, %s FROM member WHERE id = %s
           RETURNING *""",
        (goal.kind, goal.statement, goal.target, goal.target_date, goal.constraints, member_id),
        'No such member',
    )


@router.get('/members/{member_id}/equipment')
def list_equipment(
    member_id: UUID,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> list[dict[str, Any]]:
    identity.require_owner(member_id)
    return connection.execute(
        'SELECT * FROM equipment_profile WHERE member_id = %s ORDER BY name', (member_id,)
    ).fetchall()


@router.put('/members/{member_id}/equipment/{name}')
def set_equipment(
    member_id: UUID,
    name: str,
    profile: EquipmentIn,
    connection: Connection = Depends(get_connection),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    identity.require_owner(member_id)
    return one(
        connection,
        """INSERT INTO equipment_profile (household_id, member_id, name, items, notes)
           SELECT household_id, id, %s, %s, %s FROM member WHERE id = %s
           ON CONFLICT (member_id, name) DO UPDATE
             SET items = EXCLUDED.items, notes = EXCLUDED.notes, updated_at = now()
           RETURNING *""",
        (name, profile.items, profile.notes, member_id),
        'No such member',
    )


@router.get('/profile-suggestions')
def list_suggestions(connection: Connection = Depends(get_connection)) -> list[dict[str, Any]]:
    return connection.execute(
        """SELECT s.*, m.slug, m.name FROM profile_suggestion s JOIN member m ON m.id = s.member_id
            WHERE s.status = 'pending' ORDER BY s.created_at DESC LIMIT 50"""
    ).fetchall()


class SuggestionDecision(BaseModel):
    accept: bool
    apply_to: Literal['notes', 'presentation_rules', 'dislikes', 'none'] = 'none'


@router.post('/profile-suggestions/{suggestion_id}')
def decide_suggestion(
    suggestion_id: UUID,
    decision: SuggestionDecision,
    connection: Connection = Depends(get_connection),
) -> dict[str, Any]:
    """Accepting records the decision; applying the wording to the profile is opt-in."""
    suggestion = one(
        connection,
        'SELECT * FROM profile_suggestion WHERE id = %s',
        (suggestion_id,),
        'No such suggestion',
    )
    if decision.accept and decision.apply_to == 'notes':
        connection.execute(
            "UPDATE member SET notes = trim(both from notes || E'\\n' || %s), updated_at = now()"
            ' WHERE id = %s',
            (suggestion['change'], suggestion['member_id']),
        )
    elif decision.accept and decision.apply_to == 'presentation_rules':
        connection.execute(
            """UPDATE member SET presentation_rules = jsonb_set(presentation_rules, '{toggles}',
                 COALESCE(presentation_rules->'toggles', '[]'::jsonb) || to_jsonb(%s::text)),
                 updated_at = now() WHERE id = %s""",
            (suggestion['change'], suggestion['member_id']),
        )
    elif decision.accept and decision.apply_to == 'dislikes':
        connection.execute(
            'UPDATE member SET dislikes = dislikes || %s::text, updated_at = now() WHERE id = %s',
            (suggestion['change'], suggestion['member_id']),
        )
    return one(
        connection,
        'UPDATE profile_suggestion SET status = %s, resolved_at = now() WHERE id = %s RETURNING *',
        ('accepted' if decision.accept else 'dismissed', suggestion_id),
        'No such suggestion',
    )
