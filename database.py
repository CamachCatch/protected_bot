import sqlite3
import json
import os
from contextlib import contextmanager

# Путь к базе данных (можно переопределить через переменную окружения)
DB_FILE = os.getenv("DB_PATH", "antisocial.db")

@contextmanager
def get_db():
    """Контекстный менеджер для работы с БД"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_database():
    """Инициализация базы данных"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Таблица доступов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS access (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                level TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        
        # Таблица вайтлиста
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS whitelist (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        
        # Таблица черного списка
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

        # Снимки ролей пользователей до попадания в BlackList
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blacklist_role_snapshots (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role_ids TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        
        # Таблица защищенных
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS protected (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        
        # Таблица временных банов (pusy)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS temp_bans (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                expire_date TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        
        # Таблица жестких банов (apusy)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hard_bans (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                expire_date TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        
        # Таблица каналов логов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS log_channels (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL
            )
        """)

        # История логов для веб-панели
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS panel_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                category TEXT NOT NULL DEFAULT 'system',
                source TEXT NOT NULL DEFAULT 'bot'
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_panel_logs_guild_created
            ON panel_logs (guild_id, created_at DESC)
        """)
        
        # Таблица настроек автороли
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auto_roles (
                guild_id TEXT PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                role_id TEXT
            )
        """)
        
        conn.commit()
        print("✅ База данных инициализирована")

# ==========================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ДОСТУПАМИ
# ==========================================

def get_access(guild_id):
    """Получить все доступы для сервера"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, level FROM access WHERE guild_id = ?", (str(guild_id),))
        return {row['user_id']: row['level'] for row in cursor.fetchall()}

def add_access(guild_id, user_id, level):
    """Добавить доступ"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO access (guild_id, user_id, level)
            VALUES (?, ?, ?)
        """, (str(guild_id), str(user_id), level))

def remove_access(guild_id, user_id):
    """Удалить доступ"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM access WHERE guild_id = ? AND user_id = ?", 
                      (str(guild_id), str(user_id)))

# ==========================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ВАЙТЛИСТОМ
# ==========================================

def get_whitelist(guild_id):
    """Получить вайтлист для сервера"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM whitelist WHERE guild_id = ?", (str(guild_id),))
        return [int(row['user_id']) for row in cursor.fetchall()]

def add_to_whitelist(guild_id, user_id):
    """Добавить в вайтлист"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO whitelist (guild_id, user_id)
            VALUES (?, ?)
        """, (str(guild_id), str(user_id)))

def remove_from_whitelist(guild_id, user_id):
    """Удалить из вайтлиста"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whitelist WHERE guild_id = ? AND user_id = ?",
                      (str(guild_id), str(user_id)))

# ==========================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ЧЕРНЫМ СПИСКОМ
# ==========================================

def get_blacklist(guild_id):
    """Получить черный список для сервера"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM blacklist WHERE guild_id = ?", (str(guild_id),))
        return [int(row['user_id']) for row in cursor.fetchall()]

def add_to_blacklist(guild_id, user_id):
    """Добавить в черный список"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO blacklist (guild_id, user_id)
            VALUES (?, ?)
        """, (str(guild_id), str(user_id)))

def remove_from_blacklist(guild_id, user_id):
    """Удалить из черного списка"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM blacklist WHERE guild_id = ? AND user_id = ?",
                      (str(guild_id), str(user_id)))

def get_blacklist_role_snapshot(guild_id, user_id):
    """Получить снимок ролей пользователя до BlackList"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role_ids FROM blacklist_role_snapshots
            WHERE guild_id = ? AND user_id = ?
        """, (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        if not row:
            return []
        try:
            return [int(role_id) for role_id in json.loads(row['role_ids'])]
        except Exception:
            return []

def has_blacklist_role_snapshot(guild_id, user_id):
    """Проверить наличие снимка ролей пользователя"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM blacklist_role_snapshots
            WHERE guild_id = ? AND user_id = ?
        """, (str(guild_id), str(user_id)))
        return cursor.fetchone() is not None

def set_blacklist_role_snapshot(guild_id, user_id, role_ids):
    """Сохранить снимок ролей пользователя до BlackList"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO blacklist_role_snapshots (guild_id, user_id, role_ids)
            VALUES (?, ?, ?)
        """, (str(guild_id), str(user_id), json.dumps([str(role_id) for role_id in role_ids])))

def remove_blacklist_role_snapshot(guild_id, user_id):
    """Удалить снимок ролей пользователя"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM blacklist_role_snapshots
            WHERE guild_id = ? AND user_id = ?
        """, (str(guild_id), str(user_id)))

# ==========================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ЗАЩИЩЕННЫМИ
# ==========================================

def get_protected(guild_id):
    """Получить защищенных для сервера"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM protected WHERE guild_id = ?", (str(guild_id),))
        return [int(row['user_id']) for row in cursor.fetchall()]

