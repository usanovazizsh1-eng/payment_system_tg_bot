# Cloz Uz — Mini Payment System (Python)

Async mini-payment tracker for student monthly fees: **FastAPI** REST API, **SQLModel** + **SQLite** (`aiosqlite`), and a **Telegram bot** (**aiogram** v3).

## Prerequisites

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup (from scratch)

```bash
# 1. Clone / open the project
cd cloz_uz

# 2. Create and activate a virtual environment
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# macOS / Linux
# source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
# Create a file named .env in the project root:
```

```env
BOT_TOKEN=your-telegram-bot-token-from-BotFather
# Optional — defaults to sqlite+aiosqlite:///./payments.db
# DATABASE_URL=sqlite+aiosqlite:///./payments.db
```

```bash
# 5. Run FastAPI + Telegram bot (both start via lifespan)
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open interactive docs at `http://127.0.0.1:8000/docs`.

Tables are created automatically on startup (`init_db()`).

## API overview

| Method | Endpoint                         | Description                       |
|--------|----------------------------------|-----------------------------------|
| POST   | `/students`                      | Create student                    |
| GET    | `/students`                      | List students                     |
| PUT    | `/students/{student_id}`         | Update student                    |
| DELETE | `/students/{student_id}`         | Delete student (+ payments)       |
| POST   | `/payments`                      | Record payment                    |
| GET    | `/students/{student_id}/payments`| List payments for a student       |
| GET    | `/debtors?month=YYYY-MM`         | Students who underpaid that month |

## Telegram bot

| Command              | Description                           |
|----------------------|---------------------------------------|
| `/debt`              | Current-month debtors (Markdown)      |
| `/pay <id> <amount>` | Record a payment for student `id`     |

## Future Improvements

- Strengthen request/response contracts with dedicated Pydantic schemas and stricter field validators across all routes.
- Introduce Alembic migrations instead of `create_all` so schema changes stay versioned and reversible.
- Protect the REST API with JWT authentication and restrict bot commands to an allow-listed set of admin Telegram IDs.
- Schedule automated monthly reminders with APScheduler (or similar) to notify admins/debtors at billing time.
- Add unit and API tests with pytest (and httpx/AsyncClient) covering debt calculation and month-boundary edge cases.

## Итоги работы с ИИ (кратко)

Пошаговые промпты (модели и БД → FastAPI CRUD/должники → aiogram-бот + lifespan → README) дали лучший результат: каждый слой опирался на уже готовый контекст. Ошибка ИИ часто связана с датами: смешение timezone-aware и naive `datetime` при фильтрации в SQLite и нечёткие границы месяца ломали суммы оплат; это исправили единым UTC-диапазоном (`01 00:00:00` — последний день `23:59:59.999999`). В боте v3 важно парсить аргументы через `CommandObject.args`, а не «сырой» текст сообщения. Почти весь каркас (`database.py`, `models.py`, `main.py`, `bot.py`) сгенерирован ИИ; вручную обычно настраивают `.env`, venv, токен BotFather и проверяют `/debtors` и команды в Telegram. Короткие промпты с явными контрактами API и edge cases работают надёжнее одного запроса «сделай весь проект сразу».
