import os
import hashlib
import sqlite3
import uuid
import requests
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Cookie, Response, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional
from starlette.staticfiles import StaticFiles

# --- БЕЗОПАСНАЯ ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ПРИ СТАРТЕ ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Этот код выполнится строго 1 раз при запуске веб-сервера
    init_db()
    yield

# Передаем lifespan в FastAPI
app = FastAPI(title="Photo Voting Platform v2", lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_path = os.path.join(BASE_DIR, "static")
if not os.path.exists(static_path):
    os.makedirs(static_path)
app.mount("/static", StaticFiles(directory=static_path), name="static")

# НАСТРОЙКИ BETATRANSFER
BETATRANSFER_PROJECT_ID = "97225043ff25d8830c407802569e15c11e73138251142406cfcf9640f3108a20"
BETATRANSFER_API_KEY = "5b914ced89d02da9aad41058383c4e100cdbfa86a0c49f05e656fe4f4e53437a"
BETATRANSFER_URL = "https://me.betatransfer.io/api/payment"
YOUR_DOMAIN = "https://dankir0admin.pythonanywhere.com"

# Точный путь к базе данных
DB_PATH = os.path.join(BASE_DIR, "voting_platform.db")

def get_db():
    # Добавлен timeout=20, чтобы избежать блокировок базы данных на сервере
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    # Инициализация базы данных с таймаутом ожидания
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        balance REAL DEFAULT 0.0,
        is_admin INTEGER DEFAULT 0
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contestants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        photo_url TEXT NOT NULL,
        votes_count INTEGER DEFAULT 0
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS votes_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        contestant_id INTEGER,
        amount_paid REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(contestant_id) REFERENCES contestants(id)
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id TEXT PRIMARY KEY,
        user_id INTEGER,
        amount REAL,
        status TEXT DEFAULT 'pending',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")

    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, balance, is_admin) VALUES (?, ?, ?, ?)",
            ("admin", hash_password("admin123"), 0.0, 1)
        )
    conn.commit()
    conn.close()

