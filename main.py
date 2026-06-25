import os
import uuid
import sqlite3
import sys
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Response, Cookie  # ДОБАВЛЕНЫ Response и Cookie
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ================= НАСТРОЙКА ПОСТОЯННОГО ХРАНИЛИЩА =================
if os.path.exists("/app") and os.access("/app", os.W_OK):
    DATA_DIR = "/app/data"
else:
    DATA_DIR = os.path.join(BASE_DIR, "data")

PHOTOS_DIR = os.path.join(DATA_DIR, "photos")

try:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PHOTOS_DIR, exist_ok=True)
except Exception:
    DATA_DIR = os.path.join(BASE_DIR, "data")
    PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PHOTOS_DIR, exist_ok=True)

DATABASE_PATH = os.path.join(DATA_DIR, "database.db")
print(f"--- БАЗА ДАННЫХ ИНИЦИАЛИЗИРОВАНА ПО ПУТИ: {DATABASE_PATH} ---", file=sys.stdout)
print(f"--- ПАПКА ДЛЯ ФОТОГРАФИЙ: {PHOTOS_DIR} ---", file=sys.stdout)
# ===================================================================

app = FastAPI(title="Photo Rating API")

# Подключение статики и загруженных фото
PHOTOS_DIR = os.path.join(BASE_DIR, "static", "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Pydantic-модели
class AuthModel(BaseModel):
    username: str
    password: str

class DepositModel(BaseModel):
    amount: float

# УДАЛИЛИ ГЛОБАЛЬНУЮ ПЕРЕМЕННУЮ CURRENT_USER_ID, ТАК КАК ТЕПЕРЬ ВСЁ В КУКАХ

def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Функция автоматического создания ВСЕХ таблиц, если их нет"""
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
    
    cursor.execute("INSERT OR IGNORE INTO users (id, username, password, balance, is_admin) VALUES (1, 'admin', 'admin', 500.0, 1)")
    
    conn.commit()
    conn.close()
    print("--- ВСЕ ТАБЛИЦЫ БАЗЫ ДАННЫХ ИНИЦИАЛИЗИРОВАНА И ПРОВЕРЕНЫ ---", file=sys.stdout)

init_db()


# ================= СТРАНИЦЫ (ФРОНТЕНД ЧЕРЕЗ FILE_RESPONSE) =================

@app.get("/", response_class=FileResponse)
async def index_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))

@app.get("/history", response_class=FileResponse)
async def history_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "history.html"))

@app.get("/admin", response_class=FileResponse)
async def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "templates", "admin.html"))


# ================= АВТОРИЗАЦИЯ И ПОПОЛНЕНИЕ БАЛАНСА =================

@app.get("/api/me")
def get_me(user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    # Если куки нет, отдаем фейковый гостевой профиль, где админка КАТЕГОРИЧЕСКИ выключена
    if not user_id:
        return {"username": "Гость", "balance": 0.0, "is_admin": 0}
        
    cursor = db.cursor()
    cursor.execute("SELECT username, balance, is_admin FROM users WHERE id = ?", (int(user_id),))
    user = cursor.fetchone()
    
    # Если кука есть, но пользователя почему-то нет в базе
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
    
    # Сохраняем сессию только в браузере залогиненного пользователя
    response.set_cookie(key="user_id", value=str(user["id"]), httponly=True, max_age=86400)
    return {"status": "success"}

@app.post("/api/logout")
def api_logout(response: Response):
    # Принудительно устанавливаем куку в пустое значение с истекшим сроком действия
    response.set_cookie(
        key="user_id", 
        value="", 
        path="/", 
        expires=0, 
        max_age=0, 
        httponly=True
    )
    return {"status": "success"}

@app.post("/api/deposit")
def api_deposit(data: DepositModel, user_id: Optional[str] = Cookie(None), db=Depends(get_db)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Не авторизован")
        
    cursor = db.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (data.amount, int(user_id)))
    db.commit()
    return {"payment_url": "/"}


# ================= ПУБЛИЧНЫЙ API ДЛЯ ГАЛЕРЕИ И ГОЛОСОВАНИЯ =================

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
    rows = cursor.fetchall()
    return [dict(row) for row in rows]

@app.get("/api/history/{winner_id}/photos")
def get_winner_photos(winner_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT photo_url FROM winner_photos WHERE winner_id = ? ORDER BY id ASC", (winner_id,))
    rows = cursor.fetchall()
    return [row["photo_url"] for row in rows]


# ================= АДМИН-ПАНЕЛЬ (УПРАВЛЕНИЕ УЧАСТНИЦАМИ) =================

@app.post("/api/admin/contestants")
async def admin_add_contestant(
    name: str = Form(...),
    file: UploadFile = File(...),
    user_id: Optional[str] = Cookie(None),
    db=Depends(get_db)
):
    # Защита эндпоинта от не-админов
    if not user_id:
        raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

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

@app.put("/api/admin/contestants/{girl_id}")
async def admin_edit_contestant(
    girl_id: int,
    name: str = Form(...),
    votes_count: int = Form(...),
    file: Optional[UploadFile] = File(None),
    user_id: Optional[str] = Cookie(None),
    db=Depends(get_db)
):
    if not user_id: raise HTTPException(status_code=401)
    cursor = db.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
    check = cursor.fetchone()
    if not check or not check["is_admin"]: raise HTTPException(status_code=403)

    try:
        if file and file.filename:
            file_ext = os.path.splitext(file.filename)[1]
            filename = f"{uuid.uuid4()}{file_ext}"
            file_path = os.path.join(PHOTOS_DIR, filename)
            
            content = await file.read()
            with open(file_path, "wb") as f:
                f.write(content)
                
            photo_url = f"/static/photos/{filename}"
            cursor.execute("UPDATE contestants SET name = ?, votes_count = ?, photo_url = ? WHERE id = ?", (name, votes_count, photo_url, girl_id))
        else:
            cursor.execute("UPDATE contestants SET name = ?, votes_count = ? WHERE id = ?", (name, votes_count, girl_id))
            
        db.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка editing: {str(e)}")

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

@app.put("/api/admin/history/{winner_id}")
async def admin_edit_history_winner(
    winner_id: int,
    name: str = Form(...),
    title_date: str = Form(...),
    file: Optional[UploadFile] = File(None),
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
        if file and file.filename:
            main_ext = os.path.splitext(file.filename)[1]
            main_filename = f"winner_{uuid.uuid4()}{main_ext}"
            main_path = os.path.join(PHOTOS_DIR, main_filename)
            
            main_content = await file.read()
            with open(main_path, "wb") as f:
                f.write(main_content)
            cursor.execute("UPDATE history_winners SET name = ?, title_date = ?, photo_url = ? WHERE id = ?", (name, title_date, f"/static/photos/{main_filename}", winner_id))
        else:
            cursor.execute("UPDATE history_winners SET name = ?, title_date = ? WHERE id = ?", (name, title_date, winner_id))

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
        raise HTTPException(status_code=500, detail=f"Ошибка изменения архива: {str(e)}")

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


# ================= ЭНДПОИНТ-ВЫРУЧАЛОЧКА ДЛЯ АВТОРИЗАЦИИ АДМИНА =================

@app.get("/force-admin")
def force_admin(response: Response, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    user = cursor.fetchone()
    if user:
        response.set_cookie(key="user_id", value=str(user["id"]), httponly=True, max_age=86400)
        return {"status": "Вы успешно авторизованы как ADMIN через Cookies!"}
    return {"status": "Админ не найден"}
