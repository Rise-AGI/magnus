# back_end/server/schemas/_auth.py
"""Auth / login schemas."""
from pydantic import BaseModel

from ._user import UserInfo


class FeishuLoginRequest(BaseModel):
    code: str


class TokenLoginRequest(BaseModel):
    token: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo


class TokenResponse(BaseModel):
    magnus_token: str
