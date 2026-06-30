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
DROPBOX_TOKEN = "sl.u.AGkB9N4cNfcCcQSox4WlVx417pW1lGjF9Fj1XlLf8eJyQCrWhoXrApefG5Qz43Rlg8mqHu0YYjEHQ9nc4luXlgECC-QBhAb9nXGy6cvSJC95xt_7G6QQqp7TToSgolekCue2CrAUU35d0exShIlWlbJ-kOnzWx7t53lR2nRYhOEPHcBGdjaZaSmlcl44PfP_FClu1_xF0lKc8-u35aSyTWq-qSCi_4MwcjY2iWRQdEiznJxDNQBokdeYVTFbyoovqMPdTjhiD3yaeiLcPGa5ZjregpPCw2xihV9OoegYvcOI-DFgwu7zR-RXN9wsFrrm38h-2RrjhqryQVBNO9N6lA7p6vD0nCSdSlXAuQ4NH-0I4kKXQYLHJeVhwtQbSsrqSQ-nnZ7rCDwi4gNV37sria6CBtx02l-CcbPEJNchJ0A5BnP1pJSCQTN6ibcLT_1PUB2LjELfHXXn5kjS2x2lUHpo0f_uf8r-gvbnUA7OBPEP4uUmivKtyqJjh92ly2zIDa_k0GoXPOp0XDPBNgMiD9-6N1JZn8Nn1jgxlcTn9o2Io2QuhGqh0jBJydAsPC9xQTiMEsFyQYAitLz7RUGDZF1qxANcMNKynAH5cWLf2itGnRcQ2u3AejJuQ-bYmkiv-6spOZAa8Ar8qlVTN_DF_fOsReQiIVwYAwWpTmMYzd0Gd_lxr9sre5XRRMvsYtNnK-0_gqClbU-ppa0fWnZATG7p5MzNct8y2f5RBXaJKZCbc0cgaaOfQS7-VaLKvsep7nWVIo2G1YN1L79yFuswqVOc0Jn4IKInGK3FQ_5mFYhZHbIriZ2gpuv3RcM8r6VmAezXd40_oeRD-Hf-Dl2-sjIMOeCF3pOKbkzE86Y3qU2CCJoCB-YKO0rxYh8o8JicUPppK6ILDxPv5EwH6jS7eqa-RjLeCT0CXc5UOQifOcVdUZ-eSBhU7T3sTpjKDBaSKVvNIXoZhLga4heUUK5xSH15_M0I4fbU1SzPyT7kat7mk3-M-AVpxdigH4LitKRhASm8kx_ogkMKli_hI_RSWoQY3vLkQ4ZDLC5XqlrgHx9DgDbqed0c3HtRwCsy2sQPOPw72dR-dqeoa9UmpTPPjMAS9Qi7hh0YQrC1LlDjAjbEaijtMoJ5jJwBeG9-dDmk8mTdDVpm4GXdN6D2DAW8z4kJlblPavG3JMISl_YUR4DbZ39SJrzhof_H6Dp1QMyVdd7zXTt6kzbuFJ7WmUYgPG0M3Th6BIh4lQ_ovOyZYB7vN32SjbFeF_XIlCA__4rjHvxfI2ypwflul7LBpLLGu75qyFg9PZKITsMRcFTwazIH-fc7jN2iOlCjYjR3b42WTykezdKGQT92Lkwh0WV7ADkeKNj7IZ5gYD7Chl5ZsmqgtWGwYreJZl67U_YzYluNXMXRgBzkGcIHJ0PVsqZNECGJRBIEj6WZzE8AHUmkUXkq6QyMnh6dIL4KEONs5WWwMSDor_qJ6WSRrneiWoXerk32mBNHpeEaksXVpPGBJyRmtg" 
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
    
    # Хэшируем пароль "admin"
    admin_password_hash = hashlib.sha256("admin".encode()).hexdigest()
    
    # ОБНОВЛЕНО: Если админ уже есть, мы принудительно обновляем ему пароль на правильный хэш
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

