import os
import sys
import sqlite3
import hashlib
import dropbox
from fastapi import FastAPI, Depends, HTTPException, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("templates"):
    app.mount("/templates", StaticFiles(directory="templates"), name="templates")

# ================= НАСТРОЙКА DROPBOX =================
DROPBOX_TOKEN = "ТВОЙ_ПОЛУЧЕННЫЙ_ТОКЕН_DROPBOX" 
DB_LOCAL_PATH = "database.db"
DB_DROPBOX_PATH = "/database.db"

def get_dropbox_client():
    try:
        return dropbox.Dropbox(DROPBOX_TOKEN)
    except Exception as e:
        print(f"Ошибка инициализации Dropbox: {e}")
        return None

def download_db_from_dropbox():
    dbx = get_dropbox_client()
    if not dbx:
        return
    try:
        print("Скачивание базы данных из Dropbox...")
        metadata, res = dbx.files_download(path=DB_DROPBOX_PATH)
        with open(DB_LOCAL_PATH, "wb") as f:
            f.write(res.content)
        print("База данных успешно скачана!")
    except dropbox.exceptions.ApiError as e:
        print("Файл базы данных не найден в Dropbox. Создаем новую базу.")
    except Exception as e:
        print(f"Не удалось скачать базу: {e}")

def upload_db_to_dropbox():
    dbx = get_dropbox_client()
    if not dbx or not os.path.exists(DB_LOCAL_PATH):
        return
    try:
        print("Синхронизация базы данных с Dropbox...")
        with open(DB_LOCAL_PATH, "rb") as f:
            db_bytes = f.read()
        dbx.files_upload(db_bytes, path=DB_DROPBOX_PATH, mode=dropbox.files.WriteMode.overwrite)
        print("База данных успешно сохранена в Dropbox!")
    except Exception as e:
        print(f"Ошибка загрузки базы в Dropbox: {e}")

# Синхронизируем базу при старте
download_db_from_dropbox()

def get_db():
    db = sqlite3.connect(DB_LOCAL_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()

def init_db():
    db = sqlite3.connect(DB_LOCAL_PATH, check_same_thread=False)
    cursor = db.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contestants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        photo_url TEXT NOT NULL,
        votes_count INTEGER DEFAULT 0
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        balance REAL DEFAULT 0.0,
        is_admin INTEGER DEFAULT 0
    )
    """)
    # Изменяем этот блок внутри init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        title_date TEXT NOT NULL,
        photo_url TEXT NOT NULL
    )
    """)

    # ДОБАВЛЯЕМ: Таблица для хранения остальных фоток из альбома этой королевы
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id INTEGER NOT NULL,
        photo_url TEXT NOT NULL,
        FOREIGN KEY (history_id) REFERENCES history(id) ON DELETE CASCADE
    )
    """)
    
    
    admin_password_hash = hashlib.sha256("admin".encode()).hexdigest()
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if cursor.fetchone():
        cursor.execute("UPDATE users SET password = ? WHERE username = 'admin'", (admin_password_hash,))
    else:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES ('admin', ?, 500.0, 1)", (admin_password_hash,))
        
    db.commit()
    db.close()
    upload_db_to_dropbox()

init_db()

# ================= СХЕМЫ ДАННЫХ (ОБЪЯВЛЕНЫ НАВЕРХУ) =================

class UserAuthSchema(BaseModel):
    username: str
    password: str

class ContestantSchema(BaseModel):
    name: str
    file_base64: str

class AlbumFileSchema(BaseModel):
    file_base64: str
    filename: Optional[str] = None
    content_type: Optional[str] = None

class HistorySchema(BaseModel):
    name: str
    title_date: str
    file_base64: str
    filename: Optional[str] = None
    content_type: Optional[str] = None
    album_files: Optional[List[AlbumFileSchema]] = None

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# РОУТЫ СТРАНИЦ
@app.get("/", response_class=HTMLResponse)
async def get_main_page():
    path_to_html = "templates/index.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл index.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
async def get_admin_page(session_user: Optional[str] = Cookie(None)):
    if not session_user:
        return RedirectResponse(url="/", status_code=303)
    db = sqlite3.connect(DB_LOCAL_PATH)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    db.close()
    if not user or not user["is_admin"]:
        return RedirectResponse(url="/", status_code=303)
    path_to_html = "templates/admin.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл admin.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/history", response_class=HTMLResponse)
@app.get("/history/", response_class=HTMLResponse)
async def get_history_page():
    path_to_html = "templates/history.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл history.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ================= АВТОРИЗАЦИЯ И ПОЛЬЗОВАТЕЛИ =================

@app.get("/api/me")
async def api_me(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        return {"username": "Гость", "balance": 0, "is_admin": 0}
    cursor = db.cursor()
    cursor.execute("SELECT username, balance, is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if user:
        return dict(user)
    return {"username": "Гость", "balance": 0, "is_admin": 0}

@app.post("/api/register")
async def api_register(data: UserAuthSchema, db=Depends(get_db)):
    username = data.username.strip()
    password = data.password.strip()
    if len(username) < 3 or len(password) < 4:
        raise HTTPException(status_code=400, detail="Слишком короткое имя или пароль")
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    password_hash = hash_password(password)
    try:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES (?, ?, 100.0, 0)", (username, password_hash))
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "message": "Регистрация успешна!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/login")
async def api_login(data: UserAuthSchema, response: Response, db=Depends(get_db)):
    username = data.username.strip()
    password = data.password.strip()
    cursor = db.cursor()
    cursor.execute("SELECT password, username FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    if not user or user["password"] != hash_password(password):
        raise HTTPException(status_code=400, detail="Неверное имя или пароль")
    response.set_cookie(key="session_user", value=user["username"], max_age=1209600, path="/")
    return {"status": "success", "message": "Вход выполнен!"}

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


# ================= РАБОТА С УЧАСТНИЦАМИ =================

@app.get("/api/contestants")
async def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM contestants")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/admin/contestants")
async def admin_add_contestant(data: ContestantSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
    cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (data.name, inline_photo_url))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}

@app.post("/api/vote")
async def api_vote(contestant_id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Нужно войти в аккаунт")
    cursor = db.cursor()
    cursor.execute("SELECT balance FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if user and user["balance"] >= 10:
        cursor.execute("UPDATE users SET balance = balance - 10 WHERE username = ?", (session_user,))
        cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success"}
    raise HTTPException(status_code=400, detail="Недостаточно средств")

@app.delete("/api/admin/contestants/{id}")
async def delete_contestant(id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor.execute("DELETE FROM contestants WHERE id = ?", (id,))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}


# ================= РАБОТА С ЗАЛОМ СЛАВЫ =================

@app.get("/api/history")
async def get_api_history(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM history ORDER BY id DESC")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/admin/history")
@app.post("/api/admin/history/")
async def admin_add_history(data: HistorySchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    try:
        inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
        cursor.execute("INSERT INTO history (name, title_date, photo_url) VALUES (?, ?, ?)", (data.name, data.title_date, inline_photo_url))
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "message": "Добавлено в историю!"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@app.delete("/api/admin/history/{id}")
async def delete_history_item(id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor.execute("DELETE FROM history WHERE id = ?", (id,))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}
