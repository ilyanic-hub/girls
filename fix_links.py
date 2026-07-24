import sqlite3
import os
import dropbox
from pathlib import Path

# Проверяем, установлена ли библиотека python-dotenv
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    load_dotenv(dotenv_path=env_path)
    print("Пакет dotenv успешно загружен.")
except ImportError:
    print("ВНИМАНИЕ: Пакет python-dotenv не установлен! Выполни: pip install python-dotenv")

# Загружаем ключи
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET")
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")

# Выводим в терминал состояние ключей (скроет секреты, но покажет, есть ли они вообще)
print("----------------------------------------")
print("DROPBOX_APP_KEY читается:", "ДА (длина " + str(len(DROPBOX_APP_KEY)) + ")" if DROPBOX_APP_KEY else "НЕТ (пусто!)")
print("DROPBOX_APP_SECRET читается:", "ДА (длина " + str(len(DROPBOX_APP_SECRET)) + ")" if DROPBOX_APP_SECRET else "НЕТ (пусто!)")
print("DROPBOX_REFRESH_TOKEN читается:", "ДА (длина " + str(len(DROPBOX_REFRESH_TOKEN)) + ")" if DROPBOX_REFRESH_TOKEN else "НЕТ (пусто!)")
print("----------------------------------------")

if not DROPBOX_APP_KEY or not DROPBOX_REFRESH_TOKEN:
    print("\nКлючи не обнаружены! Проверь содержимое файла .env — там должны быть строки вроде:")
    print("DROPBOX_APP_KEY=твой_ключ")
    print("DROPBOX_APP_SECRET=твой_секрет")
    print("DROPBOX_REFRESH_TOKEN=твой_токен")
    exit(1)

# Инициализируем Dropbox
dbx = dropbox.Dropbox(
    app_key=DROPBOX_APP_KEY,
    app_secret=DROPBOX_APP_SECRET,
    oauth2_refresh_token=DROPBOX_REFRESH_TOKEN
)

db = sqlite3.connect("database.db")
cursor = db.cursor()

def fix_table(table_name, url_column="photo_url"):
    print(f"Обработка таблицы {table_name}...")
    try:
        cursor.execute(f"SELECT id, {url_column} FROM {table_name}")
    except sqlite3.OperationalError as e:
        print(f"Пропускаем таблицу {table_name}: {e}")
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

fix_table("album_photos")
fix_table("adult_model_photos")

db.close()
print("Готово! Все ссылки успешно обновлены.")