class ContestantSchema(BaseModel):
    name: str
    file_base64: str

# Функция-помощник для хэширования паролей
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
    # 1. Если куки пустые — сразу редирект на главную (к форме логина)
    if not session_user:
        return RedirectResponse(url="/", status_code=303)
        
    # 2. Проверяем в базе статус пользователя
    db = sqlite3.connect(DB_LOCAL_PATH)
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    db.close()
    
    # 3. Если пользователя нет в базе или он не админ — редирект на главную
    if not user or not user["is_admin"]:
        return RedirectResponse(url="/", status_code=303)
        
    # 4. И только если проверку прошёл настоящий админ — отдаем страницу
    path_to_html = "templates/admin.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл admin.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ================= НАСТОЯЩАЯ АВТОРИЗАЦИЯ И ПОЛЬЗОВАТЕЛИ =================

# Узнать, кто авторизован (через куки сессии)
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

# Регистрация нового аккаунта
@app.post("/api/register")
async def api_register(data: UserAuthSchema, db=Depends(get_db)):
    username = data.username.strip()
    password = data.password.strip()
    
    if len(username) < 3 or len(password) < 4:
        raise HTTPException(status_code=400, detail="Слишком короткое имя (мин. 3) или пароль (мин. 4)")
        
    cursor = db.cursor()
    # Проверяем, занято ли имя
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Пользователь с таким именем уже существует")
        
    # Сохраняем с хэшированным паролем и стартовым балансом (например, 100 монет для теста)
    password_hash = hash_password(password)
    try:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES (?, ?, 100.0, 0)", (username, password_hash))
        db.commit()
        upload_db_to_dropbox() # Синхронизируем с облаком
        return {"status": "success", "message": "Регистрация успешна!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка БД: {str(e)}")

# Логин (Вход)
@app.post("/api/login")
async def api_login(data: UserAuthSchema, response: Response, db=Depends(get_db)):
    username = data.username.strip()
    password = data.password.strip()
    
    cursor = db.cursor()
    cursor.execute("SELECT password, username FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    
    if not user or user["password"] != hash_password(password):
        raise HTTPException(status_code=400, detail="Неверное имя пользователя или пароль")
        
    # Ставим простую Cookie-сессию в браузер на 14 дней
    response.set_cookie(key="session_user", value=user["username"], max_age=1209600, path="/")
    return {"status": "success", "message": "Вход выполнен успешно!"}

# Выход из аккаунта
@app.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie(key="session_user", path="/")
    return {"status": "success", "message": "Вы вышли из системы"}

# Пополнение баланса авторизованного пользователя
@app.post("/api/deposit")
async def api_deposit(amount_data: dict, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Вы не авторизованы")
        
    amount = float(amount_data.get("amount", 100.0))
    cursor = db.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE username = ?", (amount, session_user))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success", "balance_added": amount}


# ================= КОНТЕНТ И ГОЛОСОВАНИЕ =================

@app.get("/api/contestants")
async def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM contestants")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/admin/contestants")
async def admin_add_contestant(data: ContestantSchema, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    # Проверка на админа
    if not session_user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="У вас нет прав администратора")

    name = data.name
    raw_photo = data.file_base64
    
    if not raw_photo or len(raw_photo.strip()) < 10:
        raise HTTPException(status_code=400, detail="Файл картинки пустой")
        
    inline_photo_url = raw_photo.replace("\n", "").replace("\r", "").strip()
    
    cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (name, inline_photo_url))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}

@app.post("/api/vote")
async def api_vote(contestant_id: int, session_user: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not session_user:
        raise HTTPException(status_code=401, detail="Чтобы голосовать, нужно войти в аккаунт")
        
    cursor = db.cursor()
    cursor.execute("SELECT balance FROM users WHERE username = ?", (session_user,))
    user = cursor.fetchone()
    
    if user and user["balance"] >= 10:
        # Списываем 10 монет у того, кто голосует
        cursor.execute("UPDATE users SET balance = balance - 10 WHERE username = ?", (session_user,))
        cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе (нужно 10 монет)")

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
