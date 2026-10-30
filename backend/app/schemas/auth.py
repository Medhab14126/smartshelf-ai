import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.business import BusinessOut


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    business_name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="UTC", max_length=64)

    @field_validator("full_name", "business_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if value.isdigit() or value.isalpha():
            raise ValueError("password must contain both letters and numbers or symbols")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str


class MeResponse(BaseModel):
    user: UserOut
    business: BusinessOut
    role: str


class LoginResponse(MeResponse):
    # Returned for API clients (Swagger, scripts). The browser app relies on
    # the HttpOnly cookie and does not store this token.
    access_token: str
    token_type: str = "bearer"  # noqa: S105
