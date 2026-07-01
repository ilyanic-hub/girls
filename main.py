import os
import sys
import sqlite3
import hashlib
import dropbox
import time
import httpx
from fastapi import FastAPI, Depends, HTTPException, Response, Cookie, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
from pydantic import BaseModel
import requests

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
# ВСТАВЬ СЮДА СВОИ ДАННЫЕ:
DROPBOX_REFRESH_TOKEN = "ApXJY9sYu1MAAAAAAAAAAYk7D9NmgMi88qboNhpKNSGsh1conF6E4kBJicP4Web6"
DROPBOX_APP_KEY = "oou4gf2ktj2y51j"
DROPBOX_APP_SECRET = "dunglx7xl3el8pa"

DB_LOCAL_PATH = "database.db"
DB_DROPBOX_PATH = "/database.db"

def get_dropbox_client():
    try:
        # Теперь подключаемся правильно: используя Refresh Token, App Key и App Secret
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

   # Таблица для хранения баланса пользователей (привязка к IP)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_balances (
            user_ip TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 0
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
    
    # Если фото не передано (админ меняет только имя), обновляем только имя
    if len(inline_photo_url) < 50: # Обычное имя короткое, а Base64-карта огромная
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

# 1. ИСПРАВЛЕНО: Теперь этот роут сохраняет и главную фотку, и фотки альбома (если они есть)
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
        # Сохраняем главную обложку
        inline_photo_url = data.file_base64.replace("\n", "").replace("\r", "").strip()
        cursor.execute("INSERT INTO history (name, title_date, photo_url) VALUES (?, ?, ?)", (data.name, data.title_date, inline_photo_url))
        history_id = cursor.lastrowid # Получаем ID только что созданной королевы

        # Если админ прикрепил дополнительные фотки для альбома, сохраняем их
        if data.album_files:
            for album_file in data.album_files:
                file_url = album_file.file_base64.replace("\n", "").replace("\r", "").strip()
                if file_url:
                    cursor.execute("INSERT INTO history_photos (history_id, photo_url) VALUES (?, ?)", (history_id, file_url))

        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "message": "Добавлено в историю вместе с альбомом!"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

# 2. ДОБАВЛЕНО: Тот самый роут, который запрашивает твой новый history.html
@app.get("/api/history/{winner_id}/photos")
async def get_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    # Достаем все дополнительные фотографии для конкретной победительницы
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
    
    # Сначала удаляем фотки альбома, затем саму запись
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
        
        # Обновляем текстовые поля
        cursor.execute("UPDATE history SET name = ?, title_date = ? WHERE id = ?", (data.name, data.title_date, id))
        
        # Если передана новая главная фотография — обновляем её
        if len(inline_photo_url) > 50:
            cursor.execute("UPDATE history SET photo_url = ? WHERE id = ?", (inline_photo_url, id))
            
        # Если админ загрузил новые фото для альбома, старый альбом очищаем и пишем новый
        if data.album_files and len(data.album_files) > 0:
            cursor.execute("DELETE FROM history_photos WHERE history_id = ?", (id,))
            for album_file in data.album_files:
                file_url = album_file.file_base64.replace("\n", "").replace("\r", "").strip()
                if file_url:
                    cursor.execute("INSERT INTO history_photos (history_id, photo_url) VALUES (?, ?)", (id, file_url))
                    
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success", "message": "Запись в истории обновлена!"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))
       

class DepositSchema(BaseModel):
    amount: int  # Количество коинов для покупки (например, 10, 50, 100)

# 1. Получить текущий баланс пользователя
@app.get("/api/balance")
async def get_balance(request: Request, db=Depends(get_db)):
    user_ip = request.client.host
    cursor = db.cursor()
    cursor.execute("SELECT balance FROM user_balances WHERE user_ip = ?", (user_ip,))
    row = cursor.fetchone()
    
    if not row:
        # Если пользователя еще нет в таблице, его баланс равен 0
        cursor.execute("INSERT INTO user_balances (user_ip, balance) VALUES (?, 0)", (user_ip,))
        db.commit()
        return {"balance": 0}
        
    return {"balance": row["balance"]}

