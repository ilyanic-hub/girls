import os
import uuid
import sqlite3
import sys
import requests
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Response, Cookie, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
import traceback
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ================= НАСТОЯЩИЙ ЖЕСТКИЙ ДИСК RAILWAY =================
DATA_DIR = "/data"
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")

# Создаем папки с максимальными правами доступа для Linux
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.chmod(DATA_DIR, 0o777)
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    os.chmod(PHOTOS_DIR, 0o777)
except Exception as e:
    print(f"!!! ОШИБКА СОЗДАНИЯ ПАПОК НА ДИСКЕ: {e} !!!", file=sys.stdout)

DATABASE_PATH = os.path.join(DATA_DIR, "database.db")
print(f"--- ФИНАЛЬНЫЙ СТАБИЛЬНЫЙ ПУТЬ БАЗЫ ДАННЫХ: {DATABASE_PATH} ---", file=sys.stdout)
# =======================================================================
# =======================================================================
# =======================================================================
# =======================================================================

app = FastAPI(title="Photo Rating API")

app.mount("/static/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

TRYBIT_API_KEY = "ТВОЙ_API_КЛЮЧ"
TRYBIT_SHOP_ID = "ТВОЙ_SHOP_ID"
TRYBIT_URL = "https://api.trybit.com/v2/invoice/create"

# =======================================================================
# ==================== ИНТЕГРАЦИЯ GOOGLE DRIVE ==========================
# =======================================================================

GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS", "{}") # Если переменной нет, будет пустой JSON, а не None
GOOGLE_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID", "")

def get_drive_service():
    """Функция безопасной авторизации в Google Drive"""
    try:
        # Очищаем строку от возможных артефактов переноса строк в Railway
        clean_creds = GOOGLE_CREDS_JSON.strip()
        creds_dict = json.loads(clean_creds)
        
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, 
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"!!! ОШИБКА АВТОРИЗАЦИИ GOOGLE: {e} !!!", file=sys.stdout)
        raise HTTPException(status_code=500, detail=f"Ошибка авторизации Google API: {e}")

async def upload_to_google_drive(file: UploadFile, drive_service) -> str:
    """Вспомогательная функция для загрузки любого файла в облако"""
    content = await file.read()
    file_stream = io.BytesIO(content)
    
    file_metadata = {
        "name": f"{uuid.uuid4()}_{file.filename}",
        "parents": [GOOGLE_FOLDER_ID]
    }
    
    media = MediaIoBaseUpload(file_stream, mimetype=file.content_type, resumable=True)
    
    # Отправляем файл
    uploaded_file = drive_service.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()
    
    file_id = uploaded_file.get("id")
    
    # Делаем файл доступным всему интернету по ссылке
    drive_service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"}
    ).execute()
    
    # Возвращаем прямую ссылку на картинку для сайта
    return f"https://lh3.googleusercontent.com/u/0/d/{file_id}"

# =======================================================================
# =======================================================================

class AuthModel(BaseModel):
    username: str
    password: str

class DepositModel(BaseModel):
    amount: float

def get_db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    print("--- ИНИЦИАЛИЗАЦИЯ ЛОКАЛЬНОЙ БАЗЫ ДАННЫХ ---", file=sys.stdout)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contestants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            photo_url TEXT,
            votes_count INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_winners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            title_date TEXT NOT NULL,
            photo_url TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS winner_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            winner_id INTEGER NOT NULL,
            photo_url TEXT NOT NULL,
            FOREIGN KEY (winner_id) REFERENCES history_winners(id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            balance REAL DEFAULT 100.0,
            is_admin INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    
    cursor.execute("INSERT OR IGNORE INTO users (id, username, password, balance, is_admin) VALUES (1, 'admin', 'admin', 500.0, 1)")
    conn.commit()
    conn.close()
    print("--- БАЗА ДАННЫХ И ТАБЛИЦЫ УСПЕШНО СОЗДАНЫ ---", file=sys.stdout)

init_db()

@app.get("/")
async def index_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))

