import sqlite3
import os
import dropbox
from pathlib import Path
from dotenv import load_dotenv

# Явно указываем путь к .env файлу прямо рядом со скриптом
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# Загружаем ключи
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET")
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")

# Проверим прямо в консоли, подгрузились ли ключи
print("APP_KEY:", "OK" if DROPBOX_APP_KEY else "ПУСТО")
print("REFRESH_TOKEN:", "OK" if DROPBOX_REFRESH_TOKEN else "ПУСТО")

dbx = dropbox.Dropbox(
    app_key=DROPBOX_APP_KEY,
    app_secret=DROPBOX_APP_SECRET,
    oauth2_refresh_token=DROPBOX_REFRESH_TOKEN
)

# Подключаемся к базе данных (проверь, точно ли база называется database.db)
db = sqlite3.connect("database.db")
cursor = db.cursor()

def fix_table(table_name, url_column="photo_url"):
    print(f"Обработка таблицы {table_name}...")
    try:
        cursor.execute(f"SELECT id, {url_column} FROM {table_name}")
    except sqlite3.OperationalError as e:
        print(f"Пропускаем таблицу {table_name}, так как её нет или она отличается: {e}")
        return

    rows = cursor.fetchall()
    updated_count = 0
    
    for row_id, old_url in rows:
        if not old_url:
            continue
        try:
            if "album_" in old_url:
                filename = old_url.split("/")[-1].split("?")[0]
                dropbox_path = f"/albums/{filename}"
                
                try:
                    shared_link = dbx.sharing_create_shared_link_with_settings(dropbox_path)
                    url = shared_link.url
                except Exception:
                    links = dbx.sharing_list_shared_links(path=dropbox_path).links
                    url = links[0].url if links else ""
                
                if url:
                    new_url = url.replace("?dl=0", "?raw=1").replace("&dl=0", "&raw=1")
                    if "?raw=1" not in new_url:
                        new_url += "&raw=1"
                    
                    cursor.execute(f"UPDATE {table_name} SET {url_column} = ? WHERE id = ?", (new_url, row_id))
                    updated_count += 1
        except Exception as e:
            print(f"Ошибка с ID {row_id}: {e}")
            
    db.commit()
    print(f"Обновлено записей в {table_name}: {updated_count}\n")

# Запускаем исправление для таблиц
fix_table("album_photos")
fix_table("adult_model_photos")

db.close()
print("Готово! Все ссылки успешно обновлены.")