# 2. Создание инвойса (счета) на оплату в TryBit
@app.post("/api/payment/create")
async def create_payment(data: DepositSchema, request: Request, db=Depends(get_db)):
    user_ip = request.client.host
    
    # 1 коин = 1 рубль (так как на фронтенде у нас 100 коинов = 10 ₽, значит 1 коин = 0.1 ₽)
    # Если на фронтенде написано 100 коинов = 10 ₽, то стоимость в рублях — это количество коинов * 0.1
    price_per_coin = 0.1  
    total_price_rub = data.amount * price_per_coin
    
    # Твой API-ключ от TryBit (замени на свой реальный ключ в кавычках)
    TRYBIT_API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1dWlkIjoiTVRBM01EY3kiLCJ0eXBlIjoicHJvamVjdCIsInYiOiJiYmY5ODQ2YjM0YmUxYmJjOTUzYmE0OWJkNjA2YjhmYWQ4Nzc5NWUxNmVmZGRjYWExNDM2NWQ5NzRjNWZkYjNlIiwiZXhwIjo4ODE4MjMwNjY2OH0.ayDjkheCSfTy9m0BxrDA-i9jp3deXrIXp208Vp66Crw"
    
    # Формируем уникальный ID заказа, чтобы платежка вернула его нам в вебхуке
    # Например: deposit_127.0.0.1_1719823456
    order_id = f"deposit_{user_ip}_{int(time.time())}"
    
    # Сюда вставь URL твоего деплоя на Railway (обязательно с /api/payment/webhook на конце)
    CALLBACK_URL = "https://girls-production.up.railway.app/api/payment/webhook"
    
    headers = {
        "Authorization": f"Bearer {TRYBIT_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "amount": total_price_rub,
        "currency": "RUB", # Или "USDT", если баланс мерчанта в TryBit настроен в долларах/крипте
        "order_id": order_id,
        "callback_url": CALLBACK_URL
    }
    
    try:
        # Добавляем verify=False, чтобы исключить проблемы с SSL-сертификатами
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                "https://api.trybit.com/v2/invoice/create",
                json=payload,
                headers=headers,
                timeout=20.0  # Увеличили таймаут до 20 секунд
            )
            
        if response.status_code in [200, 201, 211]:
            res_data = response.json()
            # TryBit v2 может возвращать ссылку в разных полях. Проверяем все варианты:
            payment_url = (
                res_data.get("payment_url") or 
                res_data.get("url") or 
                res_data.get("data", {}).get("payment_url") or
                res_data.get("data", {}).get("url")
            )
            
            if payment_url:
                return {"status": "success", "payment_url": payment_url}
            else:
                # Если ссылка не найдена, выведем в ошибку весь ответ от TryBit, чтобы понять структуру
                return {"status": "error", "detail": f"Ответ платежки без ссылки: {res_data}"}
        else:
            return {"status": "error", "detail": f"Ошибка TryBit API: Код {response.status_code}, Ответ: {response.text}"}
            
    except Exception as e:
        # Выводим полную ошибку, чтобы точно знать, в чем затык
        return {"status": "error", "detail": f"Не удалось связаться с платежной системой: {type(e).__name__} - {str(e)}"}

# 3. WEBHOOK: Сюда TryBit пришлет секретный сигнал об успешной оплате
@app.post("/api/payment/webhook")
async def payment_webhook(request: Request, db=Depends(get_db)):
    # Получаем данные от платежной системы
    data = await request.json()
    
    # В реальном TryBit мы проверим секретную подпись (хеш), чтобы никто не подделал платеж.
    # Допустим, платежка прислала статус "COMPLETED" и инфо о пользователе
    payment_status = data.get("status") # например, "success" или "completed"
    
    if payment_status == "completed":
        # Вытаскиваем IP пользователя, который платил
        # (обычно передается в custom-полях или парсится из order_id)
        order_id = data.get("order_id", "") 
        # Допустим, вытащили IP из строки deposit_192.168.1.1_17198...
        user_ip = order_id.split("_")[1] 
        
        # Сколько коинов начислить (вычисляем из полученной суммы крипты)
        amount_paid = float(data.get("amount", 0))
        coins_to_add = int(amount_paid / 0.1) 
        
        cursor = db.cursor()
        # Начисляем коины на баланс IP-адреса
        cursor.execute("UPDATE user_balances SET balance = balance + ? WHERE user_ip = ?", (coins_to_add, user_ip))
        db.commit()
        
        # Синхронизируем обновленную базу с Dropbox, чтобы балансы не стерлись!
        upload_db_to_dropbox()
        
        return {"status": "accepted"}
        
    return {"status": "ignored"}
