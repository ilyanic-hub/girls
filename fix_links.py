import sqlite3
import os
import dropbox
from dotenv import load_dotenv

load_dotenv()

# Загружаем ключи из переменных окружения (в .env файле или на сервере)
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET")
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")

# Инициализируем Dropbox с новыми ключами
dbx = dropbox.Dropbox(
    app_key=os.getenv("DROPBOX_APP_KEY"),
    app_secret=os.getenv("DROPBOX_APP_SECRET"),
    oauth2_refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN")
)

# Подключаемся к базе данных
db = sqlite3.connect("database.db") # Замени на свое имя базы, если нужно
cursor = db.cursor()

def fix_table(table_name, url_column="photo_url"):
    print(f"Обработка таблицы {table_name}...")
    cursor.execute(f"SELECT id, {url_column} FROM {table_name}")
    rows = cursor.fetchall()
    
    updated_count = 0
    for row_id, old_url in rows:
        try:
            # Пытаемся вытащить имя файла из старой ссылки или ищем в Dropbox
            # Простой вариант: если в ссылке есть имя файла, ищем его в папке /albums/
            if "album_" in old_url:
                # Достаем имя файла из старого URL
                filename = old_url.split("/")[-1].split("?")[0]
                dropbox_path = f"/albums/{filename}"
                
                # Создаем новую публичную ссылку
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
    print(f"Обновлено записей в {table_name}: {updated_count}")

# Исправляем таблицы, где хранятся ссылки на Dropbox
fix_table("album_photos")
fix_table("adult_model_photos") # если там тоже ссылки на Dropbox

db.close()
print("Готово! Картинки снова должны работать.")
