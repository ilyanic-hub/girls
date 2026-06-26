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
 
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ================= НАСТРОЙКА ЖЕСТКОГО ДИСКА (НОВЫЙ ПУТЬ) =================
# Если мы на сервере Railway, используем чистый корень /data, привязанный к Volume
if os.path.exists("/data"):
    DATA_DIR = "/data"
else:
    DATA_DIR = os.path.join(BASE_DIR, "data")

PHOTOS_DIR = os.path.join(DATA_DIR, "photos")

# Создаем папки, если их еще нет
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)

DATABASE_PATH = os.path.join(DATA_DIR, "database.db")
print(f"--- БАЗА ДАННЫХ ИНИЦИАЛИЗИРОВАНА ПО ПУТИ: {DATABASE_PATH} ---", file=sys.stdout)
print(f"--- ПАПКА ДЛЯ ФОТОГРАФИЙ: {PHOTOS_DIR} ---", file=sys.stdout)
# ==================================================================================

app = FastAPI(title="Photo Rating API")

# ПОДКЛЮЧАЕМ СТАТИКУ С ПОСТОЯННОГО ДИСКА
app.mount("/static/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# НАСТРОЙКИ TRYBIT (ОБЯЗАТЕЛЬНО ЛАТИНИЦА В ДЕПОЗИТЕ)
TRYBIT_API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1dWlkIjoiTVRBM01EY3kiLCJ0eXBlIjoicHJvamVjdCIsInYiOiJiYmY5ODQ2YjM0YmUxYmJjOTUzYmE0OWJkNjA2YjhmYWQ4Nzc5NWUxNmVmZGRjYWExNDM2NWQ5NzRjNWZkYjNlIiwiZXhwIjo4ODE4MjMwNjY2OH0.ayDjkheCSfTy9m0BxrDA-i9jp3deXrIXp208Vp66Crw"
TRYBIT_SHOP_ID = "7Z8Q5qj8f3PDS5iz"
TRYBIT_URL = "https://api.trybit.com/v2/invoice/create"
# Pydantic-модели
class AuthModel(BaseModel):
    username: str
    password: str

class DepositModel(BaseModel):
    amount: float

def get_db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Надежная инициализация базы данных с включением режима WAL."""
    print(f"--- ПРОВЕРКА И ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ: {DATABASE_PATH} ---", file=sys.stdout)
    
    conn = sqlite3.connect(DATABASE_PATH, timeout=20)
    cursor = conn.cursor()
    
    # Включаем WAL-режим (Write-Ahead Logging). 
    # Это запретит базе зануляться при перезапусках контейнера Railway!
    cursor.execute("PRAGMA journal_mode=WAL;")
    
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
    print("--- БАЗА ДАННЫХ УСПЕШНО НАСТРОЕНА В РЕЖИМЕ WAL И ГОТОВА ---", file=sys.stdout)
 init_db()




# ================= СТРАНИЦЫ (ФРОНТЕНД) =================

@app.get("/")
async def index_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))

@app.get("/history")
async def history_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "history.html"))

@app.get("/admin")
async def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "admin.html"))


# ================= АВТОРИЗАЦИЯ И ПОПОЛНЕНИЕ БАЛАНСА =================

@app.get("/api/me")
def get_me(user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        return {"username": "Гость", "balance": 0.0, "is_admin": 0}
        
    cursor = db.cursor()
    cursor.execute("SELECT id, username, balance, is_admin FROM users WHERE id = ?", (int(user_id),))
    user = cursor.fetchone()
    
    if not user:
        return {"username": "Гость", "balance": 0.0, "is_admin": 0}
        
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
def api_login(data: AuthModel, response: Response, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ? AND password = ?", (data.username, data.password))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    
    response.set_cookie(key="user_id", value=str(user["id"]), httponly=True, max_age=86400)
    return {"status": "success"}

@app.post("/api/logout")
@app.get("/api/logout")
def api_logout(response: Response):
    redirect_response = RedirectResponse(url="/", status_code=303)
    redirect_response.delete_cookie(key="user_id", path="/")
    return redirect_response


# --- ТО САМОЕ РАБОЧЕЕ ПОПОЛНЕНИЕ ЧЕРЕЗ TRYBIT V2 ---
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
    
    headers = {
        "Authorization": f"Token {TRYBIT_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "amount": float(data.amount),
        "shop_id": TRYBIT_SHOP_ID,
        "currency": "RUB",
        "order_id": order_id,
        "description": f"Deposit for user ID {user_id}"  # Использована латиница!
    }
    
    try:
        response = requests.post(TRYBIT_URL, json=payload, headers=headers, timeout=15)
        print("СТАТУС ОТВЕТА TRYBIT:", response.status_code, file=sys.stdout)
        print("ЛОГ ОТВЕТА TRYBIT:", response.text, file=sys.stdout)
        res_data = response.json()
    except Exception as err:
        print(f"Ошибка запроса к Trybit API: {err}", file=sys.stdout)
        raise HTTPException(status_code=500, detail=f"Ошибка крипто-шлюза: {err}")

    if response.status_code != 200 or "result" not in res_data:
        raise HTTPException(status_code=400, detail=f"Крипто-шлюз вернул ошибку: {response.text}")

    result_data = res_data.get("result", {})
    payment_url = result_data.get("link") or result_data.get("url")
    
    if not payment_url:
        raise HTTPException(status_code=500, detail="Шлюз не вернул ссылку на платежную страницу")

    return {"payment_url": payment_url}


# --- ОБРАБОТЧИК УСПЕШНОЙ ОПЛАТЫ (WEBHOOK) ДЛЯ НАЧИСЛЕНИЯ БАЛАНСА ---
@app.post("/api/payment/trybit-callback")
async def trybit_callback(request: Request, db=Depends(get_db)):
    try:
        data = await request.json()
    except Exception:
        form_data = await request.form()
        data = dict(form_data)

    print("ПОЛУЧЕН CALLBACK ОТ TRYBIT:", data, file=sys.stdout)

    status = data.get("status")
    if status not in ["success", "paid"]:
        return "OK"

    order_id = data.get("order_id")
    amount = data.get("amount")

    if not order_id:
        raise HTTPException(status_code=400, detail="Missing order_id")

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
            print(f"Баланс пользователя {user_id} успешно пополнен на {amount} через Trybit!", file=sys.stdout)
        except Exception as e:
            db.rollback()
            print(f"Ошибка базы данных при обработке callback: {e}", file=sys.stdout)
            raise HTTPException(status_code=500, detail="Database error")

    return "OK"


# ================= ПУБЛИЧНЫЙ API ДЛЯ ГАЛЕРЕИ И ГОЛОСОВАНИЯ =================

@app.get("/api/contestants")
def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, photo_url, votes_count FROM contestants ORDER BY votes_count DESC")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/vote")
def vote_for_girl(contestant_id: int, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Войдите в аккаунт, чтобы голосовать")
        
    cursor = db.cursor()
    cursor.execute("SELECT balance FROM users WHERE id = ?", (int(user_id),))
    user = cursor.fetchone()
    if not user or user["balance"] < 10:
        raise HTTPException(status_code=400, detail="Недостаточно средств. Пополните баланс (стоимость 10₽)!")
        
    cursor.execute("SELECT id FROM contestants WHERE id = ?", (contestant_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Участница не найдена")
    
    cursor.execute("UPDATE users SET balance = balance - 10 WHERE id = ?", (int(user_id),))
    cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
    db.commit()
    return {"status": "success"}

@app.get("/api/history")
def get_history_winners(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, title_date, photo_url FROM history_winners ORDER BY id DESC")
    return [dict(row) for row in cursor.fetchall()]

@app.get("/api/history/{winner_id}/photos")
def get_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT photo_url FROM winner_photos WHERE winner_id = ? ORDER BY id ASC", (winner_id,))
    return [row["photo_url"] for row in cursor.fetchall()]


# ================= АДМИН-ПАНЕЛЬ (УПРАВЛЕНИЕ УЧАСТНИЦАМИ) =================

@app.post("/api/admin/contestants")
async def admin_add_contestant(
    name: str = Form(...),
    file: UploadFile = File(...),
    user_id: Optional[str] = Cookie(None),
    db=Depends(get_db)
):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")

    try:
        file_ext = os.path.splitext(file.filename)[1]
        filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(PHOTOS_DIR, filename)
        
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
            
        photo_url = f"/static/photos/{filename}"
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (name, photo_url))
        db.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка добавления: {str(e)}")

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


# ================= ЗАЛ СЛАВЫ / АРХИВ (УПРАВЛЕНИЕ КОРОЛЕВАМИ) =================

@app.post("/api/admin/history")
async def admin_add_history_winner(
    name: str = Form(...),
    title_date: str = Form(...),
    file: UploadFile = File(...),
    album_files: List[UploadFile] = File([]),
    user_id: Optional[str] = Cookie(None),
    db=Depends(get_db)
):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: raise HTTPException(status_code=403)

    try:
        main_ext = os.path.splitext(file.filename)[1]
        main_filename = f"winner_{uuid.uuid4()}{main_ext}"
        main_path = os.path.join(PHOTOS_DIR, main_filename)
        
        main_content = await file.read()
        with open(main_path, "wb") as f:
            f.write(main_content)
        main_photo_url = f"/static/photos/{main_filename}"
        
        cursor.execute("INSERT INTO history_winners (name, title_date, photo_url) VALUES (?, ?, ?)", (name, title_date, main_photo_url))
        winner_id = cursor.lastrowid
        
        for alb_file in album_files:
            if alb_file.filename:
                alb_ext = os.path.splitext(alb_file.filename)[1]
                alb_filename = f"alb_{uuid.uuid4()}{alb_ext}"
                alb_path = os.path.join(PHOTOS_DIR, alb_filename)
                
                alb_content = await alb_file.read()
                with open(alb_path, "wb") as f:
                    f.write(alb_content)
                cursor.execute("INSERT INTO winner_photos (winner_id, photo_url) VALUES (?, ?)", (winner_id, f"/static/photos/{alb_filename}"))
                
        db.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка архива: {str(e)}")

@app.delete("/api/admin/history/{winner_id}")
def admin_delete_history_winner(winner_id: int, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: raise HTTPException(status_code=403)

    cursor.execute("DELETE FROM history_winners WHERE id = ?", (winner_id,))
    cursor.execute("DELETE FROM winner_photos WHERE winner_id = ?", (winner_id,))
    db.commit()
    return {"status": "success"}


# ================= ЭНДПОИНТ ДЛЯ АВТОРИЗАЦИИ АДМИНА =================

@app.get("/force-admin")
def force_admin(response: Response, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    user = cursor.fetchone()
    if user:
        response.set_cookie(key="user_id", value=str(user["id"]), httponly=True, max_age=86400)
        return {"status": "Вы успешно авторизованы как ADMIN через Cookies!"}
    return {"status": "Админ не найден"}