def get_current_user(session_user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user_id:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT id, username, balance, is_admin FROM users WHERE id = ?", (session_user_id,))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return dict(user)

class UserAuth(BaseModel):
    username: str
    password: str

class DepositRequest(BaseModel):
    amount: float

@app.post("/api/register")
def register(user: UserAuth, db=Depends(get_db)):
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (user.username, hash_password(user.password))
        )
        db.commit()
        return {"status": "success", "message": "Успешная регистрация!"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Имя пользователя занято")

@app.post("/api/login")
def login(user: UserAuth, response: Response, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, password_hash FROM users WHERE username = ?", (user.username,))
    res = cursor.fetchone()
    if not res or res["password_hash"] != hash_password(user.password):
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")

    response.set_cookie(key="session_user_id", value=str(res["id"]), httponly=True)
    return {"status": "success", "message": "Вход выполнен"}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(key="session_user_id")
    return {"status": "success"}

@app.get("/api/me")
def get_me(user=Depends(get_current_user)):
    return user

@app.get("/api/contestants")
def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM contestants ORDER BY votes_count DESC")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/vote")
def vote(contestant_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    vote_cost = 10.0
    if user["balance"] < vote_cost:
        raise HTTPException(status_code=400, detail="Недостаточно средств")

    cursor = db.cursor()
    cursor.execute("SELECT id FROM contestants WHERE id = ?", (contestant_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Участница не найдена")

    try:
        db.execute("BEGIN")
        cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (vote_cost, user["id"]))
        cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
        cursor.execute("INSERT INTO votes_log (user_id, contestant_id, amount_paid) VALUES (?, ?, ?)",
                       (user["id"], contestant_id, vote_cost))
        db.commit()
        return {"status": "success", "message": "Голос учтен!"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка транзакции")

@app.post("/api/deposit")
def deposit(req: DepositRequest, user=Depends(get_current_user), db=Depends(get_db)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Некорректная сумма")

    order_id = f"pay_{uuid.uuid4().hex[:10]}"
    currency = "RUB"

    cursor = db.cursor()
    cursor.execute("INSERT INTO payments (id, user_id, amount, status) VALUES (?, ?, ?, 'pending')",
                   (order_id, user["id"], req.amount))
    db.commit()

    sign_str = f"{req.amount:.2f}{currency}{order_id}{BETATRANSFER_PROJECT_ID}{BETATRANSFER_API_KEY}"
    sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()

    payload = {
        "project_id": BETATRANSFER_PROJECT_ID,
        "amount": f"{req.amount:.2f}",
        "currency": currency,
        "order_id": order_id,
        "sign": sign,
        "redirect_url": f"{YOUR_DOMAIN}/",
        "callback_url": f"{YOUR_DOMAIN}/api/payment/betatransfer-callback"
    }

    try:
        # Проверяем, видит ли код переменные (выведем только длину, чтобы не палить ключи в логах)
        print("ДЛИНА SHOP_ID:", len(str(os.getenv("BETATRANSFER_PROJECT_ID") or "")))
        print("ДЛИНА API_KEY:", len(str(os.getenv("BETATRANSFER_API_KEY") or "")))
        
        # Узнаем внешний IP-адрес, под которым Railway стучится в платежку
        try:
            current_ip = requests.get("https://api.ipify.org", timeout=5).text
            print("ВНЕШНИЙ IP СЕРВЕРА RAILWAY:", current_ip)
        except Exception as e:
            print("Не удалось узнать IP:", e)
        response = requests.post(BETATRANSFER_URL, json=payload, timeout=10)
        print("ОТВЕТ ОТ БЕТАТРАНСФЕР:", response.text)  # <-- Поменяли res на response
        data = response.json()
        if response.status_code == 200 and data.get("status") == "success":
            return {"payment_url": data.get("url")}
        else:
            raise HTTPException(status_code=400, detail=data.get("message", "Ошибка мерчанта"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка платежного шлюза: {str(e)}")

@app.post("/api/payment/betatransfer-callback")
async def betatransfer_callback(request: Request, db=Depends(get_db)):
    form_data = await request.form()
    if form_data.get("status") != "success":
        return "OK"

    received_sign = form_data.get("sign")
    amount = form_data.get("amount")
    order_id = form_data.get("order_id")

    check_str = f"{amount}{order_id}{BETATRANSFER_API_KEY}"
    expected_sign = hashlib.md5(check_str.encode('utf-8')).hexdigest()

    if received_sign != expected_sign:
        raise HTTPException(status_code=400, detail="Sign mismatch")

    cursor = db.cursor()
    cursor.execute("SELECT user_id, status FROM payments WHERE id = ?", (order_id,))
    payment = cursor.fetchone()

    if payment and payment["status"] == "pending":
        user_id = payment["user_id"]
        try:
            db.execute("BEGIN")
            cursor.execute("UPDATE payments SET status = 'success' WHERE id = ?", (order_id,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (float(amount), user_id))
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Database error during top-up")

    return "OK"

@app.post("/api/admin/contestants")
async def admin_add_contestant(
        name: str = Form(...),
        file: UploadFile = File(...),
        user=Depends(get_current_user),
        db=Depends(get_db)
):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Файл должен быть изображением")

    photos_dir = os.path.join(BASE_DIR, "static", "photos")
    if not os.path.exists(photos_dir):
        os.makedirs(photos_dir)

    file_extension = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(photos_dir, unique_filename)

    try:
        contents = await file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        print(f"Ошибка сохранения файла: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при сохранении файла")

    photo_url = f"/static/photos/{unique_filename}"

    cursor = db.cursor()
    cursor.execute("INSERT INTO contestants (name, photo_url) VALUES (?, ?)", (name, photo_url))
    db.commit()

    return {"status": "success", "message": "Участница успешно добавлена!"}

@app.delete("/api/admin/contestants/{girl_id}")
def admin_delete_contestant(girl_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor = db.cursor()
    cursor.execute("DELETE FROM contestants WHERE id = ?", (girl_id,))
    db.commit()
    return {"status": "success", "message": "Участница удалена"}

@app.get("/", response_class=HTMLResponse)
def page_main():
    with open(os.path.join(BASE_DIR, "templates", "index.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/admin", response_class=HTMLResponse)
def page_admin():
    with open(os.path.join(BASE_DIR, "templates", "admin.html"), "r", encoding="utf-8") as f:
        return f.read()

# --- ПРИНУДИТЕЛЬНЫЙ СТАРТ БАЗЫ ДАННЫХ ПРИ ЗАПУСКЕ ---
init_db()

# --- СТРАНИЦЫ САЙТА ---

@app.get("/", response_class=HTMLResponse)
def page_main():
    with open(os.path.join(BASE_DIR, "templates", "index.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/admin", response_class=HTMLResponse)
def page_admin():
    with open(os.path.join(BASE_DIR, "templates", "admin.html"), "r", encoding="utf-8") as f:
        return f.read()
