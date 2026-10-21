from __future__ import annotations

import calendar
import logging
import math
import os
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from dotenv import load_dotenv
from sqlmodel import select

from database import async_session_factory
from models import Payment, Student

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required in environment variables.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- Button labels ---

BTN_DEBTORS = "📊 Qarzdorlar"
BTN_PAY = "💸 To'lov qilish"
BTN_STUDENTS = "👥 O'quvchilar"
BTN_HISTORY = "🧾 To‘lovlar tarixi"


class PayStates(StatesGroup):
    waiting_student_id = State()
    waiting_amount = State()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_DEBTORS),
                KeyboardButton(text=BTN_PAY),
            ],
            [
                KeyboardButton(text=BTN_STUDENTS),
                KeyboardButton(text=BTN_HISTORY),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Menyudan tanlang…",
    )


def current_month_utc() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def month_range_utc(month: str) -> tuple[datetime, datetime]:
    year_str, month_str = month.split("-")
    year = int(year_str)
    month_num = int(month_str)
    last_day = calendar.monthrange(year, month_num)[1]
    start = datetime(year, month_num, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(year, month_num, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return start, end


def format_money(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")


async def fetch_monthly_balances() -> tuple[
    str,
    list[tuple[Student, float, float]],
    list[tuple[Student, float, float]],
]:
    month = current_month_utc()
    start, end = month_range_utc(month)

    async with async_session_factory() as session:
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

    debtors: list[tuple[Student, float, float]] = []
    overpaid: list[tuple[Student, float, float]] = []
    for student in students:
        if student.id is None:
            continue
        paid = paid_by_student.get(student.id, 0.0)
        difference = round(student.monthly_fee - paid, 2)
        if difference > 0:
            debtors.append((student, paid, difference))
        elif difference < 0:
            overpaid.append((student, paid, -difference))

    return month, debtors, overpaid


async def fetch_student_monthly_paid(session, student_id: int) -> float:
    start, end = month_range_utc(current_month_utc())
    payments = list(
        (
            await session.exec(
                select(Payment).where(
                    Payment.student_id == student_id,
                    Payment.date >= start,
                    Payment.date <= end,
                )
            )
        ).all()
    )
    return sum(payment.amount for payment in payments)


def monthly_balance_line(paid: float, monthly_fee: float) -> str:
    difference = round(paid - monthly_fee, 2)
    if difference > 0:
        return f"Ortiqcha to‘lov: {format_money(difference)} so‘m"
    if difference < 0:
        return f"Qolgan qarz: {format_money(-difference)} so‘m"
    return "✅ Oylik to‘lov to‘liq to‘langan."


async def send_debtors_list(message: Message) -> None:
    try:
        month, debtors, overpaid = await fetch_monthly_balances()
    except Exception:
        logger.exception("Failed to fetch debtors")
        await message.answer(
            "⚠️ Qarzdorlarni yuklab bo‘lmadi.\n"
            "Internet yoki server bilan bog‘liq muammo bo‘lishi mumkin. Keyinroq qayta urinib ko‘ring.",
            reply_markup=main_keyboard(),
        )
        return

    if not debtors and not overpaid:
        await message.answer(
            f"✅ {month} oyida qarzdor ham, ortiqcha to‘lov ham yo‘q.\n"
            "Barcha to‘lovlar joyida!",
            reply_markup=main_keyboard(),
        )
        return

    lines = [f"📊 Oylik balanslar ({month}):", ""]
    if debtors:
        lines.extend(["🔴 Qarzdorlar:", ""])
    for student, paid, debt in debtors:
        lines.append(
            f"#{student.id} — {student.name} | {student.group}\n"
            f"   To‘langan: {format_money(paid)} so‘m\n"
            f"   Qarz: {format_money(debt)} so‘m"
        )
        lines.append("")

    if overpaid:
        lines.extend(["🟢 Ortiqcha to‘lov qilganlar:", ""])
    for student, paid, surplus in overpaid:
        lines.append(
            f"#{student.id} — {student.name} | {student.group}\n"
            f"   Oylik: {format_money(student.monthly_fee)} so‘m\n"
            f"   To‘langan: {format_money(paid)} so‘m\n"
            f"   Ortiqcha: {format_money(surplus)} so‘m"
        )
        lines.append("")

    await message.answer("\n".join(lines).rstrip(), reply_markup=main_keyboard())


async def send_students_list(message: Message) -> None:
    try:
        async with async_session_factory() as session:
            students = list(
                (await session.exec(select(Student).order_by(Student.id))).all()
            )
    except Exception:
        logger.exception("Failed to list students")
        await message.answer(
            "⚠️ O‘quvchilar ro‘yxatini yuklab bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
            reply_markup=main_keyboard(),
        )
        return

    if not students:
        await message.answer(
            "📭 Hozircha o‘quvchi yo‘q.\n"
            "Avval API orqali (POST /students) o‘quvchi qo‘shing.",
            reply_markup=main_keyboard(),
        )
        return

    lines = ["👥 O‘quvchilar ro‘yxati:", ""]
    for s in students:
        lines.append(
            f"#{s.id} — {s.name} | {s.group}\n"
            f"   Oylik: {format_money(s.monthly_fee)} so‘m"
        )
        lines.append("")

    await message.answer("\n".join(lines).rstrip(), reply_markup=main_keyboard())


async def send_payment_history(message: Message) -> None:
    try:
        async with async_session_factory() as session:
            rows = list(
                (
                    await session.exec(
                        select(Payment, Student)
                        .join(Student, Payment.student_id == Student.id)
                        .order_by(Payment.date.desc(), Payment.id.desc())
                    )
                ).all()
            )
    except Exception:
        logger.exception("Failed to fetch payment history")
        await message.answer(
            "⚠️ To‘lovlar tarixini yuklab bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
            reply_markup=main_keyboard(),
        )
        return

    if not rows:
        await message.answer(
            "📭 Hozircha to‘lovlar yo‘q.", reply_markup=main_keyboard()
        )
        return

    tashkent_tz = timezone(timedelta(hours=5))
    entries = []
    for payment, student in rows:
        paid_at = payment.date
        if paid_at.tzinfo is None:
            paid_at = paid_at.replace(tzinfo=timezone.utc)
        paid_at = paid_at.astimezone(tashkent_tz)
        entries.append(
            f"🧾 To‘lov #{payment.id}\n"
            f"Sana: {paid_at:%Y-%m-%d %H:%M} (Toshkent)\n"
            f"O‘quvchi: {student.name} (#{student.id})\n"
            f"Guruh: {student.group}\n"
            f"Telefon: {student.phone}\n"
            f"Oylik to‘lov: {format_money(student.monthly_fee)} so‘m\n"
            f"To‘langan: {format_money(payment.amount)} so‘m"
        )

    # Keep each Telegram message comfortably below its 4096-character limit.
    pages: list[str] = []
    current_page = "🧾 To‘lovlar tarixi:\n\n"
    for entry in entries:
        block = entry + "\n\n"
        if len(current_page) + len(block) > 3800 and current_page.strip():
            pages.append(current_page.rstrip())
            current_page = ""
        current_page += block
    if current_page.strip():
        pages.append(current_page.rstrip())

    total_paid = sum(payment.amount for payment, _ in rows)
    for index, page in enumerate(pages):
        if index == len(pages) - 1:
            page += f"\n\nJami: {format_money(total_paid)} so‘m"
        await message.answer(page, reply_markup=main_keyboard())


# --- Commands & menu ---


@dp.message(Command("start", "menu", "help"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 Cloz Uz — to‘lov tizimi\n\n"
        "Quyidagi tugmalardan foydalaning:\n"
        f"• {BTN_DEBTORS} — shu oy qarzdorlari\n"
        f"• {BTN_PAY} — to‘lov yozish (bosqichma-bosqich)\n"
        f"• {BTN_STUDENTS} — o‘quvchilar va ID lar\n"
        f"• {BTN_HISTORY} — barcha to‘lovlar va tafsilotlari\n\n"
        "Joriy amalni bekor qilish: /cancel\n"
        "Eski usul ham ishlaydi: /debt yoki /pay 1 100",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current is None:
        await message.answer(
            "Bekor qilish uchun ochiq amal yo‘q. Menyudan tanlang.",
            reply_markup=main_keyboard(),
        )
    else:
        await message.answer(
            "✅ Amal bekor qilindi.",
            reply_markup=main_keyboard(),
        )


@dp.message(F.text == BTN_DEBTORS)
@dp.message(Command("debt"))
async def cmd_debt(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_debtors_list(message)


@dp.message(F.text == BTN_STUDENTS)
async def cmd_students(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_students_list(message)


@dp.message(F.text == BTN_HISTORY)
async def cmd_payment_history(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_payment_history(message)


@dp.message(F.text == BTN_PAY)
async def cmd_pay_start(message: Message, state: FSMContext) -> None:
    await state.clear()

    try:
        async with async_session_factory() as session:
            students = list(
                (await session.exec(select(Student).order_by(Student.id))).all()
            )
    except Exception:
        logger.exception("Failed to start payment flow")
        await message.answer(
            "⚠️ To‘lovni boshlab bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
            reply_markup=main_keyboard(),
        )
        return

    if not students:
        await message.answer(
            "📭 O‘quvchi topilmadi.\n"
            "To‘lov yozish uchun avval tizimga o‘quvchi qo‘shing.",
            reply_markup=main_keyboard(),
        )
        return

    preview = "\n".join(
        f"#{s.id} — {s.name} ({s.group})" for s in students[:15]
    )
    more = "" if len(students) <= 15 else f"\n… va yana {len(students) - 15} ta"

    await state.set_state(PayStates.waiting_student_id)
    await message.answer(
        "💸 To‘lov yozish\n\n"
        "Qaysi o‘quvchi uchun?\n"
        "Pastdagi ro‘yxatdan ID raqamini yuboring (faqat raqam).\n\n"
        f"{preview}{more}\n\n"
        "Jarayonni bekor qilish uchun /cancel yuboring.",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(StateFilter(PayStates.waiting_student_id), F.text)
async def process_student_id(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer(
            "❌ Noto‘g‘ri format.\n\n"
            "O‘quvchi ID si butun son bo‘lishi kerak.\n"
            "Masalan: 1\n\n"
            "Qayta yuboring yoki /cancel buyrug‘ini yuboring.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    student_id = int(text)
    if student_id <= 0:
        await message.answer(
            "❌ ID 0 dan katta bo‘lishi kerak.\nMasalan: 1",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    try:
        async with async_session_factory() as session:
            student = await session.get(Student, student_id)
    except Exception:
        logger.exception("DB error while looking up student")
        await message.answer(
            "⚠️ Bazaga ulanishda xatolik. Keyinroq qayta urinib ko‘ring.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if student is None:
        await message.answer(
            f"❌ #{student_id} raqamli o‘quvchi topilmadi.\n\n"
            "«O‘quvchilar» tugmasi orqali to‘g‘ri ID ni tekshiring,\n"
            "yoki boshqa raqam yuboring.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await state.update_data(
        student_id=student.id,
        student_name=student.name,
        student_group=student.group,
    )
    await state.set_state(PayStates.waiting_amount)
    await message.answer(
        f"✅ Topildi: {student.name} (#{student.id}, {student.group})\n\n"
        "Endi to‘lov summasini yuboring (faqat raqam).\n"
        "Masalan: 150000 yoki 150000.50\n\n"
        "Jarayonni bekor qilish uchun /cancel yuboring.",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(StateFilter(PayStates.waiting_amount), F.text)
async def process_amount(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(" ", "").replace(",", ".")

    try:
        amount = float(text)
    except ValueError:
        await message.answer(
            "❌ Summa noto‘g‘ri.\n\n"
            "Faqat musbat raqam yuboring.\n"
            "Masalan: 150000\n\n"
            "Qayta yuboring yoki /cancel buyrug‘ini yuboring.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if amount <= 0 or not math.isfinite(amount):
        await message.answer(
            "❌ Summa 0 dan katta bo‘lishi kerak.\nMasalan: 150000",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    data = await state.get_data()
    student_id = data.get("student_id")
    student_name = data.get("student_name", "")
    student_group = data.get("student_group", "")

    if not student_id:
        await state.clear()
        await message.answer(
            "⚠️ Sessiya eskirgan. Qaytadan «To‘lov qilish»ni bosing.",
            reply_markup=main_keyboard(),
        )
        return

    try:
        async with async_session_factory() as session:
            student = await session.get(Student, student_id)
            if student is None:
                await state.clear()
                await message.answer(
                    f"❌ #{student_id} o‘quvchi endi bazada yo‘q.\n"
                    "Ro‘yxatni yangilang va qayta urinib ko‘ring.",
                    reply_markup=main_keyboard(),
                )
                return

            payment = Payment(
                student_id=student_id,
                amount=amount,
                date=datetime.now(timezone.utc),
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)
            payment_id = payment.id
            student_name = student.name
            student_group = student.group
            monthly_fee = student.monthly_fee
            monthly_paid = await fetch_student_monthly_paid(session, student_id)
    except Exception:
        logger.exception("Failed to create payment")
        await message.answer(
            "⚠️ To‘lovni saqlab bo‘lmadi.\n"
            "Server xatosi. Bir ozdan keyin qayta urinib ko‘ring.\n"
            "Agar xato takrorlansa, summani tekshiring.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await state.clear()
    await message.answer(
        "✅ To‘lov saqlandi!\n\n"
        f"O‘quvchi: {student_name} (#{student_id})\n"
        f"Guruh: {student_group}\n"
        f"Summa: {format_money(amount)} so‘m\n"
        f"Joriy oy jami: {format_money(monthly_paid)} so‘m\n"
        f"{monthly_balance_line(monthly_paid, monthly_fee)}\n"
        f"To‘lov ID: #{payment_id}",
        reply_markup=main_keyboard(),
    )


# Keep slash shortcut for power users: /pay 1 100
@dp.message(Command("pay"), StateFilter(None))
async def cmd_pay_shortcut(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    parts = text.split()
    # /pay or /pay@bot
    args = parts[1:] if len(parts) > 1 else []

    if len(args) < 2:
        await cmd_pay_start(message, state)
        return

    if not args[0].isdigit():
        await message.answer(
            "❌ Noto‘g‘ri ID.\n\n"
            "To‘g‘ri format: /pay 1 150000\n"
            "Yoki menyudan «💸 To‘lov qilish» tugmasini bosing — osonroq.",
            reply_markup=main_keyboard(),
        )
        return

    student_id = int(args[0])
    amount_raw = args[1].replace(" ", "").replace(",", ".")
    try:
        amount = float(amount_raw)
    except ValueError:
        await message.answer(
            "❌ Summa noto‘g‘ri.\n\n"
            "To‘g‘ri format: /pay 1 150000\n"
            "Yoki «💸 To‘lov qilish» tugmasidan foydalaning.",
            reply_markup=main_keyboard(),
        )
        return

    if student_id <= 0 or amount <= 0 or not math.isfinite(amount):
        await message.answer(
            "❌ ID va summa musbat bo‘lishi kerak.\nMasalan: /pay 1 150000",
            reply_markup=main_keyboard(),
        )
        return

    try:
        async with async_session_factory() as session:
            student = await session.get(Student, student_id)
            if student is None:
                await message.answer(
                    f"❌ #{student_id} raqamli o‘quvchi topilmadi.\n"
                    "«👥 O‘quvchilar» tugmasi orqali ID ni tekshiring.",
                    reply_markup=main_keyboard(),
                )
                return

            payment = Payment(
                student_id=student_id,
                amount=amount,
                date=datetime.now(timezone.utc),
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)
            monthly_paid = await fetch_student_monthly_paid(session, student_id)

            await message.answer(
                "✅ To‘lov saqlandi!\n\n"
                f"O‘quvchi: {student.name} (#{student.id})\n"
                f"Guruh: {student.group}\n"
                f"Summa: {format_money(amount)} so‘m\n"
                f"Joriy oy jami: {format_money(monthly_paid)} so‘m\n"
                f"{monthly_balance_line(monthly_paid, student.monthly_fee)}\n"
                f"To‘lov ID: #{payment.id}",
                reply_markup=main_keyboard(),
            )
    except Exception:
        logger.exception("/pay shortcut failed")
        await message.answer(
            "⚠️ To‘lovni saqlab bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
            reply_markup=main_keyboard(),
        )


@dp.message(StateFilter(PayStates.waiting_student_id))
@dp.message(StateFilter(PayStates.waiting_amount))
async def pay_flow_unexpected(message: Message) -> None:
    await message.answer(
        "❌ Kutilgan formatda emas.\n\n"
        "Matn yoki raqam yuboring. Jarayonni bekor qilish uchun /cancel yuboring.",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message()
async def fallback(message: Message) -> None:
    await message.answer(
        "Tushunmadim 🤔\nMenyudagi tugmalardan birini bosing.",
        reply_markup=main_keyboard(),
    )
