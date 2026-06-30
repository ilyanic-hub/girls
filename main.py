import os
import sys
import io
import uuid
import base64
import traceback
import sqlite3
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account

app = FastAPI()

# Разрешаем CORS на всякий случай
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключение статических файлов и шаблонов (убедись, что папки существуют)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

DB_PATH = "/data/database.db"

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()

# Инициализация базы данных при старте
def init_db():
    if not os.path.exists("/data"):
        os.makedirs("/data")
    db = sqlite3.connect(DB_PATH)
    cursor = db.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT,
        is_admin INTEGER DEFAULT 0
    )
    """)
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

# Конфигурация Google Drive
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS", "{}")
GOOGLE_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID", "")

def get_drive_service():
    try:
        import json
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"!!! ОШИБКА ИНИЦИАЛИЗАЦИИ GOOGLE DRIVE: {e} !!!", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Ошибка конфигурации Google Drive")

# --- PYDANTIC МОДЕЛИ ДЛЯ ДАННЫХ (ВМЕСТО ФОРМ) ---
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
    album_files: Optional[List[dict]] = [] # Список дополнительных файлов [{base64, filename, content_type}]

# --- ЭНДПОИНТЫ ДЛЯ ТЕСТОВ И СТРАНИЦ ---
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

# --- ХЕНДЛЕРЫ ДОБАВЛЕНИЯ ЧЕРЕЗ JSON (BASE64) ---

@app.post("/api/admin/contestants")
async def admin_add_contestant(
    data: ContestantJSONModel,
    user_id: Optional[str] = Cookie(None),
    db=Depends(get_db)
):
    print(">>> [БЭКЕНД] Получен запрос на добавление участницы через Base64", file=sys.stdout)
    
    # Временная заглушка авторизации, если куки ещё не настроены, чтобы код работал
    # Если проверка нужна строгая, раскомментируй код ниже:
    # if not user_id: raise HTTPException(status_code=401, detail="Не авторизован")

    try:
        # Декодируем текстовую строку в байты файла
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
        
        # Делаем файл публичным по ссылке
        drive_service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"}
        ).execute()
        
        # Прямая ссылка для отображения в браузере
        photo_url = f"https://lh3.googleusercontent.com/u/0/d/{file_id}"
        
        cursor = db.cursor()
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (data.name, photo_url))
        db.commit()
        
        print(">>> [БЭКЕНД] Участница успешно сохранена!", file=sys.stdout)
        return {"status": "success"}
        
    except Exception as err:
        print("!!! [БЭКЕНД] КРИТИЧЕСКИЙ СБОЙ ДОБАВЛЕНИЯ УЧАСТНИЦЫ !!!", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Ошибка сервера: {str(err)}")


@app.post("/api/admin/history")
async def admin_add_history(
    data: HistoryJSONModel,
    user_id: Optional[str] = Cookie(None),
    db=Depends(get_db)
):
    print(">>> [БЭКЕНД] Получен запрос на добавление в Архив Королев", file=sys.stdout)
    try:
        drive_service = get_drive_service()
        
        # Загружаем главное фото
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

        # Загружаем файлы альбома (если они есть)
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
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Ошибка создания записи архива: {str(err)}")


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
