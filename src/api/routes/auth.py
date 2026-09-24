from fastapi import APIRouter, Depends

from src.api.dependencies import current_user
from src.api.schemas import LoginRequest
from src.memory import store
from src.security.auth import User, authenticate, create_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest):
    user = authenticate(body.username, body.password)
    await store.audit(user.username, "login", {})
    return {"access_token": create_token(user), "token_type": "bearer", "user": user.public()}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return user.public()
