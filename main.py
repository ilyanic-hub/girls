import os
import uuid
import sys
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Response, Cookie, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Railway автоматически передает эту переменную, если к сервису подключен Postgres
DATABASE_URL = os.getenv("DATABASE_URL")

# Папка для фотографий
if os.path.exists("/data"):
    DATA_DIR = "/data"
else:
    DATA_DIR = os.path.join(BASE_DIR, "data")

PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)

app = FastAPI(title="Photo Rating API")

# Подключаем статические файлы
app.mount("/static/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# ================= НАСТРОЙКИ TRYBIT =================
TRYBIT_API_KEY = "ТВОЙ_API_КЛЮЧ"
TRYBIT_SHOP_ID = "ТВОЙ_SHOP_ID"
TRYBIT_URL = "https://api.trybit.com/v2/invoice/create"

# ================= PYDANTIC МОДЕЛИ =================
class AuthModel(BaseModel):
    username: str
    password: str

class DepositModel(BaseModel):
    amount: float

# ================= РАБОТА С БАЗОЙ ДАННЫХ POSTGRESQL =================
def get_db():
    if not DATABASE_URL:
        print("КРИТИЧЕСКАЯ ОШИБКА: Переменная DATABASE_URL не найдена!", file=sys.stderr)
        raise RuntimeError("DATABASE_URL is missing")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    if not DATABASE_URL:
        return
    print("--- ПРОВЕРКА И ИНИЦИАЛИЗАЦИЯ ТАБЛИЦ POSTGRESQL ---", file=sys.stdout)
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # 1. Участницы
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contestants (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            photo_url TEXT,
            votes_count INTEGER DEFAULT 0
        )
    """)
    
    # 2. История победительниц
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_winners (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            title_date VARCHAR(255) NOT NULL,
            photo_url TEXT
        )
    """)
    
    # 3. Фотоальбомы победительниц
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS winner_photos (
            id SERIAL PRIMARY KEY,
            winner_id INTEGER NOT NULL REFERENCES history_winners(id) ON DELETE CASCADE,
            photo_url TEXT NOT NULL
        )
    """)
    
    # 4. Пользователи
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            balance REAL DEFAULT 100.0,
            is_admin INTEGER DEFAULT 0
        )
    """)
    
    # 5. Платежи
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id VARCHAR(255) PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            amount REAL,
            status VARCHAR(50) DEFAULT 'pending'
        )
    """)
    
    # ЖЕСТКАЯ И НАДЕЖНАЯ ПРОВЕРКА АДМИНА
    cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES ('admin', 'admin', 500.0, 1)")
        print("--- СГЕНЕРИРОВАН НОВЫЙ АККАУНТ АДМИНИСТРАТОРА (admin/admin) ---", file=sys.stdout)
    
    conn.commit()
    cursor.close()
    conn.close()
    print("--- БАЗА ДАННЫХ POSTGRESQL ПОЛНОСТЬЮ ГОТОВА И СТАБИЛЬНА ---", file=sys.stdout)

# Запускаем инициализацию таблиц при старте приложения
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


# ================= АВТОРИЗАЦИЯ И ПРОФИЛЬ =================
@app.get("/api/me")
def get_me(user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        return {"username": "Гость", "balance": 0.0, "is_admin": 0}
        
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT id, username, balance, is_admin FROM users WHERE id = %s", (int(user_id),))
    user = cursor.fetchone()
    cursor.close()
    
    if not user:
        return {"username": "Гость", "balance": 0.0, "is_admin": 0}
        
    return dict(user)

@app.post("/api/register")
def api_register(data: AuthModel, db=Depends(get_db)):
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES (%s, %s, 100.0, 0)", 
                       (data.username, data.password))
        db.commit()
        cursor.close()
        return {"status": "success"}
    except psycopg2.errors.UniqueViolation:
        db.rollback()
        cursor.close()
        raise HTTPException(status_code=400, detail="Пользователь уже существует")

@app.post("/api/login")
def api_login(data: AuthModel, response: Response, db=Depends(get_db)):
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT id FROM users WHERE username = %s AND password = %s", (data.username, data.password))
    user = cursor.fetchone()
    cursor.close()
    
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


# ================= ПОПОЛНЕНИЕ БАЛАНСА ЧЕРЕЗ TRYBIT V2 =================
@app.post("/api/deposit")
def api_deposit(data: DepositModel, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Не авторизован")
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Некорректная сумма")
        
    order_id = f"ORDER_{uuid.uuid4().hex[:10].upper()}"
    
    cursor = db.cursor()
    cursor.execute("INSERT INTO payments (id, user_id, amount, status) VALUES (%s, %s, %s, 'pending')",
                   (order_id, int(user_id), data.amount))
    db.commit()
    cursor.close()
    
    headers = {
        "Authorization": f"Token {TRYBIT_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "amount": float(data.amount),
        "shop_id": TRYBIT_SHOP_ID,
        "currency": "RUB",
        "order_id": order_id,
        "description": f"Deposit for user ID {user_id}"  # Только латиница
    }
    
    try:
        response = requests.post(TRYBIT_URL, json=payload, headers=headers, timeout=15)
        print("СТАТУС ОТВЕТА TRYBIT:", response.status_code, file=sys.stdout)
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

    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT user_id, status FROM payments WHERE id = %s", (order_id,))
    payment = cursor.fetchone()

    if payment and payment["status"] == 'pending':
        user_id = payment["user_id"]
        try:
            cursor.execute("UPDATE payments SET status = 'success' WHERE id = %s", (order_id,))
            cursor.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (float(amount), user_id))
            db.commit()
            print(f"Баланс пользователя {user_id} успешно пополнен на {amount} через Trybit!", file=sys.stdout)
        except Exception as e:
            db.rollback()
            print(f"Ошибка базы данных при обработке callback: {e}", file=sys.stdout)
            cursor.close()
            raise HTTPException(status_code=500, detail="Database error")

    cursor.close()
    return "OK"


# ================= ПУБЛИЧНЫЙ API ДЛЯ ГАЛЕРЕИ И ГОЛОСОВАНИЯ =================
@app.get("/api/contestants")
def get_contestants(db=Depends(get_db)):
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT id, name, photo_url, votes_count FROM contestants ORDER BY votes_count DESC")
    rows = cursor.fetchall()
    cursor.close()
    return [dict(row) for row in rows]

@app.post("/api/vote")
def vote_for_girl(contestant_id: int, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Войдите в аккаунт, чтобы голосовать")
        
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT balance FROM users WHERE id = %s", (int(user_id),))
    user = cursor.fetchone()
    
    if not user or user["balance"] < 10:
        cursor.close()
        raise HTTPException(status_code=400, detail="Недостаточно средств. Пополните баланс (стоимость 10₽)!")
        
    cursor.execute("SELECT id FROM contestants WHERE id = %s", (contestant_id,))
    if not cursor.fetchone():
        cursor.close()
        raise HTTPException(status_code=404, detail="Участница не найдена")
    
    try:
        cursor.execute("UPDATE users SET balance = balance - 10 WHERE id = %s", (int(user_id),))
        cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = %s", (contestant_id,))
        db.commit()
    except Exception as e:
        db.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail="Ошибка при голосовании")
        
    cursor.close()
    return {"status": "success"}

@app.get("/api/history")
def get_history_winners(db=Depends(get_db)):
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT id, name, title_date, photo_url FROM history_winners ORDER BY id DESC")
    rows = cursor.fetchall()
    cursor.close()
    return [dict(row) for row in rows]

@app.get("/api/history/{winner_id}/photos")
def get_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT photo_url FROM winner_photos WHERE winner_id = %s ORDER BY id ASC", (winner_id,))
    rows = cursor.fetchall()
    cursor.close()
    return [row["photo_url"] for row in rows]


# ================= АДМИН-ПАНЕЛЬ (УПРАВЛЕНИЕ УЧАСТНИЦАМИ) =================
@app.post("/api/admin/contestants")
async def admin_add_contestant(
    name: str = Form(...),
    file: UploadFile = File(...),
    user_id: Optional[str] = Cookie(None),
    db=Depends(get_db)
):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT is_admin FROM users WHERE id = %s", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: 
        cursor.close()
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    try:
        file_ext = os.path.splitext(file.filename)[1]
        filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(PHOTOS_DIR, filename)
        
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
            
        photo_url = f"/static/photos/{filename}"
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (%s, %s, 0)", (name, photo_url))
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка добавления: {str(e)}")
    finally:
        cursor.close()

@app.delete("/api/admin/contestants/{girl_id}")
def admin_delete_contestant(girl_id: int, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT is_admin FROM users WHERE id = %s", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: 
        cursor.close()
        raise HTTPException(status_code=403)

    cursor.execute("DELETE FROM contestants WHERE id = %s", (girl_id,))
    db.commit()
    cursor.close()
    return {"status": "success"}


# ================= ЗАЛ СЛАВЫ / АРХИВ =================
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
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT is_admin FROM users WHERE id = %s", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: 
        cursor.close()
        raise HTTPException(status_code=403)

    try:
        main_ext = os.path.splitext(file.filename)[1]
        main_filename = f"winner_{uuid.uuid4()}{main_ext}"
        main_path = os.path.join(PHOTOS_DIR, main_filename)
        
        main_content = await file.read()
        with open(main_path, "wb") as f:
            f.write(main_content)
        main_photo_url = f"/static/photos/{main_filename}"
        
        cursor.execute("INSERT INTO history_winners (name, title_date, photo_url) VALUES (%s, %s, %s) RETURNING id", 
                       (name, title_date, main_photo_url))
        winner_id = cursor.fetchone()["id"]
        
        for alb_file in album_files:
            if alb_file.filename:
                alb_ext = os.path.splitext(alb_file.filename)[1]
                alb_filename = f"alb_{uuid.uuid4()}{alb_ext}"
                alb_path = os.path.join(PHOTOS_DIR, alb_filename)
                
                alb_content = await alb_file.read()
                with open(alb_path, "wb") as f:
                    f.write(alb_content)
                cursor.execute("INSERT INTO winner_photos (winner_id, photo_url) VALUES (%s, %s)", 
                               (winner_id, f"/static/photos/{alb_filename}"))
                
        db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка архива: {str(e)}")
    finally:
        cursor.close()

@app.delete("/api/admin/history/{winner_id}")
def admin_delete_history_winner(winner_id: int, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = %s", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check[0]: 
        cursor.close()
        raise HTTPException(status_code=403)

    try:
        cursor.execute("DELETE FROM winner_photos WHERE winner_id = %s", (winner_id,))
        cursor.execute("DELETE FROM history_winners WHERE id = %s", (winner_id,))
        db.commit()
    except Exception as e:
        db.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail="Ошибка удаления из архива")
        
    cursor.close()
    return {"status": "success"}


# ================= ЖЕЛЕЗОБЕТОННЫЙ ВХОД ДЛЯ АДМИНА =================
@app.get("/force-admin")
def force_admin(response: Response, db=Depends(get_db)):
    cursor = db.cursor(cursor_factory=RealDictCursor)
    
    # Сначала проверяем, есть ли админ, и если вдруг пропал — создаем принудительно
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    user = cursor.fetchone()
    
    if not user:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES ('admin', 'admin', 500.0, 1) RETURNING id")
        user = cursor.fetchone()
        db.commit()
        
    cursor.close()
    
    # Выставляем куку авторизации на полную мощность
    response.set_cookie(key="user_id", value=str(user["id"]), httponly=True, max_age=86400, path="/")
    return {"status": "Вы успешно авторизованы как ADMIN в вечной базе Postgres!"}
