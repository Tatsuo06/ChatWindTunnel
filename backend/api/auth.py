from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from backend.api.deps import DB, AdminUser, CurrentUser
from backend.core.security import create_access_token, hash_password, verify_password
from backend.db.models import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    username: str
    password: str
    role: UserRole = UserRole.user


class UserResponse(BaseModel):
    id: int
    username: str
    role: UserRole

    model_config = {"from_attributes": True}


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DB):
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(user.username)
    return TokenResponse(access_token=token)


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, _: AdminUser, db: DB):
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    user = User(username=body.username, hashed_password=hash_password(body.password), role=body.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/users", response_model=list[UserResponse])
async def list_users(_: AdminUser, db: DB):
    result = await db.execute(select(User))
    return result.scalars().all()


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, admin: AdminUser, db: DB):
    if admin.id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await db.delete(user)
    await db.commit()


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUser):
    return current_user


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordChangeRequest, current_user: CurrentUser, db: DB):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    current_user.hashed_password = hash_password(body.new_password)
    await db.commit()
