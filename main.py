import os
import sys
import sqlite3
import hashlib
import dropbox
import time
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Response, Cookie, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import requests
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import pytz

app = FastAPI()

os.makedirs("static/avatars", exist_ok=True)
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

# Возвращаем твой исходный статический маунт папки templates
if os.path.exists("templates"):
    app.mount("/templates", StaticFiles(directory="templates"), name="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# 1. Сначала импорты (у вас они есть)
# ...

# 2. Функция зависимости (должна быть выше роутов)
def get_current_user(session_user: Optional[str] = Cookie(None)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return {"username": session_user}

# 3. Эндпоинт
@app.post("/api/user/upload-avatar")
async def upload_avatar(
    file: UploadFile = File(...), 
    user: dict = Depends(get_current_user) # Depends вызывается ТОЛЬКО здесь
):
    # Код обработки файла
    return {"status": "success"}

    
# ================= НАСТРОЙКА DROPBOX =================
DROPBOX_REFRESH_TOKEN = "ApXJY9sYu1MAAAAAAAAAAYk7D9NmgMi88qboNhpKNSGsh1conF6E4kBJicP4Web6"
DROPBOX_APP_KEY = "oou4gf2ktj2y51j"
DROPBOX_APP_SECRET = "dunglx7xl3el8pa"

PLISIO_API_TOKEN = "u1JWmqyQBwnA6kuvp1PbOl5UKvt4a2i9oIk5CzD5GfiyThtj9RcYPsg2nroOgzsu"

DB_LOCAL_PATH = "database.db"
DB_DROPBOX_PATH = "/database.db"

def get_dropbox_client():
    try:
        return dropbox.Dropbox(
            oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
            app_key=DROPBOX_APP_KEY,
            app_secret=DROPBOX_APP_SECRET
        )
    except Exception as e:
        print(f"Ошибка инициализации Dropbox: {e}")
        return None

def download_db_from_dropbox():
    dbx = get_dropbox_client()
    if not dbx:
        print("Dropbox клиент не готов.")
        return
    try:
        print("Скачивание базы данных из Dropbox...")
        metadata, res = dbx.files_download(path=DB_DROPBOX_PATH)
        with open(DB_LOCAL_PATH, "wb") as f:
            f.write(res.content)
        print("База данных успешно скачана!")
    except dropbox.exceptions.AuthError as auth_err:
        print(f"Ошибка авторизации Dropbox! Проверь ключи: {auth_err}")
    except dropbox.exceptions.ApiError as api_err:
        print("Файл базы данных еще не создан в Dropbox. Создаем новую локальную базу.")
    except Exception as e:
        print(f"Не удалось скачать базу (другая ошибка): {e}")

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
        is_admin INTEGER DEFAULT 0,
        last_bonus_date TEXT DEFAULT NULL,
        secret_answer TEXT DEFAULT NULL
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        title_date TEXT NOT NULL,
        photo_url TEXT NOT NULL
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id INTEGER NOT NULL,
        photo_url TEXT NOT NULL,
        FOREIGN KEY (history_id) REFERENCES history(id) ON DELETE CASCADE
    )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_balances (
            user_ip TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contestant_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contestant_id) REFERENCES contestants(id) ON DELETE CASCADE
)
""")

    # Накатываем альтеры на случай старых баз
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN last_bonus_date TEXT DEFAULT NULL")
        db.commit()
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN secret_answer TEXT DEFAULT NULL")
        db.commit()
    except sqlite3.OperationalError:
        pass
        
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

# ================= СХЕМЫ ДАННЫХ =================
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

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# Добавьте эту функцию в ваш main.py
def get_current_user(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    cursor = db.cursor()
    # Ищем пользователя, чтобы получить его реальный ID из базы
    cursor.execute("SELECT id, username FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    
    return user # Теперь это объект, у которого есть user['id']

# ================= РОУТЫ СТРАНИЦ =================
@app.get("/", response_class=HTMLResponse)
async def get_main_page():
    path_to_html = "templates/index.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл index.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/top", response_class=HTMLResponse)
@app.get("/top/", response_class=HTMLResponse)
async def get_top_page():
    path_to_html = "templates/top.html"  # Убедись, что твой файл лежит в папке templates и называется именно так
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл top.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/models", response_class=HTMLResponse)
@app.get("/models/", response_class=HTMLResponse)
async def get_models_page():
    path_to_html = "templates/models.html"  # Убедись, что файл в папке templates называется именно так
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл models.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/about", response_class=HTMLResponse)
@app.get("/about/", response_class=HTMLResponse)
async def get_about_page():
    path_to_html = "templates/about.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл about.html не найден!</h1>", status_code=404)
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

@app.get("/terms", response_class=HTMLResponse)
async def get_terms():
    path = "templates/terms.html"
    if not os.path.exists(path): return HTMLResponse(content="<h1>Файл terms.html не найден!</h1>", status_code=404)
    with open(path, "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())

@app.get("/privacy", response_class=HTMLResponse)
async def get_privacy():
    path = "templates/privacy.html"
    if not os.path.exists(path): return HTMLResponse(content="<h1>Файл privacy.html не найден!</h1>", status_code=404)
    with open(path, "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())

@app.get("/refund", response_class=HTMLResponse)
async def get_refund():
    path = "templates/refund.html"
    if not os.path.exists(path): return HTMLResponse(content="<h1>Файл refund.html не найден!</h1>", status_code=404)
    with open(path, "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())

@app.get("/api/contestants/{contestant_id}/comments")
async def get_comments(contestant_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM comments WHERE contestant_id = ? ORDER BY id DESC", (contestant_id,))
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/contestants/{contestant_id}/comments")
async def add_comment(contestant_id: int, data: dict, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Нужно войти в аккаунт")
    
    text = data.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Комментарий не может быть пустым")
    
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO comments (contestant_id, username, text) VALUES (?, ?, ?)",
        (contestant_id, session_user, text)
    )
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}


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
    
    try:
        cursor.execute(
            "INSERT INTO users (username, password, balance, is_admin, secret_answer) VALUES (?, ?, 0.0, 0, ?)", 
            (username, password_hash, answer_hash)
        )
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "message": "Регистрация успешна!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
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


# ================= РАБОТА С УЧАСТНИЦАМИ =================
@app.get("/api/contestants")
async def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    # Запрос считает количество комментариев для каждой участницы
    cursor.execute("""
        SELECT c.*, COUNT(cm.id) AS comments_count 
        FROM contestants c
        LEFT JOIN comments cm ON c.id = cm.contestant_id
        GROUP BY c.id
    """)
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

@app.put("/api/admin/contestants/{id}")
async def admin_edit_contestant(id: int, data: ContestantSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
    
    if len(inline_photo_url) < 50: 
        cursor.execute("UPDATE contestants SET name = ? WHERE id = ?", (data.name, id))
    else:
        cursor.execute("UPDATE contestants SET name = ?, photo_url = ? WHERE id = ?", (data.name, inline_photo_url, id))
        
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": "Участница успешно изменена!"}



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
        inserted_id = cursor.lastrowid

        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "history_id": inserted_id, "message": "Основная запись создана!"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@app.post("/api/admin/history/{history_id}/upload-photo")
async def admin_upload_album_photo(history_id: int, data: AlbumFileSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
        
    try:
        file_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
        
        if file_url:
            cursor.execute(
                "INSERT INTO history_photos (history_id, photo_url) VALUES (?, ?)", 
                (int(history_id), str(file_url))
            )
            db.commit()
            upload_db_to_dropbox()
            return {"status": "success", "message": "Фото успешно добавлено в альбом!"}
        else:
            return {"status": "error", "message": "Пустая строка Base64"}
            
    except Exception as err:
        print(f"Ошибка сохранения фото в альбом: {str(err)}")
        raise HTTPException(status_code=500, detail=str(err))

@app.get("/api/history/{winner_id}/photos")
async def get_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT photo_url FROM history_photos WHERE history_id = ?", (winner_id,))
    rows = cursor.fetchall()
    return [row["photo_url"] for row in rows]

@app.delete("/api/admin/history/{id}")
async def delete_history_item(id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    cursor.execute("DELETE FROM history_photos WHERE history_id = ?", (id,))
    cursor.execute("DELETE FROM history WHERE id = ?", (id,))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}

@app.put("/api/admin/history/{id}")
async def admin_edit_history(id: int, data: HistorySchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    try:
        inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
        cursor.execute("UPDATE history SET name = ?, title_date = ? WHERE id = ?", (data.name, data.title_date, id))
        
        if len(inline_photo_url) > 50:
            cursor.execute("UPDATE history SET photo_url = ? WHERE id = ?", (inline_photo_url, id))
            
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "history_id": id, "message": "Основная информация обновлена!"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@app.get("/api/admin/debug-history/{winner_id}")
async def debug_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, LENGTH(photo_url) as size FROM history_photos WHERE history_id = ?", (winner_id,))
    rows = cursor.fetchall()
    
    result = []
    for row in rows:
        result.append({
            "photo_table_id": row["id"],
            "base64_length": row["size"]
        })
    return {
        "total_photos_in_db": len(result),
        "photos": result
    }

@app.delete("/api/admin/history/{id}/photos")
async def admin_clear_history_photos(id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
        
    try:
        cursor.execute("DELETE FROM history_photos WHERE history_id = ?", (id,))
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "message": "Старый альбом очищен!"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))


class DepositSchema(BaseModel):
    amount: int  

class ResetPasswordSchema(BaseModel):
    username: str
    secret_answer: str
    new_password: str

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
        raise HTTPException(status_code=404, detail="Пользователь не найден или для него не настроено восстановление")
        
    if hash_password(secret_answer) != user["secret_answer"]:
        raise HTTPException(status_code=400, detail="Неверное секретное слово!")
        
    new_password_hash = hash_password(new_password)
    cursor.execute("UPDATE users SET password = ? WHERE username = ?", (new_password_hash, username))
    db.commit()
    
    upload_db_to_dropbox()
    return {"status": "success", "message": "Пароль успешно изменен! Теперь вы можете войти."}

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

# ================= СТАТУС КОНКУРСА (ТАЙМЕР) =================
@app.get("/api/contest-status")
async def get_contest_status():
    try:
        # Устанавливаем часовой пояс (например, Москва/Киев)
        tz = pytz.timezone("Europe/Kiev") 
        
        # Задай здесь дату и время окончания текущего раунда конкурса: Год, Месяц, День, Час, Минута
        deadline = datetime(2026, 7, 20, 23, 59, 0)
        deadline_localized = tz.localize(deadline)
        
        return {
            "status": "active",
            "deadline_iso": deadline_localized.isoformat()
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Ошибка таймера: {str(e)}"})


# ================= ПЛАТЕЖИ PLISIO =================

# 1. Создание счета в Plisio для конкретного пользователя
@app.post("/api/payment/create")
@app.post("/api/payment/create/")
async def create_plisio_invoice(request: Request, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    try:
        content_type = request.headers.get("content-type", "")
        body_data = {}
        
        if "application/json" in content_type:
            try: body_data = await request.json()
            except Exception: pass
        else:
            try: body_data = dict(await request.form())
            except Exception: pass

        # 1. Достаем сумму коинов из запроса
        coins_amount = body_data.get("amount") or body_data.get("amount_usd") or request.query_params.get("amount")
        if not coins_amount:
            raise HTTPException(status_code=400, detail="Не указана сумма перевода (amount)")

        # Конвертируем коины в USD фиат: 100 коинов = 1 доллар
        amount_usd = float(coins_amount) / 100.0

        # 2. Определяем пользователя по куке сессии
        if not session_user:
            raise HTTPException(status_code=401, detail="Пользователь не авторизован. Войдите в аккаунт перед оплатой.")
            
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (session_user,))
        user_row = cursor.fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="Авторизованный пользователь не найден в базе данных")
            
        user_id = user_row["id"]
        order_id = f"order_{user_id}_{int(time.time())}" 
        
        # Передаем правильные параметры в Plisio
        params = {
            "api_key": PLISIO_API_TOKEN,
            "source_currency": "USD",       
            "source_amount": f"{amount_usd:.2f}", # Форматируем до 2 знаков после запятой (например, "5.00")
            "order_number": order_id,        
            "order_name": f"Пополнение коинов ({coins_amount} шт.) для пользователя {session_user}",
            "callback_url": "https://www.photo-rating.club/api/payment/plisio-callback"
        }
        
        # Отправляем запрос в Plisio
        async with httpx.AsyncClient() as client:
            response = await client.get("https://plisio.net/api/v1/invoices/new", params=params)
            res_data = response.json()
        
        if res_data.get("status") == "success":
            return {"payment_url": res_data["data"]["invoice_url"]}
        else:
            print(f"Ошибка Plisio API: {res_data}")
            raise HTTPException(status_code=400, detail="Ошибка создания счета в платежной системе")
            
    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        print(f"Критическая ошибка при создании инвойса: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 2. Ежедневный бонус
@app.post("/api/bonus")
async def claim_daily_bonus(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Нужно войти в аккаунт, чтобы получить бонус")
        
    cursor = db.cursor()
    cursor.execute("SELECT balance, last_bonus_date FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
        
    now = datetime.now()
    last_bonus_str = user["last_bonus_date"]
    
    if last_bonus_str:
        last_bonus_time = datetime.fromisoformat(last_bonus_str)
        if now - last_bonus_time < timedelta(hours=24):
            time_left = timedelta(hours=24) - (now - last_bonus_time)
            hours_left = int(time_left.total_seconds() // 3600)
            minutes_left = int((time_left.total_seconds() % 3600) // 60)
            raise HTTPException(
                status_code=400, 
                detail=f"Бонус уже получен. До следующего осталось: {hours_left}ч {minutes_left}м"
            )
            
    new_time_str = now.isoformat()
    cursor.execute(
        "UPDATE users SET balance = balance + 5.0, last_bonus_date = ? WHERE username = ?", 
        (new_time_str, session_user)
    )
    db.commit()
    upload_db_to_dropbox()
    
    return {"status": "success", "message": "Вы получили 5 бесплатных коинов!"}

# 3. Вебхук Plisio (обработчик оплаты)
@app.api_route("/api/payment/plisio-callback", methods=["GET", "POST", "PUT", "OPTIONS"])
@app.api_route("/api/payment/plisio-callback/", methods=["GET", "POST", "PUT", "OPTIONS"])
async def plisio_callback(request: Request):
    if request.method in ["GET", "OPTIONS"]:
        return JSONResponse(content={"status": "ok", "message": "Endpoint works!"})
        
    try:
        content_type = request.headers.get("content-type", "")
        
        if "application/json" in content_type:
            data = await request.json()
        else:
            data = await request.form()
            
        status = data.get("status")
        print(f"Получен вебхук Plisio. Метод: {request.method}, Статус: {status}")
        
        if status == "completed":
            order_id = data.get("order_number")
            
            if order_id and order_id.startswith("order_"):
                parts = order_id.split("_")
                if len(parts) >= 2:
                    user_id = parts[1]
                    
                    # Извлекаем сумму в долларах и переводим обратно в коины (умножаем на 100)
                    amount_usd = float(data.get("source_amount") or data.get("amount") or 0.0)
                    coins_to_add = amount_usd * 100.0
                    
                    db = sqlite3.connect(DB_LOCAL_PATH)
                    cursor = db.cursor()
                    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (coins_to_add, int(user_id)))
                    db.commit()
                    db.close()
                    
                    upload_db_to_dropbox()
                    print(f"Баланс пользователя ID {user_id} пополнен на {coins_to_add} коинов!")
            
            return Response(content="OK", media_type="text/plain")
            
        return Response(content="Ignored", media_type="text/plain")
        
    except Exception as e:
        print(f"Ошибка внутри вебхука Plisio: {str(e)}")
        return JSONResponse(status_code=200, content={"status": "error_but_ok_for_plisio", "message": str(e)})
