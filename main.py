import os
import time
import sqlite3
import hashlib
import dropbox
import httpx
import pytz
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel

from fastapi import FastAPI, Depends, HTTPException, Response, Cookie, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ================= ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ =================

app = FastAPI(title="Photo Rating API", version="2.0")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("templates"):
    app.mount("/templates", StaticFiles(directory="templates"), name="templates")

# --- Константы окружения ---
DROPBOX_REFRESH_TOKEN = "ApXJY9sYu1MAAAAAAAAAAYk7D9NmgMi88qboNhpKNSGsh1conF6E4kBJicP4Web6"
DROPBOX_APP_KEY = "oou4gf2ktj2y51j"
DROPBOX_APP_SECRET = "dunglx7xl3el8pa"
PLISIO_API_TOKEN = "u1JWmqyQBwnA6kuvp1PbOl5UKvt4a2i9oIk5CzD5GfiyThtj9RcYPsg2nroOgzsu"

DB_LOCAL_PATH = "database.db"
DB_DROPBOX_PATH = "/database.db"
TIMEZONE = pytz.timezone("Europe/Kiev")

# ================= ИНТЕГРАЦИЯ DROPBOX =================

def get_dropbox_client() -> Optional[dropbox.Dropbox]:
    try:
        return dropbox.Dropbox(
            oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
            app_key=DROPBOX_APP_KEY,
            app_secret=DROPBOX_APP_SECRET
        )
    except Exception as e:
        print(f"[Dropbox] Ошибка инициализации клиента: {e}")
        return None

def download_db_from_dropbox():
    dbx = get_dropbox_client()
    if not dbx:
        return
    try:
        print("[Dropbox] Скачивание базы данных...")
        _, res = dbx.files_download(path=DB_DROPBOX_PATH)
        with open(DB_LOCAL_PATH, "wb") as f:
            f.write(res.content)
        print("[Dropbox] База данных успешно синхронизирована локально!")
    except dropbox.exceptions.ApiError:
        print("[Dropbox] Файл в облаке не найден. Будет создана новая локальная БД.")
    except Exception as e:
        print(f"[Dropbox] Ошибка скачивания: {e}")

def upload_db_to_dropbox():
    dbx = get_dropbox_client()
    if not dbx or not os.path.exists(DB_LOCAL_PATH):
        return
    try:
        print("[Dropbox] Выгрузка резервной копии...")
        with open(DB_LOCAL_PATH, "rb") as f:
            db_bytes = f.read()
        dbx.files_upload(db_bytes, path=DB_DROPBOX_PATH, mode=dropbox.files.WriteMode.overwrite)
        print("[Dropbox] База успешно сохранена в облаке!")
    except Exception as e:
        print(f"[Dropbox] Ошибка выгрузки: {e}")

# Первичный запуск синхронизации
download_db_from_dropbox()

# ================= РАБОТА С БАЗОЙ ДАННЫХ =================

