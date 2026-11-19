from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import create_token, get_current_user, hash_password, log_activity, require_role, verify_password
from ..database import get_db
from ..models import ActivityLog, User
from ..schemas import ActivityOut, LocationIn, LoginIn, PasswordChange, TokenOut, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/api", tags=["auth & users"])


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        log_activity(db, None, "login_failed", body.email[:255], request=request)
        raise HTTPException(401, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(403, "Account is disabled")
    from ..models import utcnow
    user.last_seen_at = utcnow()
    db.commit()
    log_activity(db, user, "login", request=request)
    return TokenOut(access_token=create_token(user), user=UserOut.model_validate(user))


@router.post("/auth/heartbeat")
def heartbeat(_: User = Depends(get_current_user)):
    """Called every minute by an open PIS page; marks the user as online."""
    return {"ok": True}


@router.post("/auth/location")
def sign_in_location(body: LocationIn, request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Where the device was at sign-in, as reported by the browser after the user allowed it."""
    from ..models import ActivityLog, utcnow
    now = utcnow()
    if body.declined or body.latitude is None or body.longitude is None:
        user.location_status, user.location_status_at = (body.reason or "declined")[:120], now
        db.commit()
        log_activity(db, user, "location_not_shared", detail=(body.reason or "declined")[:120], request=request)
        return {"ok": True, "saved": False}
    user.location_status, user.location_status_at = "shared", now
    user.last_latitude, user.last_longitude = body.latitude, body.longitude
    user.last_location_accuracy, user.last_location_at = body.accuracy, now
    ip = request.client.host if request.client else None
    acc = f"±{body.accuracy:,.0f} m" if body.accuracy is not None else None
    db.add(ActivityLog(user_id=user.id, user_email=user.email, action="sign_in_location",
                       detail=acc, ip=ip, latitude=body.latitude, longitude=body.longitude, accuracy_m=body.accuracy))
    db.commit()
    return {"ok": True, "saved": True}


@router.post("/auth/logout")
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from ..models import utcnow
    user.signed_out_at = utcnow()
    db.commit()
    log_activity(db, user, "logout", request=request)
    return {"ok": True}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/auth/password")
def change_password(body: PasswordChange, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    log_activity(db, user, "password_changed", request=request)
    return {"ok": True}


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, request: Request, admin: User = Depends(require_role("admin")),
                db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "A user with this email already exists")
    user = User(email=email, full_name=body.full_name, role=body.role, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    log_activity(db, admin, "user_created", email, f"role={body.role}", request)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, admin: User = Depends(require_role("admin")),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == admin.id and (body.role not in (None, "admin") or body.is_active is False):
        raise HTTPException(400, "You cannot demote or disable your own account")
    changes = body.model_dump(exclude_unset=True)
    if "password" in changes:
        user.password_hash = hash_password(changes.pop("password"))
    for key, value in changes.items():
        setattr(user, key, value)
    db.commit()
    log_activity(db, admin, "user_updated", user.email, ", ".join(f"{k}={v}" for k, v in changes.items()), request)
    return user


@router.get("/activity", response_model=list[ActivityOut])
def activity(limit: int = 200, action: str | None = None, _: User = Depends(require_role("admin")),
             db: Session = Depends(get_db)):
    stmt = select(ActivityLog).order_by(ActivityLog.id.desc()).limit(min(limit, 1000))
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    return list(db.scalars(stmt))
