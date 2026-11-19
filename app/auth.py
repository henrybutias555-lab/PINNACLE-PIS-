"""Authentication (JWT bearer tokens), role checks and activity logging."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import ActivityLog, User

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)

# Role hierarchy: higher number = more privilege.
ROLE_LEVEL = {"viewer": 0, "junior": 1, "senior": 2, "admin": 3}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.token_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, int(payload.get("sub", 0)))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive or not found")
    _mark_seen(db, user)
    return user


def _mark_seen(db: Session, user: User) -> None:
    """Remember when the user was last active (at most one write every 30 s)."""
    from .models import _aware, utcnow

    now = utcnow()
    seen = _aware(user.last_seen_at)
    if seen is None or (now - seen).total_seconds() > 30:
        user.last_seen_at = now
        try:
            db.commit()
        except Exception:      # never block a request over this
            db.rollback()


def require_role(minimum: str):
    """Dependency factory: allow users whose role is at least ``minimum``."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if ROLE_LEVEL.get(user.role, -1) < ROLE_LEVEL[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role '{minimum}' or higher")
        return user

    return checker


def log_activity(
    db: Session,
    user: User | None,
    action: str,
    target: str | None = None,
    detail: str | None = None,
    request: Request | None = None,
) -> None:
    ip = None
    if request is not None and request.client is not None:
        ip = request.client.host
    db.add(
        ActivityLog(
            user_id=user.id if user else None,
            user_email=user.email if user else None,
            action=action,
            target=target,
            detail=(detail or "")[:2000] or None,
            ip=ip,
        )
    )
    db.commit()
