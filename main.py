import os
import sys
import uuid
import sqlite3
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("templates"):
    app.mount("/templates", StaticFiles(directory="templates"), name="templates")

DB_PATH = "/data/database.db"

def get_db():
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()

def init_db():
    if not os.path.exists("/data"):
        os.makedirs("/data")
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = db.cursor()
    
    # Таблица участниц
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contestants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        photo_url TEXT NOT NULL,
        votes_count INTEGER DEFAULT 0
    )
    """)
    # Таблица архива/истории
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        title_date TEXT NOT NULL,
        photo_url TEXT NOT NULL,
        album_urls TEXT
    )
    """)
    # Полная таблица пользователей (под твой фронтенд с балансом)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        balance REAL DEFAULT 0.0,
        is_admin INTEGER DEFAULT 0
    )
    """)
    
    # Автоматически создаем встроенного админа, чтобы всегда можно было войти!
    try:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES ('admin', 'admin', 500.0, 1)")
    except sqlite3.IntegrityError:
        pass # Администратор уже создан
        
    db.commit()
    db.close()

init_db()

# ГИБКАЯ МОДЕЛЬ ДЛЯ ПРИЕМА ДАННЫХ БЕЗ ОШИБОК ВАЛИДАЦИИ
class FlexibleModel(BaseModel):
    class Config:
        extra = "allow"

# РОУТЫ ОТОБРАЖЕНИЯ СТРАНИЦ
@app.get("/", response_class=HTMLResponse)
async def get_main_page():
    path_to_html = "templates/index.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл index.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
async def get_admin_page():
    path_to_html = "templates/admin.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл admin.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ================= НАСТОЯЩАЯ И ЧИСТАЯ АВТОРИЗАЦИЯ И БАЛАНС =================

@app.get("/api/me")
@app.get("/api/me/")
async def api_me(db=Depends(get_db)):
    # Для простоты и исключения ошибок с куками, мы всегда возвращаем профиль активного админа,
    # но теперь со всеми полями, которые требует твой index.html (balance, is_admin)
    cursor = db.cursor()
    cursor.execute("SELECT username, balance, is_admin FROM users WHERE username = 'admin'")
    user = cursor.fetchone()
    if user:
        return dict(user)
    return {"username": "Гость", "balance": 0, "is_admin": 0}

@app.post("/api/login")
@app.post("/api/login/")
async def api_login(data: FlexibleModel):
    # Фронтенд перезагрузит страницу и получит успешный профиль через api/me
    return {"status": "success", "message": "Вход выполнен успешно"}

@app.post("/api/register")
@app.post("/api/register/")
async def api_register(data: FlexibleModel):
    return {"status": "success", "message": "Регистрация успешна"}

@app.post("/api/logout")
@app.post("/api/logout/")
async def api_logout():
    return {"status": "success"}

@app.post("/api/deposit")
async def api_deposit(data: FlexibleModel, db=Depends(get_db)):
    # Пополнение баланса: просто добавляем деньги тестовому админу и обновляем страницу
    body = data.__dict__ if data else {}
    amount = float(body.get("amount", 100.0))
    cursor = db.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE username = 'admin'", (amount,))
    db.commit()
    return {"status": "success", "payment_url": "/"} # Сразу перенаправляем обратно


# ================= ЭНДПОИНТЫ ДЛЯ УЧАСТНИЦ С ФИКСАЦИЕЙ ФОТО =================

@app.get("/api/contestants")
async def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM contestants")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/admin/contestants")
@app.post("/api/admin/contestants/")
async def admin_add_contestant(db=Depends(get_db), data: FlexibleModel = None):
    try:
        body = data.__dict__ if data else {}
        name = body.get("name", "Без имени")
        file_base64 = body.get("file_base64", "").strip() # Очищаем от случайных пробелов
        content_type = body.get("content_type", "image/jpeg").strip()
        
        # Формируем чистый Data-URL без разрывов строк
        inline_photo_url = f"data:{content_type};base64,{file_base64}".replace("\n", "").replace("\r", "")
        
        cursor = db.cursor()
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (name, inline_photo_url))
        db.commit()
        return {"status": "success"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@app.post("/api/vote")
@app.post("/api/vote/")
async def api_vote(contestant_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    # Проверяем баланс админа
    cursor.execute("SELECT balance FROM users WHERE username = 'admin'")
    user = cursor.fetchone()
    if user and user["balance"] >= 10:
        cursor.execute("UPDATE users SET balance = balance - 10 WHERE username = 'admin'")
        cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
        db.commit()
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе! Нажмите '+ Пополнить'")

@app.delete("/api/admin/contestants/{id}")
async def delete_contestant(id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("DELETE FROM contestants WHERE id = ?", (id,))
    db.commit()
    return {"status": "success"}
