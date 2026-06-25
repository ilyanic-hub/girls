import os
import uuid
import sqlite3
import sys  # Добавили для отладки критических ошибок
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------- НАСТРОЙКА ПОСТОЯННОГО ХРАНИЛИЩА (БЕЗОПАСНАЯ) -----------------
# Папка /tmp всегда доступна для чтения и записи в любых Linux системах,
# но мы используем её только как запасной вариант. Основной — /app/data
DATA_DIR = "/app/data"

try:
    # Пробуем создать папку на Volume. Если прав не хватает, переключаемся на локальную папку
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception as e:
    print(f"--- ПРЕДУПРЕЖДЕНИЕ: Не удалось использовать /app/data ({e}). Переключаюсь на локальную папку. ---", file=sys.stderr)
    DATA_DIR = os.path.join(BASE_DIR, "data")
    os.makedirs(DATA_DIR, exist_ok=True)

# Папка для фотографий
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)

# Путь к файлу базы данных
DATABASE_PATH = os.path.join(DATA_DIR, "database.db")
print(f"--- БАЗА ДАННЫХ ИНИЦИАЛИЗИРОВАНА ПО ПУТИ: {DATABASE_PATH} ---", file=sys.stdout)
# ----------------------------------------------------------------------------------

app = FastAPI(title="Photo Rating API")

