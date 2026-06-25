import os
import uuid
import sqlite3
import sys
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------- НАСТРОЙКА ПОСТОЯННОГО ХРАНИЛИЩА -----------------
DATA_DIR = "/app/data"
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception as e:
    print(f"--- ПРЕДУПРЕЖДЕНИЕ: Переключение на локальную папку data ({e}) ---", file=sys.stderr)
    DATA_DIR = os.path.join(BASE_DIR, "data")
    os.makedirs(DATA_DIR, exist_ok=True)

PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)

DATABASE_PATH = os.path.join(DATA_DIR, "database.db")
print(f"--- БАЗА ДАННЫХ: {DATABASE_PATH} ---", file=sys.stdout)
# -------------------------------------------------------------------

app = FastAPI(title="Photo Rating API")

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.mount("/static/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Pydantic модели для валидации твоих JS-запросов
class AuthModel(BaseModel):
    username: str
    password: str

class DepositModel(BaseModel):
    amount: float

def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Таблица пользователей (нужна для твоей системы авторизации и баланса)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        balance REAL DEFAULT 0.0,
        is_admin INTEGER DEFAULT 0
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contestants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        photo_url TEXT,
        votes_count INTEGER DEFAULT 0
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history_winners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        title_date TEXT NOT NULL,
        photo_url TEXT
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS winner_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        winner_id INTEGER,
        photo_url TEXT,
        FOREIGN KEY(winner_id) REFERENCES history_winners(id) ON DELETE CASCADE
    )""")
    
    # Создаем дефолтного админа, если таблицы были пустыми
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES ('admin', 'admin', 500.0, 1)")
        
    conn.commit()
    conn.close()
    print("--- ТАБЛИЦЫ БАЗЫ ДАННЫХ УСПЕШНО СИНХРОНИЗИРОВАНЫ ---", file=sys.stdout)

init_db()

# Текущая сессия (упрощенная имитация для авторизации, чтобы куки не усложнять)
# В продакшене лучше использовать сессии, но для совместимости с твоим фронтендом сделаем синглтон
CURRENT_USER_ID = 1 # По умолчанию админ, чтобы тебя пускало


# ================= СТРАНИЦЫ (ФРОНТЕНД) =================

@app.get("/", response_class=FileResponse)
async def index_page():
    # Заменяем на FileResponse! Движок Jinja2 больше не парсит файл и НЕ выдает unhashable type: dict!
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))

@app.get("/history", response_class=FileResponse)
async def history_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "history.html"))

@app.get("/admin", response_class=FileResponse)
async def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "admin.html"))


# ================= АВТОРИЗАЦИЯ И БАЛАНС (ПОД ТВОЙ ФРОНТЕНД) =================

@app.get("/api/me")
def get_me(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT username, balance, is_admin FROM users WHERE id = ?", (CURRENT_USER_ID,))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return dict(user)

@app.post("/api/register")
def api_register(data: AuthModel, db=Depends(get_db)):
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES (?, ?, 100.0, 0)", (data.username, data.password))
        db.commit()
        return {"status": "success"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Пользователь уже существует")

@app.post("/api/login")
def api_login(data: AuthModel, db=Depends(get_db)):
    global CURRENT_USER_ID
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ? AND password = ?", (data.username, data.password))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    CURRENT_USER_ID = user["id"]
    return {"status": "success"}

@app.post("/api/logout")
def api_logout():
    global CURRENT_USER_ID
    CURRENT_USER_ID = None
    return {"status": "success"}

@app.post("/api/deposit")
def api_deposit(data: DepositModel, db=Depends(get_db)):
    cursor = db.cursor()
    # Твой фронтенд ждет payment_url для редиректа на оплату. 
    # Делаем прямую эмуляцию: пополняем баланс сразу и отправляем обратно на главную
    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (data.amount, CURRENT_USER_ID))
    db.commit()
    return {"payment_url": "/"}


# ================= ПУБЛИЧНЫЙ API ГАЛЕРЕИ =================

@app.get("/api/contestants")
def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, photo_url, votes_count FROM contestants ORDER BY votes_count DESC")
    rows = cursor.fetchall()
    return [dict(row) for row in rows]

@app.post("/api/vote") # Изменено под твой фронтенд: fetch(`/api/vote?contestant_id=${id}`)
def vote_for_girl(contestant_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    
    # Проверяем баланс пользователя (голосование стоит 10 рублей)
    cursor.execute("SELECT balance FROM users WHERE id = ?", (CURRENT_USER_ID,))
    user = cursor.fetchone()
    if not user or user["balance"] < 10:
        raise HTTPException(status_code=400, detail="Недостаточно средств. Пополните баланс!")
        
    cursor.execute("SELECT id FROM contestants WHERE id = ?", (contestant_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Участница не найдена")
    
    # Списываем 10 руб и добавляем голос
    cursor.execute("UPDATE users SET balance = balance - 10 WHERE id = ?", (CURRENT_USER_ID,))
    cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
    db.commit()
    return {"status": "success"}

@app.get("/api/history")
def get_history_winners(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, title_date, photo_url FROM history_winners ORDER BY id DESC")
    rows = cursor.fetchall()
    return [dict(row) for row in rows]

@app.get("/api/history/{winner_id}/photos")
def get_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT photo_url FROM winner_photos WHERE winner_id = ? ORDER BY id ASC", (winner_id,))
    rows = cursor.fetchall()
    return [row["photo_url"] for row in rows]


# ================= АДМИН-ПАНЕЛЬ (УПРАВЛЕНИЕ) =================

def check_admin_rights(user_id):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if not res or res[0] != 1:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

@app.post("/api/admin/contestants")
async def admin_add_contestant(
    name: str = Form(...),
    file: UploadFile = File(...),
    db=Depends(get_db)
):
    check_admin_rights(CURRENT_USER_ID)
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(PHOTOS_DIR, filename)
    
    with open(file_path, "wb") as f:
        f.write(await file.read())
        
    photo_url = f"/static/photos/{filename}"
    cursor = db.cursor()
    cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (name, photo_url))
    db.commit()
    return {"status": "success"}

@app.post("/api/admin/contestants/{girl_id}/edit")
async def admin_edit_contestant(
    girl_id: int,
    name: str = Form(...),
    votes_count: int = Form(...),
    file: Optional[UploadFile] = File(None),
    db=Depends(get_db)
):
    check_admin_rights(CURRENT_USER_ID)
    cursor = db.cursor()
    
    if file and file.filename:
        file_ext = os.path.splitext(file.filename)[1]
        filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(PHOTOS_DIR, filename)
        with open(file_path, "wb") as f:
            f.write(await file.read())
        photo_url = f"/static/photos/{filename}"
        cursor.execute("UPDATE contestants SET name = ?, votes_count = ?, photo_url = ? WHERE id = ?", (name, votes_count, photo_url, girl_id))
    else:
        cursor.execute("UPDATE contestants SET name = ?, votes_count = ? WHERE id = ?", (name, votes_count, girl_id))
        
    db.commit()
    return {"status": "success"}

@app.delete("/api/admin/contestants/{girl_id}")
def admin_delete_contestant(girl_id: int, db=Depends(get_db)):
    check_admin_rights(CURRENT_USER_ID)
    cursor = db.cursor()
    cursor.execute("DELETE FROM contestants WHERE id = ?", (girl_id,))
    db.commit()
    return {"status": "success"}


# ================= ЗАЛ СЛАВЫ (АДМИН) =================

@app.post("/api/admin/history")
async def admin_add_history_winner(
    name: str = Form(...),
    title_date: str = Form(...),
    file: UploadFile = File(...),
    album_files: List[UploadFile] = File([]),
    db=Depends(get_db)
):
    check_admin_rights(CURRENT_USER_ID)
    cursor = db.cursor()
    
    main_ext = os.path.splitext(file.filename)[1]
    main_filename = f"winner_{uuid.uuid4()}{main_ext}"
    main_path = os.path.join(PHOTOS_DIR, main_filename)
    with open(main_path, "wb") as f:
        f.write(await file.read())
    main_photo_url = f"/static/photos/{main_filename}"
    
    cursor.execute("INSERT INTO history_winners (name, title_date, photo_url) VALUES (?, ?, ?)", (name, title_date, main_photo_url))
    winner_id = cursor.lastrowid
    
    for alb_file in album_files:
        if alb_file.filename:
            alb_ext = os.path.splitext(alb_file.filename)[1]
            alb_filename = f"alb_{uuid.uuid4()}{alb_ext}"
            alb_path = os.path.join(PHOTOS_DIR, alb_filename)
            with open(alb_path, "wb") as f:
                f.write(await alb_file.read())
            cursor.execute("INSERT INTO winner_photos (winner_id, photo_url) VALUES (?, ?)", (winner_id, f"/static/photos/{alb_filename}"))
            
    db.commit()
    return {"status": "success"}

@app.delete("/api/admin/history/{winner_id}")
def admin_delete_history_winner(winner_id: int, db=Depends(get_db)):
    check_admin_rights(CURRENT_USER_ID)
    cursor = db.cursor()
    cursor.execute("DELETE FROM history_winners WHERE id = ?", (winner_id,))
    cursor.execute("DELETE FROM winner_photos WHERE winner_id = ?", (winner_id,))
    db.commit()
    return {"status": "success"}