def add_to_protected(guild_id, user_id):
    """Добавить в защищенные"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO protected (guild_id, user_id)
            VALUES (?, ?)
        """, (str(guild_id), str(user_id)))

def remove_from_protected(guild_id, user_id):
    """Удалить из защищенных"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM protected WHERE guild_id = ? AND user_id = ?",
                      (str(guild_id), str(user_id)))

# ==========================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С БАНАМИ
# ==========================================

def get_temp_bans(guild_id):
    """Получить временные баны для сервера"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, expire_date FROM temp_bans WHERE guild_id = ?", 
                      (str(guild_id),))
        return {row['user_id']: row['expire_date'] for row in cursor.fetchall()}

def add_temp_ban(guild_id, user_id, expire_date):
    """Добавить временный бан"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO temp_bans (guild_id, user_id, expire_date)
            VALUES (?, ?, ?)
        """, (str(guild_id), str(user_id), expire_date))

def remove_temp_ban(guild_id, user_id):
    """Удалить временный бан"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM temp_bans WHERE guild_id = ? AND user_id = ?",
                      (str(guild_id), str(user_id)))

def get_hard_bans(guild_id):
    """Получить жесткие баны для сервера"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, expire_date FROM hard_bans WHERE guild_id = ?",
                      (str(guild_id),))
        return {row['user_id']: row['expire_date'] for row in cursor.fetchall()}

def add_hard_ban(guild_id, user_id, expire_date):
    """Добавить жесткий бан"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO hard_bans (guild_id, user_id, expire_date)
            VALUES (?, ?, ?)
        """, (str(guild_id), str(user_id), expire_date))

def remove_hard_ban(guild_id, user_id):
    """Удалить жесткий бан"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM hard_bans WHERE guild_id = ? AND user_id = ?",
                      (str(guild_id), str(user_id)))

# ==========================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ЛОГАМИ
# ==========================================

def get_log_channel(guild_id):
    """Получить канал логов для сервера"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT channel_id FROM log_channels WHERE guild_id = ?",
                      (str(guild_id),))
        row = cursor.fetchone()
        return int(row['channel_id']) if row else None

def set_log_channel(guild_id, channel_id):
    """Установить канал логов"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO log_channels (guild_id, channel_id)
            VALUES (?, ?)
        """, (str(guild_id), str(channel_id)))

def add_panel_log(guild_id, title, description, created_at, level="info", category="system", source="bot"):
    """Сохранить запись лога для веб-панели"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO panel_logs (guild_id, created_at, title, description, level, category, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            str(guild_id),
            created_at,
            title,
            description,
            level,
            category,
            source
        ))

def _build_panel_logs_query(guild_id, search=None, category=None, level=None, source=None, date_from=None, date_to=None):
    query = ["WHERE guild_id = ?"]
    params = [str(guild_id)]

    if search:
        like_value = f"%{search.strip()}%"
        query.append("AND (title LIKE ? OR description LIKE ?)")
        params.extend([like_value, like_value])

    if category:
        query.append("AND category = ?")
        params.append(category)

    if level:
        query.append("AND level = ?")
        params.append(level)

    if source:
        query.append("AND source = ?")
        params.append(source)

    if date_from:
        query.append("AND created_at >= ?")
        params.append(date_from)

    if date_to:
        query.append("AND created_at < ?")
        params.append(date_to)

    return " ".join(query), params

def get_panel_logs(guild_id, search=None, category=None, level=None, source=None, date_from=None, date_to=None, sort="desc", limit=200):
    """Получить историю логов с фильтрами"""
    where_clause, params = _build_panel_logs_query(
        guild_id,
        search=search,
        category=category,
        level=level,
        source=source,
        date_from=date_from,
        date_to=date_to
    )
    order = "ASC" if str(sort).lower() == "asc" else "DESC"
    safe_limit = max(1, min(int(limit or 200), 500))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT id, guild_id, created_at, title, description, level, category, source
            FROM panel_logs
            {where_clause}
            ORDER BY created_at {order}, id {order}
            LIMIT ?
        """, (*params, safe_limit))
        return [dict(row) for row in cursor.fetchall()]

