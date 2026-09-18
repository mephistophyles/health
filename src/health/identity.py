from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class Identity:
    """Who is asking. Resolved from the proxy header, never from the request body."""

    auth_user: str
    member_id: UUID
    member_slug: str
    display_name: str
    is_admin: bool

    def require_admin(self) -> None:
        if not self.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, 'Admin only')

    def require_owner(self, member_id: UUID) -> None:
        """Guard for private data: only its owner may read or write it."""
        if member_id != self.member_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, 'That data is private to its owner')


def auth_user_from_request(request: Request, auth_mode: str, single_user_id: str) -> str:
    if auth_mode == 'none':
        return single_user_id
    auth_user = request.headers.get('X-Auth-User')
    if not auth_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'Unknown identity')
    return auth_user


def get_identity(request: Request) -> Identity:
    """FastAPI dependency. The resolved identity is attached by the identity middleware."""
    identity = getattr(request.state, 'identity', None)
    if identity is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'Unknown identity')
    return identity