def get_db():
    db = sqlite3.connect(DB_LOCAL_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()

def init_db():
    with sqlite3.connect(DB_LOCAL_PATH) as db:
        cursor = db.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS contestants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            photo_url TEXT NOT NULL,
            votes_count INTEGER DEFAULT 0
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            balance REAL DEFAULT 0.0,
            is_admin INTEGER DEFAULT 0,
            last_bonus_date TEXT DEFAULT NULL,
            secret_answer TEXT DEFAULT NULL
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            title_date TEXT NOT NULL,
            photo_url TEXT NOT NULL
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            history_id INTEGER NOT NULL,
            photo_url TEXT NOT NULL,
            FOREIGN KEY (history_id) REFERENCES history(id) ON DELETE CASCADE
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_balances (
            user_ip TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 0
        )""")

        # Миграции (накатывание колонок при их отсутствии)
        for column in [("last_bonus_date", "TEXT DEFAULT NULL"), ("secret_answer", "TEXT DEFAULT NULL")]:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {column[0]} {column[1]}")
            except sqlite3.OperationalError:
                pass
            
        # Инициализация суперадмина
        admin_password_hash = hashlib.sha256("admin".encode()).hexdigest()
        cursor.execute("SELECT id FROM users WHERE username = 'admin'")
        if cursor.fetchone():
            cursor.execute("UPDATE users SET password = ? WHERE username = 'admin'", (admin_password_hash,))
        else:
            cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES ('admin', ?, 500.0, 1)", (admin_password_hash,))
        
        db.commit()
    upload_db_to_dropbox()

init_db()

# ================= СХЕМЫ ДАННЫХ PYDANTIC =================

class UserAuthSchema(BaseModel):
    username: str
    password: str
    secret_answer: Optional[str] = None

class ContestantSchema(BaseModel):
    name: str
    file_base64: str

class AlbumFileSchema(BaseModel):
    file_base64: str
    filename: Optional[str] = ""
    content_type: Optional[str] = ""

class HistorySchema(BaseModel):
    name: str
    title_date: str
    file_base64: str
    filename: Optional[str] = None
    content_type: Optional[str] = None
    album_files: Optional[List[AlbumFileSchema]] = None

class ResetPasswordSchema(BaseModel):
    username: str
    secret_answer: str
    new_password: str

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_current_round_deadline() -> datetime:
    """Рассчитывает дедлайн текущего раунда (Воскресенье, 23:59:59) по Киевскому/Московскому времени"""
    now = datetime.now(TIMEZONE)
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and (now.hour > 23 or (now.hour == 23 and now.minute >= 59)):
        days_until_sunday = 7
    deadline = now + timedelta(days=days_until_sunday)
    return deadline.replace(hour=23, minute=59, second=59, microsecond=0)

# ================= РОУТЫ СТРАНИЦ (ФРОНТЕНД) =================

def render_template(path: str) -> HTMLResponse:
    if not os.path.exists(path):
        return HTMLResponse(content=f"<h1>Файл {os.path.basename(path)} не найден!</h1>", status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f"read")

@app.get("/", response_class=HTMLResponse)
async def get_main_page():
    return render_template("templates/index.html")

@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
async def get_admin_page(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        
    return render_template("templates/admin.html")

@app.get("/history", response_class=HTMLResponse)
@app.get("/history/", response_class=HTMLResponse)
async def get_history_page():
    return render_template("templates/history.html")

@app.get("/terms", response_class=HTMLResponse)
async def get_terms():
    return render_template("templates/terms.html")

@app.get("/privacy", response_class=HTMLResponse)
async def get_privacy():
    return render_template("templates/privacy.html")

@app.get("/refund", response_class=HTMLResponse)
async def get_refund():
    return render_template("templates/refund.html")


# ================= АВТОРИЗАЦИЯ И ПОЛЬЗОВАТЕЛИ =================

@app.get("/api/contest-status")
async def get_contest_status():
    """Эндпоинт для фронтенд-таймера"""
    deadline = get_current_round_deadline()
    return {
        "deadline_iso": deadline.isoformat(),
        "is_active": datetime.now(TIMEZONE) < deadline
    }

@app.get("/api/me")
async def api_me(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        return {"username": "Гость", "balance": 0, "is_admin": 0}
    cursor = db.cursor()
    cursor.execute("SELECT username, balance, is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    return dict(user) if user else {"username": "Гость", "balance": 0, "is_admin": 0}

@app.post("/api/register")
async def api_register(data: UserAuthSchema, db=Depends(get_db)):
    username = data.username.strip()
    password = data.password.strip()
    secret_answer = data.secret_answer.strip() if data.secret_answer else ""
    
    if len(username) < 3 or len(password) < 4:
        raise HTTPException(status_code=400, detail="Слишком короткое имя или пароль")
    if not secret_answer:
        raise HTTPException(status_code=400, detail="Укажите секретное слово для восстановления")
        
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
        
    password_hash = hash_password(password)
    answer_hash = hash_password(secret_answer.lower())
    
    cursor.execute(
        "INSERT INTO users (username, password, balance, is_admin, secret_answer) VALUES (?, ?, 0.0, 0, ?)", 
        (username, password_hash, answer_hash)
    )
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": "Регистрация успешна!"}
        
@app.post("/api/login")
@limiter.limit("5 per minute")
async def api_login(request: Request, data: UserAuthSchema, response: Response, db=Depends(get_db)):
    username = data.username.strip()
    password = data.password.strip()
    
    cursor = db.cursor()
    cursor.execute("SELECT password, is_admin FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    
    if not user or hash_password(password) != user["password"]:
        raise HTTPException(status_code=400, detail="Неверное имя пользователя или пароль")
        
    response.set_cookie(key="session_user", value=username, max_age=86400, httponly=True)
    return {"status": "success", "message": "Вход выполнен успешно!", "is_admin": user["is_admin"]}

@app.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie(key="session_user", path="/")
    return {"status": "success", "message": "Вы вышли"}

@app.post("/api/deposit")
async def api_deposit(amount_data: dict, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    amount = float(amount_data.get("amount", 100.0))
    cursor = db.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE username = ?", (amount, session_user))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "balance_added": amount}

@app.post("/api/reset-password")
@limiter.limit("3 per minute")
async def api_reset_password(request: Request, data: ResetPasswordSchema, db=Depends(get_db)):
    username = data.username.strip()
    secret_answer = data.secret_answer.strip().lower()
    new_password = data.new_password.strip()
    
    if len(new_password) < 4:
        raise HTTPException(status_code=400, detail="Новый пароль слишком короткий")
        
    cursor = db.cursor()
    cursor.execute("SELECT secret_answer FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    
    if not user or not user["secret_answer"]:
        raise HTTPException(status_code=404, detail="Пользователь не найден или функция восстановления недоступна")
        
    if hash_password(secret_answer) != user["secret_answer"]:
        raise HTTPException(status_code=400, detail="Неверное секретное слово!")
        
    cursor.execute("UPDATE users SET password = ? WHERE username = ?", (hash_password(new_password), username))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": "Пароль изменен!"}

@app.get("/api/balance")
async def get_balance(request: Request, db=Depends(get_db)):
    user_ip = request.client.host
    cursor = db.cursor()
    cursor.execute("SELECT balance FROM user_balances WHERE user_ip = ?", (user_ip,))
    row = cursor.fetchone()
    
    if not row:
        cursor.execute("INSERT INTO user_balances (user_ip, balance) VALUES (?, 0)", (user_ip,))
        db.commit()
        return {"balance": 0}
        
    return {"balance": row["balance"]}


# ================= РАБОТА С УЧАСТНИЦАМИ И ГОЛОСОВАНИЕ =================

@app.get("/api/contestants")
async def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM contestants ORDER BY votes_count DESC")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/vote")
async def api_vote(contestant_id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Нужно войти в аккаунт")
        
    # БЕЗОПАСНОСТЬ: Жесткая проверка дедлайна раунда перед фиксацией голоса
    if datetime.now(TIMEZONE) > get_current_round_deadline():
        raise HTTPException(status_code=400, detail="Раунд завершен! Голоса больше не принимаются.")

    cursor = db.cursor()
    cursor.execute("SELECT balance FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    
    if user and user["balance"] >= 10:
        cursor.execute("UPDATE users SET balance = balance - 10 WHERE username = ?", (session_user,))
        cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success"}
        
    raise HTTPException(status_code=400, detail="Недостаточно коинов на балансе")

@app.post("/api/admin/contestants")
async def admin_add_contestant(data: ContestantSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user: raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
    cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (data.name, inline_photo_url))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}

@app.delete("/api/admin/contestants/{id}")
async def delete_contestant(id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user: raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    cursor.execute("DELETE FROM contestants WHERE id = ?", (id,))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}

@app.put("/api/admin/contestants/{id}")
async def admin_edit_contestant(id: int, data: ContestantSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user: raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
    if len(inline_photo_url) < 50: 
        cursor.execute("UPDATE contestants SET name = ? WHERE id = ?", (data.name, id))
    else:
        cursor.execute("UPDATE contestants SET name = ?, photo_url = ? WHERE id = ?", (data.name, inline_photo_url, id))
        
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": "Данные изменены!"}


# ================= РАБОТА С ЗАЛОМ СЛАВЫ =================

@app.get("/api/history")
async def get_api_history(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM history ORDER BY id DESC")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/admin/history")
async def admin_add_history(data: HistorySchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user: raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
    cursor.execute("INSERT INTO history (name, title_date, photo_url) VALUES (?, ?, ?)", (data.name, data.title_date, inline_photo_url))
    inserted_id = cursor.lastrowid
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "history_id": inserted_id}

@app.post("/api/admin/history/{history_id}/upload-photo")
async def admin_upload_album_photo(history_id: int, data: AlbumFileSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user: raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
        
    file_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
    if not file_url:
        return {"status": "error", "message": "Пустая строка Base64"}
        
    cursor.execute("INSERT INTO history_photos (history_id, photo_url) VALUES (?, ?)", (history_id, file_url))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": "Фото добавлено!"}

@app.get("/api/history/{winner_id}/photos")
async def get_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT photo_url FROM history_photos WHERE history_id = ?", (winner_id,))
    return [row["photo_url"] for row in cursor.fetchall()]

@app.delete("/api/admin/history/{id}")
async def delete_history_item(id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user: raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    cursor.execute("DELETE FROM history_photos WHERE history_id = ?", (id,))
    cursor.execute("DELETE FROM history WHERE id = ?", (id,))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}


# ================= ПЛАТЕЖИ PLISIO И БОНУСЫ =================

@app.post("/api/payment/create")
async def create_plisio_invoice(request: Request, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Пользователь не авторизован")
        
    content_type = request.headers.get("content-type", "")
    body_data = {}
    if "application/json" in content_type:
        try: body_data = await request.json()
        except Exception: pass
    else:
        try: body_data = dict(await request.form())
        except Exception: pass

    coins_amount = body_data.get("amount") or request.query_params.get("amount")
    if not coins_amount:
        raise HTTPException(status_code=400, detail="Не указана сумма перевода")

    amount_usd = float(coins_amount) / 100.0
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (session_user,))
    user_row = cursor.fetchone()
    
    if not user_row:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
        
    order_id = f"order_{user_row['id']}_{int(time.time())}" 
    
    params = {
        "api_key": PLISIO_API_TOKEN,
        "source_currency": "USD",       
        "source_amount": f"{amount_usd:.2f}",
        "order_number": order_id,        
        "order_name": f"Пополнение коинов ({coins_amount} шт.) для {session_user}",
        "callback_url": "https://www.photo-rating.club/api/payment/plisio-callback"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get("https://plisio.net/api/v1/invoices/new", params=params)
        res_data = response.json()
    
    if res_data.get("status") == "success":
        return {"payment_url": res_data["data"]["invoice_url"]}
    raise HTTPException(status_code=400, detail="Ошибка создания инвойса в Plisio")

@app.post("/api/bonus")
async def claim_daily_bonus(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Нужно войти в аккаунт")
        
    cursor = db.cursor()
    cursor.execute("SELECT balance, last_bonus_date FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    
    if not user: raise HTTPException(status_code=404, detail="Пользователь не найден")
        
    now = datetime.now()
    if user["last_bonus_date"]:
        last_bonus_time = datetime.fromisoformat(user["last_bonus_date"])
        if now - last_bonus_time < timedelta(hours=24):
            time_left = timedelta(hours=24) - (now - last_bonus_time)
            raise HTTPException(
                status_code=400, 
                detail=f"До следующего бонуса осталось: {int(time_left.total_seconds() // 3600)}ч {int((time_left.total_seconds() % 3600) // 60)}м"
            )
            
    cursor.execute("UPDATE users SET balance = balance + 5.0, last_bonus_date = ? WHERE username = ?", (now.isoformat(), session_user))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": "Вы получили 5 бесплатных коинов!"}

@app.api_route("/api/payment/plisio-callback", methods=["GET", "POST", "PUT", "OPTIONS"])
async def plisio_callback(request: Request):
    if request.method in ["GET", "OPTIONS"]:
        return JSONResponse(content={"status": "ok"})
        
    try:
        content_type = request.headers.get("content-type", "")
        data = await request.json() if "application/json" in content_type else await request.form()
            
        if data.get("status") == "completed":
            order_id = data.get("order_number")
            if order_id and order_id.startswith("order_"):
                parts = order_id.split("_")
                if len(parts) >= 2:
                    user_id = int(parts[1])
                    amount_usd = float(data.get("source_amount") or data.get("amount") or 0.0)
                    coins_to_add = amount_usd * 100.0
                    
                    with sqlite3.connect(DB_LOCAL_PATH) as db:
                        cursor = db.cursor()
                        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (coins_to_add, user_id))
                        db.commit()
                        
                    upload_db_to_dropbox()
                    print(f"[Billing] ID {user_id} получил {coins_to_add} коинов.")
            return Response(content="OK", media_type="text/plain")
            
        return Response(content="Ignored", media_type="text/plain")
    except Exception as e:
        print(f"[Billing Error] Ошибка вебхука Plisio: {e}")
        return JSONResponse(status_code=200, content={"status": "error_logged"})