def get_panel_log_summary(guild_id, search=None, category=None, level=None, source=None, date_from=None, date_to=None):
    """Получить краткую сводку по логам"""
    where_clause, params = _build_panel_logs_query(
        guild_id,
        search=search,
        category=category,
        level=level,
        source=source,
        date_from=date_from,
        date_to=date_to
    )

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM panel_logs
            {where_clause}
        """, params)
        total = cursor.fetchone()["total"]

        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM panel_logs
            {where_clause} AND level = 'danger'
        """, params)
        danger_count = cursor.fetchone()["total"]

        cursor.execute(f"""
            SELECT title, created_at
            FROM panel_logs
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """, params)
        last_entry = cursor.fetchone()

        cursor.execute(f"""
            SELECT category, COUNT(*) AS total
            FROM panel_logs
            {where_clause}
            GROUP BY category
            ORDER BY total DESC, category ASC
            LIMIT 5
        """, params)
        top_categories = [dict(row) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM panel_logs
            WHERE guild_id = ? AND created_at >= datetime('now', 'localtime', 'start of day')
        """, (str(guild_id),))
        today_count = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM panel_logs
            WHERE guild_id = ?
              AND level = 'danger'
              AND created_at >= datetime('now', 'localtime', '-24 hours')
        """, (str(guild_id),))
        danger_last_day = cursor.fetchone()["total"]

    return {
        "total": total,
        "danger_count": danger_count,
        "today_count": today_count,
        "danger_last_day": danger_last_day,
        "last_entry": dict(last_entry) if last_entry else None,
        "top_categories": top_categories
    }

def get_panel_log_filters(guild_id):
    """Получить доступные значения фильтров по логам"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT category
            FROM panel_logs
            WHERE guild_id = ?
            ORDER BY category ASC
        """, (str(guild_id),))
        categories = [row["category"] for row in cursor.fetchall()]

        cursor.execute("""
            SELECT DISTINCT source
            FROM panel_logs
            WHERE guild_id = ?
            ORDER BY source ASC
        """, (str(guild_id),))
        sources = [row["source"] for row in cursor.fetchall()]

        cursor.execute("""
            SELECT DISTINCT level
            FROM panel_logs
            WHERE guild_id = ?
            ORDER BY level ASC
        """, (str(guild_id),))
        levels = [row["level"] for row in cursor.fetchall()]

    return {
        "categories": categories,
        "sources": sources,
        "levels": levels
    }

# ==========================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С АВТОРОЛЬЮ
# ==========================================

def get_auto_role(guild_id):
    """Получить настройки автороли для сервера"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT enabled, role_id FROM auto_roles WHERE guild_id = ?",
                      (str(guild_id),))
        row = cursor.fetchone()
        if row:
            role_ids = [role_id for role_id in (row['role_id'] or '').split(',') if role_id]
            return {'enabled': bool(row['enabled']), 'role_id': role_ids[0] if role_ids else None, 'role_ids': role_ids}
        return {'enabled': False, 'role_id': None, 'role_ids': []}

def set_auto_role(guild_id, enabled, role_id=None, role_ids=None):
    """Установить настройки автороли"""
    if role_ids is None:
        role_ids = [role_id] if role_id else []
    role_ids = [str(rid) for rid in role_ids if rid]
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO auto_roles (guild_id, enabled, role_id)
            VALUES (?, ?, ?)
        """, (str(guild_id), 1 if enabled else 0, ",".join(role_ids) if role_ids else None))