# Монтируем статику
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.mount("/static/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS contestants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            photo_url TEXT,
            votes_count INTEGER DEFAULT 0
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_winners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            title_date TEXT NOT NULL,
            photo_url TEXT
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS winner_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            winner_id INTEGER,
            photo_url TEXT,
            FOREIGN KEY(winner_id) REFERENCES history_winners(id) ON DELETE CASCADE
        )""")
        
        conn.commit()
        conn.close()
        print("--- ТАБЛИЦЫ БАЗЫ ДАННЫХ УСПЕШНО ПРОВЕРЕНЫ/СОЗДАНЫ ---", file=sys.stdout)
    except Exception as e:
        print(f"--- КРИТИЧЕСКАЯ ОШИБКА ИНИЦИАЛИЗАЦИИ БАЗЫ: {e} ---", file=sys.stderr)


init_db()

# Имитация авторизации (замени на свою реальную проверку куки/сессий, если она есть)
async def get_current_user():
    return {"username": "admin", "is_admin": True}


# ================= СТРАНИЦЫ (ФРОНТЕНД) =================

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    return templates.TemplateResponse("history.html", {"request": request})

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


# ================= ПУБЛИЧНЫЙ API =================

@app.get("/api/contestants")
def get_contestants(db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name, photo_url, votes_count FROM contestants ORDER BY votes_count DESC")
    rows = cursor.fetchall()
    return [dict(row) for row in rows]

@app.post("/api/vote/{girl_id}")
def vote_for_girl(girl_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM contestants WHERE id = ?", (girl_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Участница не найдена")
    
    cursor.execute("UPDATE contestants SET votes_count = votes_count + 1 WHERE id = ?", (girl_id,))
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


# ================= АДМИН-ПАНЕЛЬ (УПРАВЛЕНИЕ) =================

@app.post("/api/admin/contestants")
async def admin_add_contestant(
    name: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(PHOTOS_DIR, filename)
    
    with open(file_path, "wb") as f:
        f.write(await file.read())
        
    photo_url = f"/static/photos/{filename}"
    
    cursor = db.cursor()
    cursor.execute("INSERT INTO contestants (name, photo_url, votes_count) VALUES (?, ?, 0)", (name, photo_url))
    db.commit()
    return {"status": "success"}

@app.post("/api/admin/contestants/{girl_id}/edit")
async def admin_edit_contestant(
    girl_id: int,
    name: str = Form(...),
    votes_count: int = Form(...),
    file: Optional[UploadFile] = File(None),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor = db.cursor()
    
    if file and file.filename:
        file_ext = os.path.splitext(file.filename)[1]
        filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(PHOTOS_DIR, filename)
        
        with open(file_path, "wb") as f:
            f.write(await file.read())
            
        photo_url = f"/static/photos/{filename}"
        cursor.execute("UPDATE contestants SET name = ?, votes_count = ?, photo_url = ? WHERE id = ?", (name, votes_count, photo_url, girl_id))
    else:
        cursor.execute("UPDATE contestants SET name = ?, votes_count = ? WHERE id = ?", (name, votes_count, girl_id))
        
    db.commit()
    return {"status": "success"}

@app.delete("/api/admin/contestants/{girl_id}")
def admin_delete_contestant(girl_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor = db.cursor()
    cursor.execute("DELETE FROM contestants WHERE id = ?", (girl_id,))
    db.commit()
    return {"status": "success"}


# ================= АДМИН-ПАНЕЛЬ (ЗАЛ СЛАВЫ / АРХИВ) =================

@app.post("/api/admin/history")
async def admin_add_history_winner(
    name: str = Form(...),
    title_date: str = Form(...),
    file: UploadFile = File(...),
    album_files: List[UploadFile] = File([]),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor = db.cursor()
    
    # 1. Сохраняем главное фото королевы
    main_ext = os.path.splitext(file.filename)[1]
    main_filename = f"winner_{uuid.uuid4()}{main_ext}"
    main_path = os.path.join(PHOTOS_DIR, main_filename)
    
    with open(main_path, "wb") as f:
        f.write(await file.read())
        
    main_photo_url = f"/static/photos/{main_filename}"
    
    cursor.execute("INSERT INTO history_winners (name, title_date, photo_url) VALUES (?, ?, ?)", (name, title_date, main_photo_url))
    winner_id = cursor.lastrowid
    
    # 2. Сохраняем фотографии для альбома фотосессии
    for alb_file in album_files:
        if alb_file.filename:
            alb_ext = os.path.splitext(alb_file.filename)[1]
            alb_filename = f"alb_{uuid.uuid4()}{alb_ext}"
            alb_path = os.path.join(PHOTOS_DIR, alb_filename)
            
            with open(alb_path, "wb") as f:
                f.write(await alb_file.read())
                
            cursor.execute("INSERT INTO winner_photos (winner_id, photo_url) VALUES (?, ?)", (winner_id, f"/static/photos/{alb_filename}"))
            
    db.commit()
    return {"status": "success"}

@app.post("/api/admin/history/{winner_id}/edit")
async def admin_edit_history_winner(
    winner_id: int,
    name: str = Form(...),
    title_date: str = Form(...),
    file: Optional[UploadFile] = File(None),
    album_files: List[UploadFile] = File([]),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor = db.cursor()

    if file and file.filename:
        main_ext = os.path.splitext(file.filename)[1]
        main_filename = f"winner_{uuid.uuid4()}{main_ext}"
        main_path = os.path.join(PHOTOS_DIR, main_filename)
        
        with open(main_path, "wb") as f:
            f.write(await file.read())
            
        cursor.execute("UPDATE history_winners SET name = ?, title_date = ?, photo_url = ? WHERE id = ?", (name, title_date, f"/static/photos/{main_filename}", winner_id))
    else:
        cursor.execute("UPDATE history_winners SET name = ?, title_date = ? WHERE id = ?", (name, title_date, winner_id))

    for alb_file in album_files:
        if alb_file.filename:
            alb_ext = os.path.splitext(alb_file.filename)[1]
            alb_filename = f"alb_{uuid.uuid4()}{alb_ext}"
            alb_path = os.path.join(PHOTOS_DIR, alb_filename)
            
            with open(alb_path, "wb") as f:
                f.write(await alb_file.read())
                
            cursor.execute("INSERT INTO winner_photos (winner_id, photo_url) VALUES (?, ?)", (winner_id, f"/static/photos/{alb_filename}"))

    db.commit()
    return {"status": "success"}

@app.delete("/api/admin/history/{winner_id}")
def admin_delete_history_winner(winner_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    if not user["is_admin"]: raise HTTPException(status_code=403, detail="Доступ запрещен")
    cursor = db.cursor()
    cursor.execute("DELETE FROM history_winners WHERE id = ?", (winner_id,))
    cursor.execute("DELETE FROM winner_photos WHERE winner_id = ?", (winner_id,))
    db.commit()
    return {"status": "success"}
