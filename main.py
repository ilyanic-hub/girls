import os
import sys
import sqlite3
import dropbox
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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
DROPBOX_TOKEN = "sl.u.AGls_K43zjvs_WQwm1IQp2PPQ56UDj1kC-qfHwNnMXehlfyhPOHkjwo77BBYvOKk4V8iLxQCBNr7Q5Rk-oXcRn4sMrd4QBqxEcuW8vJqNoxCnWig7xVp0gDmPzxXqEzO-3NqlE9uRz1Z6xtVtY5UdyTd0avhTDolateqENDT7OhlzFFTfF8BJsWwJPGH6_B5QnRBQ_H8rs3W8du4JB0nTWdV6HpPzQjye4j03CKvxXRlysl9ZynUV8m0hYrZJ0iPEHEGCsYl2nGSUImP0Iu9IcqHZzpUf3OvjRBOx4eF5061LhyiOeNcuY0rzti2YUoa_B5mc9-q_EFqAohCelBuFRUkAnS4u0zJ28cwjpG0DRNvLbl-QZqCE64X3TK8OXaTXkGa12kxi1RkxT84a3tjHafR9-TT_uuw0s87MDdIRyn1kKrN4wXfM-cJt9P63VPquWAAaAWKexQtUnOoQXol_q_GSJMVPAcNNifT_sdcZDSazv1EnCCVJN5gJ_g-vr_YLnizhjSZTPtI2I4w5QC-gNHIuFscg6fGkaBm6bqPLqIlZVRGtVXdO4FHTyLlZl6ojPcNm0pMPy9o5wUa2ZQVyHn87XF02SdmEm2wVZObck1OkqFRBmXiSqN1G2qh6Hv30-6ua50ZXc-h0ffcnYM45H24QcHH5tO9BCHOt9sM_Mx1zsRc7dlVhxordEOdHqOFD1NFc4qbt6XrqWI5rXZApVJBZVuONpRPPZcedmDEPsXaVDCp8fHRCCgKKdjSneiwDFg__cCYdbgd2tPe9sWSLQmqEYJvTlQW-EJ1MIi4gw2U1hkFAjaDdqNKrqUseeUnEqUE_w6AIc_B72_ypbj6SzoP-NaykMDME0YpSC_EtozfQhdSMO4QzmyxF5zI6cAJKgnPedtBStlIgeL5nc1sRu7d_IgXHYZIOOwASQ0ISu2AlcpZKBaadlAy3oec8je-xX3riaySJkScmlKMnae10ZbMmbsNkIu2KYkhQ2l7rIpftAhxEQTcdr7x72mAs1YnkA8rtuRzP1G5h5wOWJAQxAV50nnbF61DfUVX77h37cguo6eBGUpPWg5uVFkZOB6kNx62jd1iIZp6-_BAmTZP11V8-R9TZkaLLwF4P2BTA3Dik4RG6AjF7UMNgLHSkafphbV8iAQRv9j2J_0oS_OYZ7S88garFQEbZUzIF7z2-LfrwZD03skJe0Bply8c98HaS6MUMxCN1vAGBOf8zFOrzm_Xm8oXoyp8zkg7PSimcC7uYXJQNOjyASYiD55cyxMx5T44P39PvSZFlsSmy7U8IJwnHAoJovz7ipOewHjBx3BkwlMXfWFbQvK9dMbmfk1LWcdo1OZ03jlfCUB1xpcXQie2noDqBI8OdsKoxtdV_teYYr3IAoo30tyUIqKUGVun9g1kYMDiz5qONFYEAauG5js-ZLzJNUeCX6S7lKYzo5bCwiGyEL3IgfgZTeDKYPh-VDjAQ07JBS0iYBZgip8mVfhdXTbXnE65QK-gpemICzXKPA" 
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
        print("Файл базы данных не найден в Dropbox. Создаем новую локальную базу.")
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
        print("База данных успешно сохранена в облаке Dropbox!")
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
    try:
        cursor.execute("INSERT INTO users (username, password, balance, is_admin) VALUES ('admin', 'admin', 500.0, 1)")
    except sqlite3.IntegrityError:
        pass
    db.commit()
    db.close()
    upload_db_to_dropbox()

init_db()

# ================= СТРОГИЕ МОДЕЛИ ДАННЫХ ДЛЯ ЖЕЛЕЗНОЙ ПРОВЕРКИ =================

class ContestantSchema(BaseModel):
    name: str
    file_base64: str
    filename: Optional[str] = None
    content_type: Optional[str] = None

class AlbumFileSchema(BaseModel):
    file_base64: str
    filename: Optional[str] = None
    content_type: Optional[str] = None

class HistorySchema(BaseModel):
    name: str
    title_date: str
    file_base64: str
    filename: Optional[str] = None
    content_type: Optional[str] = None
    album_files: Optional[List[AlbumFileSchema]] = None


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
async def get_admin_page():
    path_to_html = "templates/admin.html"
    if not os.path.exists(path_to_html):
        return HTMLResponse(content="<h1>Файл admin.html не найден!</h1>", status_code=404)
    with open(path_to_html, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/me")
async def api_me(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT username, balance, is_admin FROM users WHERE username = 'admin'")
    user = cursor.fetchone()
    if user:
        return dict(user)
    return {"username": "Гость", "balance": 0, "is_admin": 0}

@app.get("/api/contestants")
async def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM contestants")
    return [dict(row) for row in cursor.fetchall()]

# ИСПРАВЛЕНО: Теперь роут использует ContestantSchema и точно знает, где искать картинку
@app.post("/api/admin/contestants")
@app.post("/api/admin/contestants/")
async def admin_add_contestant(data: ContestantSchema, db=Depends(get_db)):
    try:
        name = data.name
        raw_photo = data.file_base64
        
        if not raw_photo or len(raw_photo.strip()) < 10:
            raise HTTPException(status_code=400, detail="Файл картинки пустой или слишком короткий")
            
        inline_photo_url = raw_photo.replace("\n", "").replace("\r", "").strip()
        
        cursor = db.cursor()
        cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (name, inline_photo_url))
        db.commit()
        
        upload_db_to_dropbox()
        return {"status": "success"}
    except HTTPException as he:
        raise he
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@app.post("/api/vote")
async def api_vote(contestant_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT balance FROM users WHERE username = 'admin'")
    user = cursor.fetchone()
    if user and user["balance"] >= 10:
        cursor.execute("UPDATE users SET balance = balance - 10 WHERE username = 'admin'")
        cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (contestant_id,))
        db.commit()
        upload_db_to_dropbox()
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Недостаточно средств")

@app.delete("/api/admin/contestants/{id}")
async def delete_contestant(id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("DELETE FROM contestants WHERE id = ?", (id,))
    db.commit()
    upload_db_to_dropbox()
    return {"status": "success"}