@app.get("/history")
async def history_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "history.html"))

@app.get("/admin")
async def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "admin.html"))

@app.get("/api/me")
def get_me(user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        return {"username": "Гость", "balance": 0.0, "is_admin": 0}
    cursor = db.cursor()
    cursor.execute("SELECT id, username, balance, is_admin FROM users WHERE id = ?", (int(user_id),))
    row = cursor.fetchone()
    if not row:
        return {"username": "Гость", "balance": 0.0, "is_admin": 0}
    return dict(row)

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
def api_login(data: AuthModel, response: Response, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ? AND password = ?", (data.username, data.password))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    response.set_cookie(key="user_id", value=str(row["id"]), httponly=True, max_age=86400)
    return {"status": "success"}

@app.post("/api/logout")
@app.get("/api/logout")
def api_logout(response: Response):
    redirect_response = RedirectResponse(url="/", status_code=303)
    redirect_response.delete_cookie(key="user_id", path="/")
    return redirect_response

@app.post("/api/deposit")
def api_deposit(data: DepositModel, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Не авторизован")
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Некорректная сумма")
        
    order_id = f"ORDER_{uuid.uuid4().hex[:10].upper()}"
    cursor = db.cursor()
    cursor.execute("INSERT INTO payments (id, user_id, amount, status) VALUES (?, ?, ?, 'pending')",
                   (order_id, int(user_id), data.amount))
    db.commit()
    
    headers = {"Authorization": f"Token {TRYBIT_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "amount": float(data.amount),
        "shop_id": TRYBIT_SHOP_ID,
        "currency": "RUB",
        "order_id": order_id,
        "description": f"Deposit for user ID {user_id}"
    }
    
    try:
        response = requests.post(TRYBIT_URL, json=payload, headers=headers, timeout=15)
        res_data = response.json()
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Ошибка крипто-шлюза: {err}")

    if response.status_code != 200 or "result" not in res_data:
        raise HTTPException(status_code=400, detail=f"Крипто-шлюз вернул ошибку: {response.text}")

    result_data = res_data.get("result", {})
    payment_url = result_data.get("link") or result_data.get("url")
    return {"payment_url": payment_url}

@app.post("/api/payment/trybit-callback")
async def trybit_callback(request: Request, db=Depends(get_db)):
    try:
        data = await request.json()
    except Exception:
        form_data = await request.form()
        data = dict(form_data)

    status = data.get("status")
    if status not in ["success", "paid"]:
        return "OK"

    order_id = data.get("order_id")
    amount = data.get("amount")

    cursor = db.cursor()
    cursor.execute("SELECT user_id, status FROM payments WHERE id = ?", (order_id,))
    payment = cursor.fetchone()

    if payment and payment["status"] == 'pending':
        user_id = payment["user_id"]
        try:
            cursor.execute("UPDATE payments SET status = 'success' WHERE id = ?", (order_id,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (float(amount), user_id))
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Database error")

    return "OK"

@app.get("/api/contestants")
def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, photo_url, votes_count FROM contestants ORDER BY votes_count DESC")
    rows = cursor.fetchall()
    return [dict(row) for row in rows]

@app.post("/api/vote")
def vote_for_girl(contestant_id: int, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Войдите в аккаунт, чтобы голосовать")
    cursor = db.cursor()
    cursor.execute("SELECT balance FROM users WHERE id = ?", (int(user_id),))
    user = cursor.fetchone()
    if not user or user["balance"] < 10:
        raise HTTPException(status_code=400, detail="Недостаточно средств!")
        
    try:
        cursor.execute("UPDATE users SET balance = balance - 10 WHERE id = ?", (int(user_id),))
        cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка при голосовании")
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
    
# ================= АДМИН-МЕТОДЫ С ИНТЕГРАЦИЕЙ GOOGLE DRIVE =================

import traceback

@app.post("/api/admin/contestants")
async def admin_add_contestant(
    name: str = Form(...), file: UploadFile = File(...), user_id: Optional[str] = Cookie(None), db=Depends(get_db)
):
    print(">>> ЗАПРОС ДОЛЕТЕЛ ДО БЭКЕНДА! Начинаем обработку...", file=sys.stdout)
    
    if not user_id: 
        print(">>> Ошибка: user_id не найден в Cookie", file=sys.stdout)
        raise HTTPException(status_code=401, detail="Не авторизован")
        
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    
    if not check or not check["is_admin"]: 
        print(f">>> Ошибка: пользователь {user_id} не админ", file=sys.stdout)
        raise HTTPException(status_code=403, detail="Нет прав")

    try:
        print(">>> Пробуем подключиться к Google Drive...", file=sys.stdout)
        drive_service = get_drive_service()
        
        print(f">>> Файл получен: {file.filename}, тип: {file.content_type}. Загружаем...", file=sys.stdout)
        photo_url = await upload_to_google_drive(file, drive_service)
        
        print(f">>> Файл успешно загружен! Ссылка: {photo_url}", file=sys.stdout)
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (name, photo_url))
        db.commit()
        
        print(">>> Данные успешно сохранены в SQLite!", file=sys.stdout)
        return {"status": "success"}
        
    except BaseException as err:
        print("!!! КРИТИЧЕСКИЙ СБОЙ ВНУТРИ МЕТОДА !!!", file=sys.stdout)
        traceback.print_exc(file=sys.stdout)  # Выведет полную трассировку ошибки со строками кода
        raise HTTPException(status_code=500, detail=f"Глобальная ошибка сервера: {str(err)}")

@app.delete("/api/admin/contestants/{girl_id}")
def admin_delete_contestant(girl_id: int, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: raise HTTPException(status_code=403)

    cursor.execute("DELETE FROM contestants WHERE id = ?", (girl_id,))
    db.commit()
    return {"status": "success"}

@app.post("/api/admin/history")
async def admin_add_history_winner(
    name: str = Form(...), title_date: str = Form(...), file: UploadFile = File(...),
    album_files: List[UploadFile] = File([]), user_id: Optional[str] = Cookie(None), db=Depends(get_db)
):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: raise HTTPException(status_code=403)

    try:
        drive_service = get_drive_service()
        
        # Загружаем главное фото королевы на Google Диск
        main_photo_url = await upload_to_google_drive(file, drive_service)
        
        cursor.execute("INSERT INTO history_winners (name, title_date, photo_url) VALUES (?, ?, ?)", (name, title_date, main_photo_url))
        winner_id = cursor.lastrowid
        
        # Загружаем фото из альбома на Google Диск
        for alb_file in album_files:
            if alb_file.filename:
                alb_photo_url = await upload_to_google_drive(alb_file, drive_service)
                cursor.execute("INSERT INTO winner_photos (winner_id, photo_url) VALUES (?, ?)", (winner_id, alb_photo_url))
                
        db.commit()
        return {"status": "success"}
    except Exception as err:
        print(f"!!! ОШИБКА ДОБАВЛЕНИЯ В АРХИВ: {err} !!!", file=sys.stdout)
        raise HTTPException(status_code=500, detail=f"Ошибка Google Drive при архивации: {err}")

@app.delete("/api/admin/history/{winner_id}")
def admin_delete_history_winner(winner_id: int, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: raise HTTPException(status_code=403)

    cursor.execute("DELETE FROM winner_photos WHERE winner_id = ?", (winner_id,))
    cursor.execute("DELETE FROM history_winners WHERE id = ?", (winner_id,))
    db.commit()
    return {"status": "success"}

@app.get("/force-admin")
def force_admin(response: Response, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    row = cursor.fetchone()
    response.set_cookie(key="user_id", value=str(row[0]), httponly=True, max_age=86400, path="/")
    return {"status": "Успешный вход под ADMIN через локальный SQLite!"}
