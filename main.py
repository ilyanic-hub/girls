import os
import sys
import sqlite3
import hashlib
import dropbox
import time
import httpx
from pydantic import BaseModel
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Response, Cookie, Request, UploadFile, File, APIRouter
from fastapi.templating import Jinja2Templates
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
from pydantic import BaseModel

app = FastAPI()
router = APIRouter()

TELEGRAM_BOT_TOKEN = "8923888437:AAEsIYtyGYT3kSE7ZDAS8s84O9YRhpPdGB0"
TELEGRAM_CHAT_ID = "8501380785"

def send_telegram_notification(username: str):
    """Отправляет уведомление в Telegram о новом пользователе"""
    try:
        message = f"🎉 **Новая регистрация на сайте!**\n👤 Пользователь: `{username}`"
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        # Отправляем асинхронно или через обычный requests (таймаут 3 сек, чтобы сайт не завис)
        requests.post(url, json=payload, timeout=3)
    except Exception as e:
        print(f"Ошибка отправки уведомления в Telegram: {e}")


# Папка, куда будут сохраняться аватарки
UPLOAD_DIR = "static/avatars"
os.makedirs(UPLOAD_DIR, exist_ok=True)
templates = Jinja2Templates(directory="templates")
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

# Монтирование статических файлов
if os.path.exists("templates"):
    app.mount("/templates", StaticFiles(directory="templates"), name="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


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

# Сначала СКАЧИВАЕМ базу, чтобы не затереть данные!
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
        secret_answer TEXT DEFAULT NULL,
        avatar TEXT DEFAULT NULL
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

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS adult_models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        age INTEGER NOT NULL,
        status TEXT NOT NULL,
        photo_url TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS adult_model_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id INTEGER NOT NULL,
        photo_url TEXT NOT NULL,
        FOREIGN KEY (model_id) REFERENCES adult_models(id) ON DELETE CASCADE
    )
    """)

    cursor.execute("CREATE TABLE IF NOT EXISTS user_purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, model_id INTEGER)")
    
    try:
        cursor = db.cursor()
        cursor.execute("PRAGMA table_info(adult_models)")
        columns = cursor.fetchall()
    
        # Ровно 4 пробела (или 1 таб) от края try
        has_is_paid = any(col[1] == 'is_paid' for col in columns)
        
        if not has_is_paid:
            print("Колонка is_paid не найдена. Добавляю...")
            cursor.execute("ALTER TABLE adult_models ADD COLUMN is_paid INTEGER DEFAULT 0")
            db.commit()
            print("Колонка is_paid успешно добавлена.")
    except Exception as e:
        # try и except находятся НА ОДНОЙ ВЕРТИКАЛЬНОЙ ЛИНИИ
        print(f"Ошибка при проверке/добавлении колонки is_paid: {e}")
    
    # Накатываем альтеры на случай старых баз
    try:    
        cursor.execute("ALTER TABLE users ADD COLUMN last_bonus_date TEXT DEFAULT NULL")
        db.commit()
    except sqlite3.OperationalError: pass

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN secret_answer TEXT DEFAULT NULL")
        db.commit()
    except sqlite3.OperationalError: pass

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT NULL")
        db.commit()
    except sqlite3.OperationalError: pass
        
    admin_password_hash = hashlib.sha256("admin".encode()).hexdigest()
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if cursor.fetchone():
        cursor.execute("UPDATE users SET password = ? WHERE username = 'admin'", (admin_password_hash,))
    else:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES ('admin', ?, 500.0, 1)", (admin_password_hash,))

    # === ДОБАВИТЬ В КОНЕЦ ФУНКЦИИ init_db() ===
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    # Записываем текущий месяц (например, "2026-07"), если записи ещё нет
    cursor.execute("SELECT value FROM system_settings WHERE key = 'current_round_month'")
    if not cursor.fetchone():
        current_month = datetime.now().strftime("%Y-%m")
        cursor.execute("INSERT INTO system_settings (key, value) VALUES ('current_round_month', ?)", (current_month,))
        
    db.commit()
    db.close()
    # upload_db_to_dropbox() <-- ТУТ ЭТО УДАЛЕНО, чтобы пустая база не летела в облако!

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

class AdultModelSchema(BaseModel):
    name: str
    age: int
    status: str
    file_base64: str
    is_paid: int = 100  # Добавляем это поле (0 — бесплатно, 1 — за токены)

class BuyModelRequest(BaseModel):
    model_id: int

class PhotoLinkSchema(BaseModel):
    photo_url: str

@app.post("/api/admin//{model_id}/photos-link")
def add_photo_link(model_id: int, data: PhotoLinkSchema, db = Depends(get_db)):
    cursor = db.cursor()
    # Просто сохраняем готовую ссылку на Dropbox в таблицу фотографий
    cursor.execute(
        "INSERT INTO adult_photos (model_id, photo_url) VALUES (?, ?)", 
        (model_id, data.photo_url)
    )
    db.commit()
    return {"status": "success"}


# ================= ЗАВИСИМОСТЬ АВТОРИЗАЦИИ =================
def get_current_user(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    cursor = db.cursor()
    cursor.execute("SELECT id, username FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    
    return user


# ================= ЭНДПОИНТ ЗАГРУЗКИ АВАТАРА =================
@app.post("/api/user/avatar")
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Файл должен быть изображением")
    
    username = user["username"]
    file_extension = os.path.splitext(file.filename)[1]
    if not file_extension:
        file_extension = ".jpg"
        
    filename = f"avatar_{username}{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    try:
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при сохранении файла: {str(e)}")
    
    avatar_url = f"/static/avatars/{filename}"
    
    try:
        cursor = db.cursor()
        cursor.execute("UPDATE users SET avatar = ? WHERE username = ?", (avatar_url, username))
        db.commit()
        upload_db_to_dropbox()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при обновлении базы данных: {str(e)}")
    
    return {"status": "success", "avatar_url": avatar_url}


# ================= РОУТЫ СТРАНИЦ СЕРВЕРА =================
@app.get("/api/debug-users-table")
async def debug_users_table(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("PRAGMA table_info(users)")
    columns = [dict(row) for row in cursor.fetchall()]
    return {"columns": columns}

@app.post("/api//buy")
async def buy_adult_model_access(data: BuyModelRequest, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    cursor = db.cursor()
    
    try:
        # Автоматически создаем таблицу покупок, если её нет
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            model_id INTEGER
        )
        """)
        db.commit()

        # 1. Проверяем баланс пользователя (ИСПРАВЛЕНО: используем balance вместо tokens)
        cursor.execute("SELECT balance FROM users WHERE username = ?", (session_user,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        
        # Проверяем, хватает ли средств (баланс из базы сравниваем с ценой в 50 единиц)
        if user["balance"] < 50:
            return {"status": "error", "message": "Недостаточно средств! Пополните баланс."}
        
        # 2. Проверяем, не куплена ли модель уже ранее
        cursor.execute(
            "SELECT id FROM user_purchases WHERE username = ? AND model_id = ?", 
            (session_user, data.model_id)
        )
        already_bought = cursor.fetchone()
        if already_bought:
            return {"status": "success", "message": "Доступ уже был куплен ранее!"}
        
        # 3. Списываем 50 единиц с баланса (ИСПРАВЛЕНО: обновляем поле balance)
        cursor.execute("UPDATE users SET balance = balance - 50 WHERE username = ?", (session_user,))
        
        # 4. Записываем покупку в базу
        cursor.execute(
            "INSERT INTO user_purchases (username, model_id) VALUES (?, ?)", 
            (session_user, data.model_id)
        )
        
        db.commit()
        
        if "upload_db_to_dropbox" in globals():
            upload_db_to_dropbox()
            
        return {"status": "success", "message": "Доступ успешно разблокирован!"}
        
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": f"Ошибка на стороне сервера БД: {str(e)}"}

@app.get("/api/admin/get--list")
async def get_adult_models_list_for_admin(db=Depends(get_db)):
    try:
        cursor = db.cursor()
        # ВАЖНО: Добавили поле is_paid в SELECT
        cursor.execute("SELECT id, name, age, status, photo_url, is_paid FROM adult_models ORDER BY id DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        return {"status": "error", "message": f"Ошибка БД: {str(e)}"}

# 1. Создаем схему данных, которую ждет FastAPI от фронтенда
class PhotoLinkSchema(BaseModel):
    photo_url: str

# 2. Создаем сам эндпоинт для сохранения ссылки в БД adult-models

@app.get("/api/admin/adult-models/{model_id}/photos")
async def get_adult_model_photos(model_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    try:
        # Проверяем, как называется таблица. Обычно это adult_albums или adult_photos
        cursor.execute("SELECT id, photo_url FROM adult_model_photos WHERE model_id = ? ORDER BY id DESC", (model_id,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        return {"status": "error", "message": f"Ошибка БД: {str(e)}"}

# Этот роут будет отдавать фотографии альбома для обычной страницы сайта
@app.get("/api/adult-models/{model_id}/photos")
async def get_adult_model_photos_public(model_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    try:
        cursor.execute("SELECT id, photo_url FROM adult_model_photos WHERE model_id = ? ORDER BY id DESC", (model_id,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        return {"status": "error", "message": f"Ошибка БД: {str(e)}"}

@app.delete("/api/admin/adult-photos/{photo_id}")
async def delete_adult_photo(photo_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM adult_model_photos WHERE id = ?", (photo_id,))
        db.commit()
        return {"status": "success", "message": "Фото удалено из альбома"}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка удаления: {str(e)}"}

import traceback

def check_and_rotate_round(db):
    cursor = db.cursor()
    
    # 1. Получаем месяц текущего активного раунда из БД
    cursor.execute("SELECT value FROM system_settings WHERE key = 'current_round_month'")
    row = cursor.fetchone()
    if not row:
        return
    
    saved_month = row["value"] # Строка вида "2026-07"
    # Закомментируй реальную строку и поставь искусственную дату для теста:
    # current_month = datetime.now().strftime("%Y-%m")
    current_month = "2026-08"
    
    # Если календарный месяц изменился — закрываем старый раунд!
    if current_month != saved_month:
        try:
            print(f"=== ОБНАРУЖЕН НАСТУПИВШИЙ НОВЫЙ МЕСЯЦ! Закрываем раунд {saved_month} ===")
            
            # Находим участницу с максимальным количеством голосов
            cursor.execute("SELECT name, photo_url, votes_count FROM contestants ORDER BY votes_count DESC LIMIT 1")
            winner = cursor.fetchone()
            
            if winner and winner["votes_count"] > 0:
                # Красиво форматируем дату для Зала славы (например: "Июнь 2026")
                # Для простоты запишем текстом прошлый месяц:
                title_date = f"Раунд {saved_month}"
                
                # Переносим победительницу в историю (Зал славы)
                cursor.execute(
                    "INSERT INTO history (name, title_date, photo_url) VALUES (?, ?, ?)",
                    (winner["name"], title_date, winner["photo_url"])
                )
                print(f"Победительница {winner['name']} перенесена в Зал славы.")
            
            # Обнуляем голоса у ВСЕХ участниц для нового раунда
            cursor.execute("UPDATE contestants SET votes_count = 0")
            
            # Обновляем текущий рабочий месяц в настройках системы
            cursor.execute("UPDATE system_settings SET value = ? WHERE key = 'current_round_month'", (current_month,))
            
            db.commit()
            print("=== РАУНД УСПЕШНО ОБНОВЛЕН ДЛЯ НОВОГО МЕСЯЦА ===")
            
            if "upload_db_to_dropbox" in globals():
                upload_db_to_dropbox()
                
        except Exception as e:
            db.rollback()
            print(f"Ошибка при автоматической смене раунда: {e}")


@app.get("/", response_class=HTMLResponse)
async def get_main_page(request: Request, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    path_to_html = "templates/index.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл index.html не найден!</h1>", status_code=404)
        
    # === АВТОМАТИЧЕСКАЯ ПРОВЕРКА И СМЕНА РАУНДА ТУТ ===
    check_and_rotate_round(db)
    
    user_data = None
    if session_user:
        try:
            cursor = db.cursor()
            cursor.execute("SELECT username, balance FROM users WHERE username = ?", (session_user,))
            row = cursor.fetchone()
            
            if row:
                user_data = {
                    "username": row[0],
                    "balance": row[1]
                }
        except Exception as e:
            print(f"Ошибка БД на главной: {e}")

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": user_data}
    )

@app.get("/top", response_class=HTMLResponse)
@app.get("/top/", response_class=HTMLResponse)
async def get_top_page():
    path_to_html = "templates/top.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл top.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# ЕДИНСТВЕННЫЙ И ПРАВИЛЬНЫЙ РОУТ ДЛЯ СТРАНИЦЫ МModels 18+ (старые дубли удалены ниже)
# ====================================================================
# 1. СТРАНИЦА КОНТЕНТА 18+ (Текущий адрес изменен на /18plus)
# ====================================================================
@app.get("/18plus", response_class=HTMLResponse)
@app.get("/18plus/", response_class=HTMLResponse)
async def get_adult_page(request: Request, db=Depends(get_db)):
    try:
        cursor = db.cursor()
        cursor.execute("SELECT * FROM adult_models ORDER BY id DESC")
        rows = cursor.fetchall()
        models_list = [dict(row) for row in rows]
        
        if not models_list:
            models_list = [
                {"name": "Алина", "age": 21, "status": "🔥 Фото и видео", "photo_url": "/static/avatars/default.jpg"},
                {"name": "Катя", "age": 24, "status": "💬 Приватные чаты", "photo_url": "/static/avatars/default.jpg"}
            ]

        return templates.TemplateResponse(
            request=request, 
            name="18plus.html", 
            context={"request": request, "models": models_list}
        )
    except Exception as e:
        return HTMLResponse(content=f"<h1>Ошибка бэкенда: {str(e)}</h1>", status_code=500)


# ====================================================================
# 2. ИНФОРМАЦИОННАЯ СТРАНИЦА ДЛЯ МОДЕЛЕЙ (Адрес /models)
# ====================================================================
@app.get("/models", response_class=HTMLResponse)
@app.get("/models/", response_class=HTMLResponse)
async def get_models_info_page():
    path_to_html = "templates/models.html" # Убедись, что твой новый HTML файл называется именно так!
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл models.html не найден в папке templates!</h1>", status_code=404)
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

@app.put("/api/admin/adult-models/{model_id}")
async def edit_adult_model(model_id: int, payload: dict, db=Depends(get_db)):
    cursor = db.cursor()
    name = payload.get("name")
    age = payload.get("age")
    status = payload.get("status")
    file_base64 = payload.get("file_base64")
    
    try:
        if file_base64 and file_base64.strip():
            # 1. Сюда нужно подставить твою функцию сохранения фото.
            # Например, если у тебя есть функция upload_to_imgur(file_base64), используй её.
            # Пока запишем сохранение самого base64 или вызов твоей функции:
            photo_url = file_base64  # Меняешь на вызов своей функции загрузки, если нужно
            
            cursor.execute(
                "UPDATE adult_model_photos SET name=?, age=?, status=?, photo_url=? WHERE id=?",
                (name, age, status, photo_url, model_id)
            )
        else:
            # Если файл не передали, обновляем только текстовые поля, не трогая старую аватарку
            cursor.execute(
                "UPDATE adult_models SET name=?, age=?, status=? WHERE id=?",
                (name, age, status, model_id)
            )
        
        db.commit()
        return {"status": "success", "message": "Данные модели успешно обновлены"}
        
    except Exception as e:
        return {"status": "error", "message": f"Ошибка базы данных: {str(e)}"}

# 2. Удаление модели 18+ через админку (Обновлённый роут)
@app.delete("/api/admin/adult-models/{id}")
async def delete_adult_model(id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    print(f"=== ЗАПРОС НА УДАЛЕНИЕ МОДЕЛИ С ID: {id} ===")
    
    # 1. Удаляем саму модель из главной таблицы моделей по её id
    cursor.execute("DELETE FROM adult_models WHERE id = ?", (id,))
    
    # 2. Удаляем все фотографии, привязанные к этой модели в таблице альбомов
    cursor.execute("DELETE FROM adult_model_photos WHERE model_id = ?", (id,))
    
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": f"Модель {id} и её фотографии полностью удалены"}

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


# ================= КОММЕНТАРИИ =================
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
async def api_get_me(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        return {"username": "Гость", "balance": 0, "is_admin": 0}
    
    cursor = db.cursor()
    cursor.execute("SELECT username, balance, is_admin FROM users WHERE username = ?", (session_user,))
    row = cursor.fetchone()
    if not row:
        return {"username": "Гость", "balance": 0, "is_admin": 0}
        
    return {
        "username": row[0],
        "balance": row[1],
        "is_admin": row[2]
    }

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
        
        # === ВСТАВЛЯЕМ ОТПРАВКУ УВЕДОМЛЕНИЯ ТУТ ===
        send_telegram_notification(username)
        
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




# ================= РАБОТА С УЧАСТНИЦАМИ =================
@app.get("/api/contestants")
async def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
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

# 1. Добавление модели 18+ через админку
# 1. Добавление модели 18+ через админку (с поддержкой платного контента)
@app.post("/api/admin/adult-models")
async def admin_add_adult_model(data: AdultModelSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
    
    cursor.execute(
        "INSERT INTO adult_models (name, age, status, photo_url, is_paid) VALUES (?, ?, ?, ?, ?)", 
        (data.name, data.age, data.status, inline_photo_url, data.is_paid)
    )
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": "Модель успешно добавлена!"}
    
# 2. Удаление модели 18+ через админку
@app.delete("/api/admin/adult-models/{id}")
async def delete_adult_model(id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    # ИСПРАВЛЕНО: удаляем из adult_models, а не из таблицы фотографий альбома
    cursor.execute("DELETE FROM adult_models WHERE id = ?", (id,))
    # Дополнительно удаляем её фотографии альбома, чтобы не засорять БД
    cursor.execute("DELETE FROM adult_model_photos WHERE model_id = ?", (id,))
    
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "message": "Модель и её альбом удалены"}

# 3. Загрузка фото в альбом конкретной модели
@app.post("/api/admin/adult-models/{model_id}/upload-photo")
async def admin_upload_adult_model_photo(model_id: int, data: AlbumFileSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
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
                "INSERT INTO adult_model_photos (model_id, photo_url) VALUES (?, ?)", 
                (int(model_id), str(file_url))
            )
            db.commit()
            upload_db_to_dropbox()
            return {"status": "success", "message": "Фото успешно добавлено в альбом модели!"}
        else:
            return {"status": "error", "message": "Пустая строка Base64"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

# 4. Получение всех фотографий из альбома конкретной модели (для пользователей и админки)
@app.get("/api/adult-models/{model_id}/photos")
async def get_adult_model_photos(model_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    # ИСПРАВЛЕНО: возвращаем id и photo_url словарём, чтобы фронтенд (p.photo_url) корректно читал данные
    cursor.execute("SELECT id, photo_url FROM adult_model_photos WHERE model_id = ? ORDER BY id DESC", (model_id,))
    rows = cursor.fetchall()
    return [dict(row) for row in rows]

# 5. Эндпоинт для отображения списка моделей в админке
@app.get("/api/admin/get-adult-models-list")
async def get_adult_models_list_for_admin(db=Depends(get_db)):
    cursor = db.cursor()
    # ИСПРАВЛЕНО: тянем данные из правильной таблицы adult_models и добавили выборку поля is_paid
    cursor.execute("SELECT id, name, age, status, photo_url, is_paid FROM adult_models ORDER BY id DESC")
    return [dict(row) for row in cursor.fetchall()]

# 6. НОВЫЙ РОУТ: Публичный список моделей для страницы 18+.html
@app.get("/api/adult-models")
async def get_adult_models_public(session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    try:
        cursor = db.cursor()
        
        # Тянем все модели из базы
        cursor.execute("SELECT id, name, age, status, photo_url, is_paid FROM adult_models ORDER BY id DESC")
        models = [dict(row) for row in cursor.fetchall()]
        
        # Если пользователь авторизован, получаем список ID моделей, которые он уже купил
        bought_ids = []
        if session_user:
            cursor.execute("SELECT model_id FROM user_purchases WHERE username = ?", (session_user,))
            bought_ids = [row["model_id"] for row in cursor.fetchall()]
        
        # Добавляем в каждую карточку метку: если модель платная, но уже куплена, ставим ей is_paid = 0
        for model in models:
            if model["id"] in bought_ids:
                model["is_paid"] = 0  # Снимаем блокировку, так как доступ уже куплен
                
        return models
    except Exception as e:
        return {"status": "error", "message": f"Ошибка БД: {str(e)}"}


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
        return {"status": "success", "message": "Альбом успешно очищен"}
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

@app.post("/api/adult-models/buy")
async def buy_adult_model_access(data: BuyModelRequest, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    cursor = db.cursor()
    
    # 1. Проверяем баланс пользователя
    cursor.execute("SELECT tokens FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    if user["tokens"] < 50:
        return {"status": "error", "message": "Недостаточно токенов! Пополните баланс."}
    
    # 2. Проверяем, не куплена ли модель уже
    cursor.execute(
        "SELECT id FROM user_purchases WHERE username = ? AND model_id = ?", 
        (session_user, data.model_id)
    )
    already_bought = cursor.fetchone()
    if already_bought:
        return {"status": "success", "message": "Доступ уже был куплен ранее"}
    
    try:
        # 3. Списываем 50 токенов
        cursor.execute("UPDATE users SET tokens = tokens - 50 WHERE username = ?", (session_user,))
        
        # 4. Записываем покупку в базу (убедись, что у тебя есть таблица user_purchases)
        cursor.execute(
            "INSERT INTO user_purchases (username, model_id) VALUES (?, ?)", 
            (session_user, data.model_id)
        )
        
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "message": "Доступ успешно разблокирован!"}
        
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": f"Ошибка транзакции: {str(e)}"}


# ================= СТАТУС КОНКУРСА (ТАЙМЕР) =================
@app.get("/api/contest-status")
async def get_contest_status():
    try:
        tz = pytz.timezone("Europe/Kiev") 
        deadline = datetime(2026, 7, 20, 23, 59, 0)
        deadline_localized = tz.localize(deadline)
        
        return {
            "status": "active",
            "deadline_iso": deadline_localized.isoformat()
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Ошибка таймера: {str(e)}"})


# ================= ПЛАТЕЖИ PLISIO =================
@app.post("/api/deposit") # Теперь старый адрес ведет на правильную платежку!
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

        coins_amount = body_data.get("amount") or body_data.get("amount_usd") or request.query_params.get("amount")
        if not coins_amount:
            raise HTTPException(status_code=400, detail="Не указана сумма перевода (amount)")

        amount_usd = float(coins_amount) / 100.0

        if not session_user:
            raise HTTPException(status_code=401, detail="Пользователь не авторизован. Войдите в аккаунт перед оплатой.")
            
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (session_user,))
        user_row = cursor.fetchone()
        
        if not user_row:
            raise HTTPException(status_code=404, detail="Авторизованный пользователь не найден в базе данных")
            
        user_id = user_row["id"]
        order_id = f"order_{user_id}_{int(time.time())}" 
        
        params = {
            "api_key": PLISIO_API_TOKEN,
            "source_currency": "USD",       
            "source_amount": f"{amount_usd:.2f}",
            "order_number": order_id,        
            "order_name": f"Пополнение коинов ({coins_amount} шт.) для пользователя {session_user}",
            "callback_url": "https://www.photo-rating.club/api/payment/plisio-callback"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get("https://plisio.net/api/v1/invoices/new", params=params)
            res_data = response.json()
        
        print("=== ПОЛНЫЙ ОТВЕТ ОТ PLISIO ===", res_data)
        
        if res_data.get("status") == "success":
            return {"payment_url": res_data["data"]["invoice_url"]}
        else:
            plisio_error = res_data.get("data", {}).get("message", "Неизвестная ошибка")
            print(f"Ошибка Plisio API: {res_data}")
            raise HTTPException(status_code=400, detail=f"Ошибка платежной системы: {plisio_error}")
            
    # === ВОТ ЭТИХ СТРОК ТУТ НЕ ХВАТАЛО, ЧТОБЫ ЗАКРЫТЬ TRY ===
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
