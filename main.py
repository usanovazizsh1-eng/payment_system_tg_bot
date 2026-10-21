from __future__ import annotations

import asyncio
import calendar
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_session, init_db
from models import Payment, Student

logger = logging.getLogger(__name__)


# --- Schemas ---


class StudentCreate(BaseModel):
    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    group: str = Field(min_length=1)
    monthly_fee: float = Field(ge=0)


class StudentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    phone: Optional[str] = Field(default=None, min_length=1)
    group: Optional[str] = Field(default=None, min_length=1)
    monthly_fee: Optional[float] = Field(default=None, ge=0)


class StudentRead(BaseModel):
    id: int
    name: str
    phone: str
    group: str
    monthly_fee: float
    created_at: datetime

    model_config = {"from_attributes": True}


class PaymentCreate(BaseModel):
    student_id: int = Field(gt=0)
    amount: float = Field(gt=0)
    date: Optional[datetime] = None


class PaymentRead(BaseModel):
    id: int
    student_id: int
    amount: float
    date: datetime

    model_config = {"from_attributes": True}


class DebtorOut(BaseModel):
    id: int
    name: str
    group: str
    monthly_fee: float
    paid_amount: float
    debt: float


# --- Helpers ---

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def parse_month_range(month: str) -> tuple[datetime, datetime]:
    """
    Parse YYYY-MM into an inclusive UTC range:
    start = YYYY-MM-01 00:00:00.000000
    end   = last day of month 23:59:59.999999
    """
    match = _MONTH_RE.fullmatch(month)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid month format. Expected YYYY-MM.",
        )

    year = int(match.group(1))
    month_num = int(match.group(2))
    if month_num < 1 or month_num > 12:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Month must be between 01 and 12.",
        )

    last_day = calendar.monthrange(year, month_num)[1]
    start = datetime(year, month_num, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(year, month_num, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return start, end


async def get_student_or_404(session: AsyncSession, student_id: int) -> Student:
    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found.",
        )
    return student


# --- App ---


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Import here so missing BOT_TOKEN does not block API-only imports/tests.
    from bot import bot, dp

    await init_db()

    await bot.delete_webhook(drop_pending_updates=True)
    polling_task = asyncio.create_task(
        dp.start_polling(bot),
        name="aiogram-polling",
    )
    logger.info("Aiogram bot polling started")

    try:
        yield
    finally:
        await dp.stop_polling()
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("Aiogram bot polling stopped")


app = FastAPI(title="Cloz Uz Payments", version="1.0.0", lifespan=lifespan)


# --- Students CRUD ---


@app.post("/students", response_model=StudentRead, status_code=status.HTTP_201_CREATED)
async def create_student(
    payload: StudentCreate,
    session: AsyncSession = Depends(get_session),
) -> Student:
    student = Student(
        name=payload.name.strip(),
        phone=payload.phone.strip(),
        group=payload.group.strip(),
        monthly_fee=payload.monthly_fee,
    )
    session.add(student)
    await session.commit()
    await session.refresh(student)
    return student


@app.get("/students", response_model=list[StudentRead])
async def list_students(
    session: AsyncSession = Depends(get_session),
) -> list[Student]:
    result = await session.exec(select(Student).order_by(Student.id))
    return list(result.all())


@app.put("/students/{student_id}", response_model=StudentRead)
async def update_student(
    student_id: int,
    payload: StudentUpdate,
    session: AsyncSession = Depends(get_session),
) -> Student:
    student = await get_student_or_404(session, student_id)
    data = payload.model_dump(exclude_unset=True)

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one field to update.",
        )

    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(student, key, value)

    session.add(student)
    await session.commit()
    await session.refresh(student)
    return student


@app.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_student(
    student_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    student = await get_student_or_404(session, student_id)

    payments = await session.exec(
        select(Payment).where(Payment.student_id == student_id)
    )
    for payment in payments.all():
        await session.delete(payment)

    await session.delete(student)
    await session.commit()


# --- Payments ---


@app.post("/payments", response_model=PaymentRead, status_code=status.HTTP_201_CREATED)
async def create_payment(
    payload: PaymentCreate,
    session: AsyncSession = Depends(get_session),
) -> Payment:
    await get_student_or_404(session, payload.student_id)

    payment = Payment(
        student_id=payload.student_id,
        amount=payload.amount,
        date=payload.date if payload.date is not None else datetime.now(timezone.utc),
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


@app.get("/students/{student_id}/payments", response_model=list[PaymentRead])
async def list_student_payments(
    student_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Payment]:
    await get_student_or_404(session, student_id)

    result = await session.exec(
        select(Payment)
        .where(Payment.student_id == student_id)
        .order_by(Payment.date.desc())
    )
    return list(result.all())


# --- Debtors ---


@app.get("/debtors", response_model=list[DebtorOut])
async def list_debtors(
    month: str = Query(..., description="Billing month in YYYY-MM format"),
    session: AsyncSession = Depends(get_session),
) -> list[DebtorOut]:
    start, end = parse_month_range(month)

    students = list((await session.exec(select(Student).order_by(Student.id))).all())
    payments = list(
        (
            await session.exec(
                select(Payment).where(
                    Payment.date >= start,
                    Payment.date <= end,
                )
            )
        ).all()
    )

    paid_by_student: dict[int, float] = {}
    for payment in payments:
        paid_by_student[payment.student_id] = (
            paid_by_student.get(payment.student_id, 0.0) + payment.amount
        )

    debtors: list[DebtorOut] = []
    for student in students:
        if student.id is None:
            continue
        paid_amount = paid_by_student.get(student.id, 0.0)
        if paid_amount < student.monthly_fee:
            debtors.append(
                DebtorOut(
                    id=student.id,
                    name=student.name,
                    group=student.group,
                    monthly_fee=student.monthly_fee,
                    paid_amount=paid_amount,
                    debt=student.monthly_fee - paid_amount,
                )
            )

    return debtors
