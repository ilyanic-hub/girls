import os
import hashlib
import sqlite3
import uuid
import requests
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Cookie, Response, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
from starlette.staticfiles import StaticFiles

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Photo Voting Platform v2", lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_path = os.path.join(BASE_DIR, "static")
if not os.path.exists(static_path):
    os.makedirs(static_path)
app.mount("/static", StaticFiles(directory=static_path), name="static")

# НАСТРОЙКИ TRYBIT
TRYBIT_API_KEY = "ТВОЙ_API_КЛЮЧ_ИЗ_ЛИЧНОГО_КАБИНЕТА"
TRYBIT_SHOP_ID = "ТВОЙ_SHOP_ID_МАГАЗИНА"
TRYBIT_URL = "https://api.trybit.com/v2/invoice/create"
YOUR_DOMAIN = "https://girls-production.up.railway.app"

DB_PATH = os.path.join(BASE_DIR, "voting_platform.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
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
    CREATE TABLE IF NOT EXISTS history_winners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        photo_url TEXT NOT NULL,
        title_date TEXT NOT NULL
    )""")

    # ТАБЛИЦА ДЛЯ АЛЬБОМОВ ФОТОСЕССИЙ
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS winner_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        winner_id INTEGER,
        photo_url TEXT NOT NULL,
        FOREIGN KEY(winner_id) REFERENCES history_winners(id) ON DELETE CASCADE
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
        return {"status": "success"}
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
    return {"status": "success"}

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

@app.get("/api/history")
def get_history_winners(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM history_winners ORDER BY id DESC")
    return [dict(row) for row in cursor.fetchall()]

# ПОЛУЧИТЬ АЛЬБОМ ДЛЯ КОНКРЕТНОЙ ПОБЕДИТЕЛЬНИЦЫ
@app.get("/api/history/{winner_id}/photos")
def get_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT photo_url FROM winner_photos WHERE winner_id = ?", (winner_id,))
    return [row["photo_url"] for row in cursor.fetchall()]

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
        return {"status": "success"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка транзакции")

@app.post("/api/deposit")
def deposit(req: DepositRequest, user=Depends(get_current_user), db=Depends(get_db)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Некорректная сумма")
    order_id = f"ORDER_{uuid.uuid4().hex[:10].upper()}"
    cursor = db.cursor()
    cursor.execute("INSERT INTO payments (id, user_id, amount, status) VALUES (?, ?, ?, 'pending')",
                   (order_id, user["id"], req.amount))
    db.commit()
    headers = {"Authorization": f"Token {TRYBIT_API_KEY}", "Content-Type": "application/json"}
    payload = {"amount": float(req.amount), "shop_id": TRYBIT_SHOP_ID, "currency": "RUB", "order_id": order_id}
    try:
        response = requests.post(TRYBIT_URL, json=payload, headers=headers, timeout=15)
        data = response.json()
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Ошибка: {err}")
    result_data = data.get("result", {})
    return {"payment_url": result_data.get("link") or result_data.get("url")}

@app.post("/api/payment/trybit-callback")
async def trybit_callback(request: Request, db=Depends(get_db)):
    try: data = await request.json()
    except Exception:
        form_data = await request.form()
        data = dict(form_data)
    if data.get("status") not in ["success", "paid"]: return "OK"
    order_id = data.get("order_id")
    amount = data.get("amount")
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
        except Exception: db.rollback()
    return "OK"

# --- АДМИН-ПАНЕЛЬ ---
@app.post("/api/admin/contestants")
async def admin_add_contestant(name: str = Form(...), file: UploadFile = File(...), user=Depends(get_current_user), db=Depends(get_db)):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    photos_dir = os.path.join(BASE_DIR, "static", "photos")
    if not os.path.exists(photos_dir): os.makedirs(photos_dir)
    file_path = os.path.join(photos_dir, f"{uuid.uuid4()}{os.path.splitext(file.filename)[1]}")
    with open(file_path, "wb") as f: f.write(await file.read())
    cursor = db.cursor()
    cursor.execute("INSERT INTO contestants (name, photo_url) VALUES (?, ?)", (name, f"/static/photos/{os.path.basename(file_path)}"))
    db.commit()
    return {"status": "success"}

# ЗАГРУЗКА ПОБЕДИТЕЛЬНИЦЫ + КУЧИ ФОТО ДЛЯ АЛЬБОМА
@app.post("/api/admin/history")
async def admin_add_history_winner(
        name: str = Form(...),
        title_date: str = Form(...),
        file: UploadFile = File(...),              # Главное фото
        album_files: List[UploadFile] = File([]),  # Дополнительные фото альбома
        user=Depends(get_current_user),
        db=Depends(get_db)
):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    photos_dir = os.path.join(BASE_DIR, "static", "photos")
    if not os.path.exists(photos_dir): os.makedirs(photos_dir)

    # Сохраняем главное фото
    main_path = os.path.join(photos_dir, f"winner_{uuid.uuid4()}{os.path.splitext(file.filename)[1]}")
    with open(main_path, "wb") as f: f.write(await file.read())
    main_url = f"/static/photos/{os.path.basename(main_path)}"

    cursor = db.cursor()
    cursor.execute("INSERT INTO history_winners (name, photo_url, title_date) VALUES (?, ?, ?)", (name, main_url, title_date))
    winner_id = cursor.lastrowid

    # Сохраняем фотографии альбома (если они есть)
    for album_file in album_files:
        if album_file.filename:
            alb_path = os.path.join(photos_dir, f"alb_{uuid.uuid4()}{os.path.splitext(album_file.filename)[1]}")
            with open(alb_path, "wb") as f: f.write(await album_file.read())
            alb_url = f"/static/photos/{os.path.basename(alb_path)}"
            cursor.execute("INSERT INTO winner_photos (winner_id, photo_url) VALUES (?, ?)", (winner_id, alb_url))

    db.commit()
    return {"status": "success"}

@app.delete("/api/admin/contestants/{girl_id}")
def admin_delete_contestant(girl_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor = db.cursor()
    cursor.execute("DELETE FROM contestants WHERE id = ?", (girl_id,))
    db.commit()
    return {"status": "success"}

# --- СТРАНИЦЫ САЙТА ---
@app.get("/", response_class=HTMLResponse)
def page_main():
    with open(os.path.join(BASE_DIR, "templates", "index.html"), "r", encoding="utf-8") as f: return f.read()

@app.get("/history", response_class=HTMLResponse)
def page_history():
    with open(os.path.join(BASE_DIR, "templates", "history.html"), "r", encoding="utf-8") as f: return f.read()

@app.get("/admin", response_class=HTMLResponse)
def page_admin():
    with open(os.path.join(BASE_DIR, "templates", "admin.html"), "r", encoding="utf-8") as f: return f.read()
