import os
import sqlite3
import hashlib
import requests
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Cookie, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ================= НАСТРОЙКИ И ИНИЦИАЛИЗАЦИЯ =================

DATABASE_FILE = "database.db"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8923888437:AAEsIYtyGYT3kSE7ZDAS8s84O9YRhpPdGB0")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8501380785")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN", "_WgJ5t--cYkAAAAAAAAAAZ5DdohYivqI_AUgdnlIh-iMtRK4CL3UYdgBoFB2HUG0")

app = FastAPI(title="Photo Rating & Models Platform API")

# Настройка CORS (при необходимости)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= РАБОТА С БАЗОЙ ДАННЫХ =================

def get_db():
    """Зависимость для получения подключения к БД с поддержкой Row-объектов"""
    db = sqlite3.connect(DATABASE_FILE)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Инициализация БД и автоматическое применение миграций"""
    db = sqlite3.connect(DATABASE_FILE)
    cursor = db.cursor()
    
    # Создание основной таблицы пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            secret_answer TEXT NOT NULL,
            balance REAL DEFAULT 0.0,
            is_admin INTEGER DEFAULT 0,
            avatar TEXT
        )
    """)
    
    # Автоматическое добавление колонки role, если её нет
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
        db.commit()
        print("[DB] Колонка 'role' успешно добавлена в таблицу 'users'.")
    except sqlite3.OperationalError:
        # Колонка уже существует, пропускаем
        pass

    # (Здесь при необходимости можно добавить создание других таблиц, например contestants)
    
    db.commit()
    db.close()

# Запускаем инициализацию при старте приложения
init_db()

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================

def hash_password(password: str) -> str:
    """Хеширование пароля через SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def upload_db_to_dropbox():
    """Резервное копирование базы данных в Dropbox"""
    if not DROPBOX_ACCESS_TOKEN or DROPBOX_ACCESS_TOKEN == "ТВОЙ_DROPBOX_ТОКЕН":
        return
    
    url = "https://content.dropboxapi.com/2/files/upload"
    headers = {
        "Authorization": f"Bearer {DROPBOX_ACCESS_TOKEN}",
        "Dropbox-API-Arg": '{"path": "/database.db", "mode": "overwrite", "autorename": true, "mute": false}',
        "Content-Type": "application/octet-stream"
    }
    
    try:
        with open(DATABASE_FILE, "rb") as f:
            data = f.read()
        response = requests.post(url, headers=headers, data=data, timeout=10)
        if response.status_code == 200:
            print("[Dropbox] Резервная копия базы успешно загружена.")
        else:
            print(f"[Dropbox] Ошибка загрузки: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[Dropbox] Ошибка соединения: {e}")

# ================= СХЕМЫ ДАННЫХ (PYDANTIC) =================

class UserRegister(BaseModel):
    username: str
    password: str
    secret_answer: str
    role: Optional[str] = "user"  # "user" (зритель) или "model" (модель)

class UserLogin(BaseModel):
    username: str
    password: str

# ================= ЭНДПОИНТЫ АВТОРИЗАЦИИ =================

@app.post("/api/register")
async def api_register(data: UserRegister, db=Depends(get_db)):
    username = data.username.strip()
    password = data.password.strip()
    secret_answer = data.secret_answer.strip()
    role = data.role.strip() if data.role else "user"

    if not username or not password or not secret_answer:
        raise HTTPException(status_code=400, detail="Все поля обязательны для заполнения")

    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Пользователь с таким именем уже существует")

    hashed_pw = hash_password(password)
    try:
        cursor.execute(
            "INSERT INTO users (username, password, secret_answer, balance, role) VALUES (?, ?, ?, 0.0, ?)",
            (username, hashed_pw, secret_answer, role)
        )
        db.commit()
        
        # Резервное копирование в облако
        upload_db_to_dropbox()
        
        # Отправка логов в Telegram
        role_emoji = "📸" if role == "model" else "👤"
        try:
            message = f"🎉 **Новая регистрация на сайте!**\n{role_emoji} Роль: `{role}`\n👤 Логин: `{username}`"
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=3)
        except Exception as tg_err:
            print(f"[Telegram] Ошибка отправки уведомления: {tg_err}")

        return {"status": "success", "message": "Регистрация успешна!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {str(e)}")


@app.post("/api/login")
async def api_login(data: UserLogin, response: Response, db=Depends(get_db)):
    username = data.username.strip()
    password = data.password.strip()

    cursor = db.cursor()
    cursor.execute("SELECT password FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()

    if not row or row["password"] != hash_password(password):
        raise HTTPException(status_code=401, detail="Неверное имя пользователя или пароль")

    # Устанавливаем защищенную куку сессии на 30 дней
    response.set_cookie(
        key="session_user", 
        value=username, 
        max_age=2592000, 
        path="/", 
        httponly=True, 
        samesite="lax"
    )
    return {"status": "success", "message": "Вход выполнен успешно"}


@app.get("/api/me")
async def get_current_user_api(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    """
    Возвращает профиль текущего авторизованного пользователя на основе сессионной куки.
    Если пользователь не авторизован — отдает дефолтного 'Гостя' без ошибок 401,
    чтобы фронтенд мог корректно отобразить неавторизованное состояние.
    """
    if not session_user:
        return {"username": "Гость", "balance": 0, "is_admin": 0, "role": "user", "avatar": None}
        
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, username, balance, is_admin, role, avatar FROM users WHERE username = ?", 
        (session_user,)
    )
    user_row = cursor.fetchone()
    
    if not user_row:
        return {"username": "Гость", "balance": 0, "is_admin": 0, "role": "user", "avatar": None}
        
    return {
        "id": user_row["id"],
        "username": user_row["username"],
        "balance": user_row["balance"],
        "is_admin": user_row["is_admin"],
        "role": user_row["role"] if user_row["role"] else "user",
        "avatar": user_row["avatar"]
    }


@app.post("/api/logout")
async def api_logout(response: Response):
    # Удаляем куку авторизации
    response.delete_cookie(key="session_user", path="/")
    return {"status": "success", "message": "Вышли из системы"}
