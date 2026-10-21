from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Student(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phone: str
    group: str
    monthly_fee: float
    created_at: datetime = Field(default_factory=utc_now)

    payments: List["Payment"] = Relationship(back_populates="student")


class Payment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    amount: float
    date: datetime = Field(default_factory=utc_now)

    student: Optional["Student"] = Relationship(back_populates="payments")
