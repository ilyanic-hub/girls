import os
import sys
import io
import uuid
import base64
import traceback
import sqlite3
import json
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account
from google.auth.transport import requests  # ПРАВИЛЬНЫЙ ИМПОРТ ПРЯМО К МОДУЛЮ

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
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()

# Инициализация базы данных
def init_db():
    if not os.path.exists("/data"):
        os.makedirs("/data")
    db = sqlite3.connect(DB_PATH)
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

GOOGLE_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID", "")

# СВЕРХНАДЕЖНАЯ ФУНКЦИЯ С ИСПРАВЛЕНИЕМ ВРЕМЕНИ СЕРВЕРА
def get_drive_service():
    if not os.path.exists("google_keys.json"):
        print("!!! КРИТИЧЕСКАЯ ОШИБКА: Файл google_keys.json не найден в проекте !!!", file=sys.stderr)
        raise HTTPException(status_code=500, detail="На сервере Admin отсутствует файл google_keys.json")
        
    try:
        with open("google_keys.json", "r", encoding="utf-8") as f:
            creds_data = json.load(f)
            
        if "private_key" in creds_data:
            key = creds_data["private_key"]
            key = key.replace("\\\\n", "\n").replace("\\n", "\n")
            creds_data["private_key"] = key
            
        creds = service_account.Credentials.from_service_account_info(
            creds_data, 
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        
        # ФИКС ВРЕМЕНИ: Теперь вызываем через корректно импортированный модуль requests
        req = requests.Request()
        creds.refresh(req)
        
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"!!! ОШИБКА ИНИЦИАЛИЗАЦИИ GOOGLE DRIVE: {e} !!!", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Ошибка авторизации Google: {str(e)}")

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

# РОУТ ДЛЯ ОТКРЫТИЯ СТРАНИЦЫ АДМИНКИ
@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
async def get_admin_page():
    path_to_html = "templates/admin.html"
    if not os.path.exists(path_to_html):
        raise HTTPException(status_code=404, detail="Файл templates/admin.html не найден на сервере!")
        
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# ЭНДПОИНТЫ ДЛЯ ТЕСТОВ
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

# ЭНДПОИНТЫ ДЛЯ ДАННЫХ
@app.post("/api/admin/contestants")
@app.post("/api/admin/contestants/")
async def admin_add_contestant(data: ContestantJSONModel, db=Depends(get_db)):
    print(">>> [БЭКЕНД] Начинаем загрузку участницы...", file=sys.stdout)
    try:
        file_bytes = base64.b64decode(data.file_base64)
        file_stream = io.BytesIO(file_bytes)
        
        drive_service = get_drive_service()
        
        file_metadata = {
            "name": f"{uuid.uuid4()}_{data.filename}",
            "parents": [GOOGLE_FOLDER_ID] if GOOGLE_FOLDER_ID else []
        }
        
        media = MediaIoBaseUpload(file_stream, mimetype=data.content_type, resumable=True)
        uploaded_file = drive_service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()
        
        file_id = uploaded_file.get("id")
        
        drive_service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"}
        ).execute()
        
        photo_url = f"https://lh3.googleusercontent.com/u/0/d/{file_id}"
        
        cursor = db.cursor()
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (data.name, photo_url))
        db.commit()
        
        print(">>> [БЭКЕНД] Участница успешно сохранена!", file=sys.stdout)
        return {"status": "success"}
        
    except Exception as err:
        print("!!! [БЭКЕНД] СБОЙ ВНУТРИ АДМИНКИ !!!", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Ошибка обработки: {str(err)}")

@app.post("/api/admin/history")
@app.post("/api/admin/history/")
async def admin_add_history(data: HistoryJSONModel, db=Depends(get_db)):
    try:
        drive_service = get_drive_service()
        
        main_bytes = base64.b64decode(data.file_base64)
        main_stream = io.BytesIO(main_bytes)
        main_metadata = {
            "name": f"winner_{uuid.uuid4()}_{data.filename}",
            "parents": [GOOGLE_FOLDER_ID] if GOOGLE_FOLDER_ID else []
        }
        main_media = MediaIoBaseUpload(main_stream, mimetype=data.content_type, resumable=True)
        main_file = drive_service.files().create(body=main_metadata, media_body=main_media, fields="id").execute()
        main_id = main_file.get("id")
        drive_service.permissions().create(fileId=main_id, body={"type": "anyone", "role": "reader"}).execute()
        main_url = f"https://lh3.googleusercontent.com/u/0/d/{main_id}"

        album_urls = []
        if data.album_files:
            for f_data in data.album_files:
                f_bytes = base64.b64decode(f_data["file_base64"])
                f_stream = io.BytesIO(f_bytes)
                f_metadata = {
                    "name": f"album_{uuid.uuid4()}_{f_data['filename']}",
                    "parents": [GOOGLE_FOLDER_ID] if GOOGLE_FOLDER_ID else []
                }
                f_media = MediaIoBaseUpload(f_stream, mimetype=f_data["content_type"], resumable=True)
                f_file = drive_service.files().create(body=f_metadata, media_body=f_media, fields="id").execute()
                f_id = f_file.get("id")
                drive_service.permissions().create(fileId=f_id, body={"type": "anyone", "role": "reader"}).execute()
                album_urls.append(f"https://lh3.googleusercontent.com/u/0/d/{f_id}")

        album_str = ",".join(album_urls) if album_urls else ""
        
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO history (name, title_date, photo_url, album_urls) VALUES (?, ?, ?, ?)",
            (data.name, data.title_date, main_url, album_str)
        )
        db.commit()
        return {"status": "success"}
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Ошибка архивации: {str(err)}")

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
