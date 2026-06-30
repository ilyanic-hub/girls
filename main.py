import os
import sys
import uuid
import sqlite3
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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
    # Таблица пользователей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )
    """)
    db.commit()
    db.close()

init_db()

# PYDANTIC МОДЕЛИ
class ContestantJSONModel(BaseModel):
    name: str
    file_base64: str
    filename: str
    content_type: str

class HistoryJSONModel(BaseModel):
    name: str
    title_date: str
    file_base64: str
    filename: str
    content_type: str
    album_files: Optional[List[dict]] = []

class AuthModel(BaseModel):
    username: str
    password: str

# РОУТЫ ДЛЯ ОТОБРАЖЕНИЯ СТРАНИЦ

@app.get("/", response_class=HTMLResponse)
async def get_main_page():
    path_to_html = "templates/index.html"
    if not os.path.exists(path_to_html):
        raise HTTPException(status_code=404, detail="Файл templates/index.html не найден!")
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
async def get_admin_page():
    path_to_html = "templates/admin.html"
    if not os.path.exists(path_to_html):
        raise HTTPException(status_code=404, detail="Файл templates/admin.html не найден!")
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# API ЭНДПОИНТЫ ДЛЯ АВТОРИЗАЦИИ (БЭКЕНД)
# Проверь, совпадают ли эти адреса с тем, куда твоя форма отправляет данные (fetch или axios)

@app.post("/api/auth/register")
@app.post("/api/auth/register/")
async def api_register(data: AuthModel, db=Depends(get_db)):
    try:
        cursor = db.cursor()
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (data.username, data.password))
        db.commit()
        return {"status": "success", "message": "Пользователь успешно зарегистрирован"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Пользователь с таким логином уже существует")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/login")
@app.post("/api/auth/login/")
async def api_login(data: AuthModel, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (data.username, data.password))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    return {"status": "success", "message": "Успешный вход"}


# ЭНДПОИНТЫ ДЛЯ УЧАСТНИЦ И ИСТОРИИ

@app.get("/force-admin")
async def force_admin():
    return {"status": "Успешный вход под ADMIN через локальный SQLite!"}

@app.get("/api/contestants")
async def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM contestants")
    return [dict(row) for row in cursor.fetchall()]

@app.get("/api/history")
async def get_history(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM history")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/admin/contestants")
@app.post("/api/admin/contestants/")
async def admin_add_contestant(data: ContestantJSONModel, db=Depends(get_db)):
    try:
        inline_photo_url = f"data:{data.content_type};base64,{data.file_base64}"
        cursor = db.cursor()
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (data.name, inline_photo_url))
        db.commit()
        return {"status": "success"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Ошибка локального сохранения: {str(err)}")

@app.post("/api/admin/history")
@app.post("/api/admin/history/")
async def admin_add_history(data: HistoryJSONModel, db=Depends(get_db)):
    try:
        inline_main_url = f"data:{data.content_type};base64,{data.file_base64}"
        album_urls = []
        if data.album_files:
            for f_data in data.album_files:
                album_urls.append(f"data:{f_data['content_type']};base64 School,{f_data['file_base64']}")
        album_str = ",".join(album_urls) if album_urls else ""
        
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO history (name, title_date, photo_url, album_urls) VALUES (?, ?, ?, ?)",
            (data.name, data.title_date, inline_main_url, album_str)
        )
        db.commit()
        return {"status": "success"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Ошибка локальной архивации: {str(err)}")

@app.delete("/api/admin/contestants/{id}")
async def delete_contestant(id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("DELETE FROM contestants WHERE id = ?", (id,))
    db.commit()
    return {"status": "success"}

@app.delete("/api/admin/history/{id}")
async def delete_history(id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("DELETE FROM history WHERE id = ?", (id,))
    db.commit()
    return {"status": "success"}
