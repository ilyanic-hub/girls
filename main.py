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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contestants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        photo_url TEXT NOT NULL,
        votes_count INTEGER DEFAULT 0
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        title_date TEXT NOT NULL,
        photo_url TEXT NOT NULL,
        album_urls TEXT
    )
    """)
    db.commit()
    db.close()

init_db()

# Универсальная модель, которая примет любые поля от фронтенда, чтобы ничего не падало
class FlexibleModel(BaseModel):
    class Config:
        extra = "allow"

# РОУТЫ ДЛЯ СТРАНИЦ
@app.get("/", response_class=HTMLResponse)
async def get_main_page():
    path_to_html = "templates/index.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл index.html не найден в templates!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
async def get_admin_page():
    path_to_html = "templates/admin.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл admin.html не найден в templates!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ================= СУПЕР-ЗАГЛУШКИ ДЛЯ АВТОРИЗАЦИИ =================
# Перехватываем абсолютно любые запросы на вход/регистрацию и всегда говорим "ДА"

@app.api_route("/api/auth/login", methods=["GET", "POST", "PUT"])
@app.api_route("/api/auth/login/", methods=["GET", "POST", "PUT"])
@app.api_route("/auth/login", methods=["GET", "POST", "PUT"])
@app.api_route("/login", methods=["POST"])
async def fake_login(payload: Optional[dict] = None):
    return JSONResponse(content={"status": "success", "message": "Успешный вход", "token": "fake-super-token", "user": {"username": "admin", "role": "admin"}})

@app.api_route("/api/auth/register", methods=["GET", "POST", "PUT"])
@app.api_route("/api/auth/register/", methods=["GET", "POST", "PUT"])
@app.api_route("/auth/register", methods=["GET", "POST", "PUT"])
@app.api_route("/register", methods=["POST"])
async def fake_register(payload: Optional[dict] = None):
    return JSONResponse(content={"status": "success", "message": "Успешная регистрация", "token": "fake-super-token"})

@app.get("/api/auth/me")
@app.get("/api/user")
async def fake_me():
    return {"username": "admin", "role": "admin", "status": "success"}


# ЭНДПОИНТЫ ДЛЯ ДАННЫХ
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
async def admin_add_contestant(db=Depends(get_db), data: FlexibleModel = None):
    try:
        # Извлекаем данные динамически
        body = data.__dict__
        name = body.get("name", "Без имени")
        file_base64 = body.get("file_base64", "")
        content_type = body.get("content_type", "image/jpeg")
        
        inline_photo_url = f"data:{content_type};base64,{file_base64}"
        cursor = db.cursor()
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (name, inline_photo_url))
        db.commit()
        return {"status": "success"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@app.post("/api/admin/history")
@app.post("/api/admin/history/")
async def admin_add_history(db=Depends(get_db), data: FlexibleModel = None):
    try:
        body = data.__dict__
        name = body.get("name", "Без имени")
        title_date = body.get("title_date", "")
        file_base64 = body.get("file_base64", "")
        content_type = body.get("content_type", "image/jpeg")
        album_files = body.get("album_files", [])

        inline_main_url = f"data:{content_type};base64,{file_base64}"
        album_urls = []
        if album_files:
            for f_data in album_files:
                album_urls.append(f"data:{f_data.get('content_type', 'image/jpeg')};base64,{f_data.get('file_base64', '')}")
        album_str = ",".join(album_urls) if album_urls else ""
        
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO history (name, title_date, photo_url, album_urls) VALUES (?, ?, ?, ?)",
            (name, title_date, inline_main_url, album_str)
        )
        db.commit()
        return {"status": "success"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

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
