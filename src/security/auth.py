"""
Authentication - Option A (hardcoded users in config/roles.yaml) + signed JWT.

Login returns a JWT. Every API call sends it as "Authorization: Bearer <token>".
The role inside the token is only trusted because WE signed it - the LLM never
decides who the user is.
"""
import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import jwt

from config.settings import load_yaml, settings
from src.utils.errors import AuthError


@dataclass
class User:
    username: str
    full_name: str
    role: str
    department: str
    tools: list[str] = field(default_factory=list)
    access_levels: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return {"username": self.username, "full_name": self.full_name, "role": self.role,
                "department": self.department, "tools": self.tools, "access_levels": self.access_levels}


def _verify_password(password: str, stored: str) -> bool:
    _, salt, expected = stored.split("$")
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return hmac.compare_digest(got, expected)  # constant time compare


def get_user(username: str) -> User:
    cfg = load_yaml("roles.yaml")
    u = cfg["users"].get(username)
    if not u:
        raise AuthError("unknown user")
    role_cfg = cfg["roles"][u["role"]]
    return User(username=username, full_name=u["full_name"], role=u["role"], department=u["department"],
                tools=role_cfg["tools"], access_levels=role_cfg["access_levels"])


def authenticate(username: str, password: str) -> User:
    cfg = load_yaml("roles.yaml")
    u = cfg["users"].get((username or "").strip().lower())
    if not u or not _verify_password(password or "", u["password_hash"]):
        raise AuthError("invalid username or password")
    return get_user(username.strip().lower())


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user.username, "role": user.role, "iat": now,
               "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> User:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid token: {e}") from e
    user = get_user(payload["sub"])
    if user.role != payload.get("role"):  # role changed in config after login -> force re-login
        raise AuthError("role changed, please log in again")
    return user
