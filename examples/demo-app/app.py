"""A small FastAPI app written the way many 2023 tutorials wrote it."""

from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()
app = FastAPI()


class UserRow(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String)


class User(BaseModel):
    id: int
    email: str

    class Config:
        orm_mode = True


@app.on_event("startup")
def startup() -> None:
    app.state.started_at = datetime.utcnow()


@app.get("/users/{user_id}")
def get_user(user_id: int) -> dict:
    user = User.parse_obj({"id": user_id, "email": "amal@example.com"})
    return user.dict()
