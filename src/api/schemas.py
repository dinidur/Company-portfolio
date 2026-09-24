from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ResumeRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    approved: bool


class FeedbackRequest(BaseModel):
    run_id: str = Field(min_length=8, max_length=64)
    score: int = Field(ge=0, le=1)  # 1 = thumbs up, 0 = thumbs down
    comment: str = Field(default="", max_length=1000)
