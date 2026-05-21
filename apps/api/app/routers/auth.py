from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import User
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserInfo
from app.services.crypto import (
    create_token,
    hash_password,
    verify_password,
    verify_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user_id = verify_token(authorization.removeprefix("Bearer "))
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    body: RegisterRequest, session: AsyncSession = Depends(get_session)
):
    existing = await session.scalar(select(User).where(User.email == body.email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = User(email=body.email, password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    return TokenResponse(
        token=create_token(user.id),
        user=UserInfo(id=user.id, email=user.email, role=user.role),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid email or password"
        )
    return TokenResponse(
        token=create_token(user.id),
        user=UserInfo(id=user.id, email=user.email, role=user.role),
    )


@router.get("/me", response_model=UserInfo)
async def me(user: User = Depends(current_user)):
    return user


@router.get("/required")
async def auth_required(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Frontend bootstrap — tells the UI whether to show a login screen."""
    user: User | None = None
    if authorization and authorization.startswith("Bearer "):
        user_id = verify_token(authorization.removeprefix("Bearer "))
        if user_id is not None:
            user = await session.get(User, user_id)
    return {
        "auth_required": settings.auth_required,
        "signed_in": user is not None,
        "user": UserInfo.model_validate(user) if user is not None else None,
    }
