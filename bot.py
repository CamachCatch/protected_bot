import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import asyncio
import datetime
import aiohttp
import re
import subprocess
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from functools import wraps
import threading
from dotenv import load_dotenv
from database import *

# Загрузка переменных окружения из .env файла
load_dotenv()

# Инициализация базы данных
init_database()

# ==========================================
# --- НАСТРОЙКИ ---
# ==========================================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents, help_command=None)
COMMANDS_SYNCED = False

OWNER_ID = 1501654483925602366
TEXADMIN_ID = 1558756135182405650
TRUSTED_USER_IDS = {OWNER_ID, TEXADMIN_ID}

MAIN_SERVER_ID = 1574399255605547049
AUTO_ROLE_ID = 1574399255605547053
BLACKLIST_ROLE_ID = 1510053933358514176

UI_COLOR = 0x2b2d31
RED_COLOR = 0xdd2e44

def is_trusted_user(user_id):
    return int(user_id) in TRUSTED_USER_IDS

# ==========================================
# --- ДИЗАЙН ---
# ==========================================
def supports_ephemeral(ctx):
    return getattr(ctx, "interaction", None) is not None

async def send_ctx(ctx, content=None, *, embed=None, ephemeral=False):
    if supports_ephemeral(ctx):
        return await ctx.send(content, embed=embed, ephemeral=ephemeral)
    return await ctx.send(content, embed=embed)

async def reply_ok(ctx, text, *, ephemeral=True):
    return await send_ctx(ctx, f"> ( + ) {text}", ephemeral=ephemeral)

async def reply_err(ctx, text, *, ephemeral=True):
    return await send_ctx(ctx, f"> ( - ) {text}", ephemeral=ephemeral)

async def reply_info(ctx, text, *, ephemeral=True):
    return await send_ctx(ctx, f"> ( ⏳ ) {text}", ephemeral=ephemeral)

@bot.check
async def require_main_server(ctx):
    return True

@bot.event
async def on_command_error(ctx, error):
    error = getattr(error, "original", error)

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.CheckFailure):
        return await reply_err(ctx, "Проверка доступа к команде не пройдена.")

    if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
        return await reply_err(ctx, "Неверные аргументы команды. Проверьте подсказки Discord или `/help`.")

    try:
        await reply_err(ctx, f"Ошибка выполнения команды: {error}")
    except Exception:
        pass
    print(f"Command error in {getattr(ctx.command, 'qualified_name', 'unknown')}: {error}")

async def send_interaction_error(interaction, text):
    content = f"> ( - ) {text}"
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction, error):
    error = getattr(error, "original", error)
    if isinstance(error, app_commands.CheckFailure):
        return await send_interaction_error(interaction, "Недостаточно прав для этой команды.")
    await send_interaction_error(interaction, f"Ошибка выполнения команды: {error}")
    print(f"App command error in {getattr(interaction.command, 'name', 'unknown')}: {error}")

async def sync_application_commands():
    global_commands = list(bot.tree.get_commands())
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()

    for command in global_commands:
        bot.tree.add_command(command, override=True)

    synced_guilds = 0

    for guild in bot.guilds:
        guild_obj = discord.Object(id=guild.id)
        bot.tree.clear_commands(guild=guild_obj)
        bot.tree.copy_global_to(guild=guild_obj)
        synced_guilds += len(await bot.tree.sync(guild=guild_obj))
        await asyncio.sleep(0.2)

    return len(global_commands), synced_guilds

# ==========================================
# --- БАЗЫ ДАННЫХ (SQLite через обертки) ---
# ==========================================

# Обертки для совместимости со старым кодом
class DBList:
    """Список-обертка для работы с БД"""
    def __init__(self, guild_id, get_func, add_func, remove_func):
        self.guild_id = str(guild_id)
        self.get_func = get_func
        self.add_func = add_func
        self.remove_func = remove_func
    
    def __iter__(self):
        return iter(self.get_func(self.guild_id))
    
    def __contains__(self, item):
        return item in self.get_func(self.guild_id)
    
    def append(self, item):
        if item not in self:
            self.add_func(self.guild_id, item)
    
    def remove(self, item):
        if item in self:
            self.remove_func(self.guild_id, item)

class DBDictOfLists:
    """Словарь списков для whitelist/blacklist/protected"""
    def __init__(self, get_func, add_func, remove_func):
        self.get_func = get_func
        self.add_func = add_func
        self.remove_func = remove_func
    
    def __getitem__(self, guild_id):
        return DBList(guild_id, self.get_func, self.add_func, self.remove_func)
    
    def __contains__(self, guild_id):
        return True

class DBDictOfDicts:
    """Словарь словарей для access/bans"""
    def __init__(self, get_func, set_func, del_func):
        self.get_func = get_func
        self.set_func = set_func
        self.del_func = del_func
    
    def __getitem__(self, guild_id):
        return DBDictInner(str(guild_id), self.get_func, self.set_func, self.del_func)
    
    def __contains__(self, guild_id):
        return True

class DBDictInner:
    """Внутренний словарь для конкретной гильдии"""
    def __init__(self, guild_id, get_func, set_func, del_func):
        self.guild_id = guild_id
        self.get_func = get_func
        self.set_func = set_func
        self.del_func = del_func
    
    def __getitem__(self, key):
        data = self.get_func(self.guild_id)
        return data.get(str(key))
    
    def __setitem__(self, key, value):
        self.set_func(self.guild_id, str(key), value)
    
    def __delitem__(self, key):
        self.del_func(self.guild_id, str(key))
    
    def __contains__(self, key):
        data = self.get_func(self.guild_id)
        return str(key) in data
    
    def get(self, key, default=None):
        data = self.get_func(self.guild_id)
        return data.get(str(key), default)
    
    def items(self):
        return self.get_func(self.guild_id).items()
    
    def copy(self):
        """Возвращает копию словаря"""
        return self.get_func(self.guild_id).copy()
    
    def keys(self):
        """Возвращает ключи словаря"""
        return self.get_func(self.guild_id).keys()
    
    def values(self):
        """Возвращает значения словаря"""
        return self.get_func(self.guild_id).values()

# Инициализация "словарей" для доступа к БД
USER_ACCESS = DBDictOfDicts(
    get_access,
    lambda gid, uid, lvl: add_access(gid, uid, lvl),
    lambda gid, uid: remove_access(gid, uid)
)

WHITELIST_IDS = DBDictOfLists(
    get_whitelist,
    add_to_whitelist,
    remove_from_whitelist
)

BLACKLIST_IDS = DBDictOfLists(
    get_blacklist,
    add_to_blacklist,
    remove_from_blacklist
)

PROTECTED_USERS = DBDictOfLists(
    get_protected,
    add_to_protected,
    remove_from_protected
)

TEMP_BANS = DBDictOfDicts(
    get_temp_bans,
    add_temp_ban,
    remove_temp_ban
)

A_TEMP_BANS = DBDictOfDicts(
    get_hard_bans,
    add_hard_ban,
    remove_hard_ban
)

class LogChannelsDict:
    def __getitem__(self, guild_id):
        return get_log_channel(str(guild_id))
    def __contains__(self, guild_id):
        return get_log_channel(str(guild_id)) is not None
    def __setitem__(self, guild_id, channel_id):
        set_log_channel(str(guild_id), str(channel_id))

LOG_CHANNELS = LogChannelsDict()

class AutoRolesDict:
    def __getitem__(self, guild_id):
        return get_auto_role(str(guild_id))
    def __setitem__(self, guild_id, value):
        enabled = value.get('enabled', False)
        role_ids = value.get('role_ids')
        role_id = value.get('role_id')
        set_auto_role(str(guild_id), enabled, role_id, role_ids)

AUTO_ROLES = AutoRolesDict()

def get_g_dict(data_dict, guild_id):
    """Получить словарь для гильдии"""
    return data_dict[guild_id]

def get_g_list(data_dict, guild_id):
    """Получить список для гильдии"""
    return data_dict[guild_id]

def load_data(file, default):
    """Загрузить данные из JSON файла (только для бэкапов)"""
    if not os.path.exists(file):
        return default
    try:
        with open(file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return default

def save_backup(file, data):
    """Сохранить бэкап в JSON файл"""
    try:
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

# ==========================================
# --- АНТИРЕЙД ЗАЩИТА ---
# ==========================================
CREATE_VIOLATION_TRACKER = {}  # {guild_id: {user_id: [timestamps]}}
MOD_ACTION_TRACKER = {}  # {guild_id: {user_id: [timestamps]}}

def track_recent_action(tracker, guild_id, user_id, window_seconds):
    now = datetime.datetime.now()
    gid = str(guild_id)
    uid = str(user_id)

    if gid not in tracker:
        tracker[gid] = {}
    if uid not in tracker[gid]:
        tracker[gid][uid] = []

    tracker[gid][uid] = [
        t for t in tracker[gid][uid]
        if (now - t).total_seconds() < window_seconds
    ]
    tracker[gid][uid].append(now)
    return len(tracker[gid][uid])

def clear_recent_actions(tracker, guild_id, user_id):
    gid = str(guild_id)
    uid = str(user_id)
    if gid in tracker and uid in tracker[gid]:
        tracker[gid][uid] = []

def track_creation_violation(guild_id, user_id):
    return track_recent_action(CREATE_VIOLATION_TRACKER, guild_id, user_id, 120)

def track_moderation_action(guild_id, user_id):
    return track_recent_action(MOD_ACTION_TRACKER, guild_id, user_id, 60)

def add_apusy_record(guild_id, user_id, days=999):
    end = datetime.datetime.now() + datetime.timedelta(days=days)
    end_str = end.strftime("%d.%m.%Y %H:%M:%S")
    g_abans = get_g_dict(A_TEMP_BANS, guild_id)
    g_abans[str(user_id)] = end_str
    return end_str

async def send_dm_warning(user, message):
    try:
        await user.send(message)
        return True
    except:
        return False

async def issue_apusy_ban(guild, user, reason, *, log_title=None, log_description=None, remove_from_whitelist=False):
    if remove_from_whitelist:
        wl = get_g_list(WHITELIST_IDS, guild.id)
        if user.id in wl:
            wl.remove(user.id)

    add_apusy_record(guild.id, user.id)

    ban_applied = False
    try:
        await guild.ban(discord.Object(id=user.id), reason=reason)
        ban_applied = True
    except:
        pass

    if log_title and log_description:
        await send_log(guild, log_title, log_description, RED_COLOR)

    return ban_applied

async def handle_unauthorized_creation(guild, actor, action_text, cleanup_coro):
    violation_count = track_creation_violation(guild.id, actor.id)

    cleanup_ok = True
    try:
        await cleanup_coro
    except:
        cleanup_ok = False

    if violation_count >= 3:
        ban_applied = False
        try:
            await guild.ban(actor, reason="Anti-Nuke: многократное создание каналов/ролей без WhiteList")
            ban_applied = True
        except:
            pass

        if ban_applied:
            clear_recent_actions(CREATE_VIOLATION_TRACKER, guild.id, actor.id)

        cleanup_note = "Объект удален." if cleanup_ok else "Объект не удалось удалить автоматически."
        ban_note = "Нарушитель забанен." if ban_applied else "Бот не смог выдать бан автоматически."
        await send_log(
            guild,
            "Anti-Nuke (Создание)",
            f"{actor.mention} {action_text} без WhiteList 3 раза за 2 минуты.\n{cleanup_note}\n{ban_note}",
            RED_COLOR
        )
        return

    dm_sent = await send_dm_warning(
        actor,
        f"Предупреждение Anti-Nuke на сервере {guild.name}: у вас нет WhiteList для создания каналов и ролей. "
        f"Зафиксировано {violation_count}/3 нарушений за 2 минуты. При третьем нарушении вы будете забанены."
    )
    cleanup_note = "Объект удален." if cleanup_ok else "Объект не удалось удалить автоматически."
    dm_note = "Предупреждение отправлено в ЛС." if dm_sent else "Не удалось отправить предупреждение в ЛС."
    await send_log(
        guild,
        "Anti-Nuke (Предупреждение)",
        f"{actor.mention} {action_text} без WhiteList.\n{cleanup_note}\nПредупреждение {violation_count}/3. {dm_note}",
        UI_COLOR
    )

def detect_log_category(title, description=""):
    text = f"{title} {description}".lower()

    if any(keyword in text for keyword in ["anti-nuke", "anti-raid", "вебхук", "боты", "предупреждение"]):
        return "security"
    if any(keyword in text for keyword in ["апусы", "apusy", "pusy", "бан", "разбан", "kick", "кик", "очистка"]):
        return "moderation"
    if any(keyword in text for keyword in ["вайтлист", "whitelist", "mod", "доступ"]):
        return "access"
    if "blacklist" in text or "черный список" in text:
        return "blacklist"
    if any(keyword in text for keyword in ["прота", "protected", "защита"]):
        return "protection"
    if any(keyword in text for keyword in ["автороль", "role", "роль", "giverole", "giveall", "unrole"]):
        return "roles"
    if any(keyword in text for keyword in ["backup", "бэкап", "save_server", "load_server"]):
        return "backup"
    return "system"

def detect_log_level(title, description="", color=UI_COLOR):
    text = f"{title} {description}".lower()

    if "предупреждение" in text:
        return "warning"
    if color == RED_COLOR or any(keyword in text for keyword in ["anti-nuke", "anti-raid", "apusy", "бан", "blacklist"]):
        return "danger"
    if any(keyword in text for keyword in ["разбан", "снят", "снял", "удален из", "обновлены"]):
        return "success"
    return "info"

def save_panel_log_entry(guild_id, title, description, *, color=UI_COLOR, source="bot"):
    created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        add_panel_log(
            guild_id,
            title,
            description,
            created_at,
            level=detect_log_level(title, description, color),
            category=detect_log_category(title, description),
            source=source
        )
    except Exception:
        pass

def save_web_log(guild_id, title, description, *, level="info", category="panel"):
    created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        add_panel_log(
            guild_id,
            title,
            description,
            created_at,
            level=level,
            category=category,
            source="panel"
        )
    except Exception:
        pass

# ==========================================
# --- ФУНКЦИЯ ЛОГИРОВАНИЯ ---
# ==========================================
async def send_log(guild, title, description, color=UI_COLOR):
    save_panel_log_entry(guild.id, title, description, color=color, source="discord")
    gid = str(guild.id)
    if gid in LOG_CHANNELS:
        ch_id = LOG_CHANNELS[gid]
        ch = guild.get_channel(ch_id)
        if ch:
            emb = discord.Embed(title=title, description=description, color=color)
            emb.set_footer(text=f"Time: {datetime.datetime.now().strftime('%H:%M:%S')}")
            try: 
                await ch.send(embed=emb)
            except: 
                pass

# ==========================================
# --- СПИСКИ ДОСТУПА И ВАЙТЛИСТА ---
# ==========================================
def load_txt_list(filename):
    """Загрузка списка ID из txt файла"""
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, "r", encoding='utf-8') as f:
            return [int(line.strip()) for line in f if line.strip()]
    except:
        return []

def load_hardcoded_users(filename):
    """Загрузка словаря пользователей из txt файла (формат: id:level)"""
    if not os.path.exists(filename):
        return {}
    try:
        result = {}
        with open(filename, "r", encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and ':' in line:
                    uid, lvl = line.split(':', 1)
                    result[uid.strip()] = lvl.strip()
        return result
    except:
        return {}

def load_revoked_users(filename):
    """Загрузка списка отозванных пользователей из txt файла"""
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, "r", encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    except:
        return []

HARDCODED_USERS = load_hardcoded_users("hardcoded_users.txt")
WL_ONLY_USERS = load_txt_list("wl_only_users.txt")
REVOKED_USERS = load_revoked_users("revoked_users.txt")

if MAIN_SERVER_ID != 0:
    main_gid = str(MAIN_SERVER_ID)
    if main_gid not in USER_ACCESS: USER_ACCESS[main_gid] = {}
    if main_gid not in WHITELIST_IDS: WHITELIST_IDS[main_gid] = []

    # Удаление отозванных пользователей
    for r_uid in REVOKED_USERS:
        if r_uid in USER_ACCESS[main_gid]:
            del USER_ACCESS[main_gid][r_uid]
        if int(r_uid) in WHITELIST_IDS[main_gid]:
            WHITELIST_IDS[main_gid].remove(int(r_uid))

    # Добавление хардкоженных пользователей
    for uid, lvl in HARDCODED_USERS.items(): 
        USER_ACCESS[main_gid][uid] = lvl
        
    # Добавление пользователей в вайтлист
    for uid in WL_ONLY_USERS:
        if uid not in WHITELIST_IDS[main_gid]: 
            WHITELIST_IDS[main_gid].append(uid)

def get_lvl(guild_id, uid):
    if is_trusted_user(uid): return 3
    if not guild_id: return 0
    guild_access = get_g_dict(USER_ACCESS, guild_id)
    lvl_name = guild_access.get(str(uid), "none")
    return {"low": 1, "mid": 2, "pusy": 3}.get(lvl_name, 0)

def get_bot_member(guild):
    return guild.me or guild.get_member(bot.user.id)

def parse_user_id(value):
    if value is None:
        return None
    if isinstance(value, (discord.User, discord.Member)):
        return value.id

    match = re.search(r"\d{15,25}", str(value))
    if not match:
        return None

    try:
        return int(match.group(0))
    except ValueError:
        return None

async def resolve_user(ctx, value):
    user_id = parse_user_id(value)
    if not user_id:
        return None, None

    member = ctx.guild.get_member(user_id) if ctx.guild else None
    if member:
        return user_id, member

    user = bot.get_user(user_id)
    if user:
        return user_id, user

    try:
        return user_id, await bot.fetch_user(user_id)
    except Exception:
        return user_id, None

def resolve_member(ctx, value):
    user_id = parse_user_id(value)
    if not user_id or not ctx.guild:
        return None, None
    return user_id, ctx.guild.get_member(user_id)

def get_blacklist_manageable_roles(member):
    bot_member = get_bot_member(member.guild)
    if not bot_member:
        return []
    return [
        role for role in member.roles
        if not role.is_default()
        and not role.managed
        and role.id != BLACKLIST_ROLE_ID
        and role < bot_member.top_role
    ]

async def apply_blacklist_restrictions(guild, user_id, reason="BlackList"):
    member = guild.get_member(int(user_id))
    if not member:
        return False, "пользователь не найден на сервере"

    warnings = []
    roles_to_remove = get_blacklist_manageable_roles(member)
    if not has_blacklist_role_snapshot(guild.id, member.id):
        set_blacklist_role_snapshot(guild.id, member.id, [role.id for role in roles_to_remove])

    if roles_to_remove:
        try:
            await member.remove_roles(*roles_to_remove, reason=reason)
        except Exception as e:
            warnings.append(f"не удалось снять роли: {e}")

    bot_member = get_bot_member(guild)
    blacklist_role = guild.get_role(BLACKLIST_ROLE_ID)
    if not blacklist_role:
        return True, "роль BlackList не найдена"
    if bot_member and blacklist_role >= bot_member.top_role:
        return True, "роль BlackList выше роли бота"
    if blacklist_role not in member.roles:
        try:
            await member.add_roles(blacklist_role, reason=reason)
        except Exception as e:
            warnings.append(f"не удалось выдать роль BlackList: {e}")

    return True, "; ".join(warnings) if warnings else None

async def restore_blacklist_roles(guild, user_id, reason="BlackList снят"):
    member = guild.get_member(int(user_id))
    snapshot = get_blacklist_role_snapshot(guild.id, user_id)

    if not member:
        remove_blacklist_role_snapshot(guild.id, user_id)
        return False, "пользователь не найден на сервере"

    bot_member = get_bot_member(guild)
    blacklist_role = guild.get_role(BLACKLIST_ROLE_ID)
    warnings = []
    if blacklist_role and blacklist_role in member.roles and bot_member and blacklist_role < bot_member.top_role:
        try:
            await member.remove_roles(blacklist_role, reason=reason)
        except Exception as e:
            warnings.append(f"не удалось снять роль BlackList: {e}")

    roles_to_restore = []
    for role_id in snapshot:
        role = guild.get_role(int(role_id))
        if role and role not in member.roles and not role.managed and bot_member and role < bot_member.top_role:
            roles_to_restore.append(role)

    if roles_to_restore:
        try:
            await member.add_roles(*roles_to_restore, reason=reason)
        except Exception as e:
            warnings.append(f"не удалось вернуть роли: {e}")

    remove_blacklist_role_snapshot(guild.id, user_id)
    return True, "; ".join(warnings) if warnings else None

# ==========================================
# --- БЫСТРЫЙ ANTI-NUKE ---
# ==========================================
nuke_tracker = {}

async def check_nuke_threat(guild, user, action_name):
    wl = get_g_list(WHITELIST_IDS, guild.id)
    if user.id == bot.user.id or is_trusted_user(user.id) or user.id in wl:
        return False
    now = datetime.datetime.now()
    if user.id not in nuke_tracker: 
        nuke_tracker[user.id] = []
    nuke_tracker[user.id] = [t for t in nuke_tracker[user.id] if (now - t).total_seconds() < 8]
    nuke_tracker[user.id].append(now)
    
    if len(nuke_tracker[user.id]) >= 3:
        try:
            await guild.ban(user, reason=f"Anti-Nuke: лимит ({action_name})")
            nuke_tracker[user.id] = []
            await send_log(guild, "Anti-Nuke", f"{user.mention} забанен: {action_name}", RED_COLOR)
            return True
        except: 
            pass
    return False

async def get_audit_actor(guild, action, target_id, max_retries=5):
    for _ in range(max_retries):
        try:
            async for entry in guild.audit_logs(limit=3, action=action):
                if entry.target.id == target_id:
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 10:
                        return entry.user
        except: 
            pass
        await asyncio.sleep(0.1)
    return None

@bot.event
async def on_member_ban(guild, user):
    actor = await get_audit_actor(guild, discord.AuditLogAction.ban, user.id)
    if actor:
        wl = get_g_list(WHITELIST_IDS, guild.id)
        
        if actor.id in wl and not is_trusted_user(actor.id):
            action_count = track_moderation_action(guild.id, actor.id)
            if action_count > 3:
                ban_applied = await issue_apusy_ban(
                    guild,
                    actor,
                    "Anti-Raid: 4+ банов/киков за минуту при наличии WhiteList",
                    log_title="Anti-Raid (Массовые баны/кики)",
                    log_description=f"{actor.mention} забанил или кикнул более 3 человек за минуту.\nWhiteList снят, выдан APusy бан.",
                    remove_from_whitelist=True
                )
                if ban_applied:
                    clear_recent_actions(MOD_ACTION_TRACKER, guild.id, actor.id)
            return
        
        if actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
            try:
                await guild.unban(user, reason="Anti-Nuke: отмена бана")
            except:
                pass

            await issue_apusy_ban(
                guild,
                actor,
                "Anti-Nuke: бан через Discord без WhiteList",
                log_title="Anti-Nuke (Ban)",
                log_description=f"{actor.mention} забанил {user.mention} без WhiteList.\nЖертва разбанена, нарушителю выдан APusy бан."
            )

@bot.event
async def on_member_remove(member):
    guild = member.guild
    actor = await get_audit_actor(guild, discord.AuditLogAction.kick, member.id)
    if actor:
        wl = get_g_list(WHITELIST_IDS, guild.id)
        if actor.id in wl and not is_trusted_user(actor.id):
            action_count = track_moderation_action(guild.id, actor.id)
            if action_count > 3:
                ban_applied = await issue_apusy_ban(
                    guild,
                    actor,
                    "Anti-Raid: 4+ банов/киков за минуту при наличии WhiteList",
                    log_title="Anti-Raid (Массовые баны/кики)",
                    log_description=f"{actor.mention} забанил или кикнул более 3 человек за минуту.\nWhiteList снят, выдан APusy бан.",
                    remove_from_whitelist=True
                )
                if ban_applied:
                    clear_recent_actions(MOD_ACTION_TRACKER, guild.id, actor.id)
            return

        if actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
            try:
                await guild.ban(actor, reason="Anti-Nuke: кик без вайтлиста")
                await send_log(guild, "Anti-Nuke (Kick)", f"{actor.mention} кикнул {member.mention} без прав. \nНарушитель забанен.", RED_COLOR)
            except: 
                pass

@bot.event
async def on_guild_channel_delete(channel):
    async def process():
        guild = channel.guild
        actor = await get_audit_actor(guild, discord.AuditLogAction.channel_delete, channel.id)
        wl = get_g_list(WHITELIST_IDS, guild.id)
        if actor and actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
            is_nuker = await check_nuke_threat(guild, actor, "Удаление каналов")
            try:
                await channel.clone(reason="Anti-Nuke: восстановление канала")
                if not is_nuker:
                    await guild.ban(actor, reason="Anti-Nuke: удаление каналов")
                    await send_log(guild, "Anti-Nuke (Каналы)", f"{actor.mention} удалил канал #{channel.name} без прав. \nКанал восстановлен, нарушитель забанен.", RED_COLOR)
            except: 
                pass
    asyncio.create_task(process())

@bot.event
async def on_guild_channel_create(channel):
    async def process():
        guild = channel.guild
        actor = await get_audit_actor(guild, discord.AuditLogAction.channel_create, channel.id)
        wl = get_g_list(WHITELIST_IDS, guild.id)
        if actor and actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
            await handle_unauthorized_creation(
                guild,
                actor,
                f"создал канал #{channel.name}",
                channel.delete(reason="Anti-Nuke: удаление канала без WhiteList")
            )
    asyncio.create_task(process())

@bot.event
async def on_guild_role_create(role):
    async def process():
        guild = role.guild
        actor = await get_audit_actor(guild, discord.AuditLogAction.role_create, role.id)
        wl = get_g_list(WHITELIST_IDS, guild.id)
        if actor and actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
            await handle_unauthorized_creation(
                guild,
                actor,
                f"создал роль @{role.name}",
                role.delete(reason="Anti-Nuke: удаление роли без WhiteList")
            )
    asyncio.create_task(process())

@bot.event
async def on_guild_role_delete(role):
    async def process():
        guild = role.guild
        actor = await get_audit_actor(guild, discord.AuditLogAction.role_delete, role.id)
        wl = get_g_list(WHITELIST_IDS, guild.id)
        if actor and actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
            is_nuker = await check_nuke_threat(guild, actor, "Удаление ролей")
            try:
                await guild.create_role(name=role.name, permissions=role.permissions, color=role.color, hoist=role.hoist, mentionable=role.mentionable, reason="Anti-Nuke: восстановление роли")
                if not is_nuker:
                    await guild.ban(actor, reason="Anti-Nuke: удаление ролей")
                    await send_log(guild, "Anti-Nuke (Роли)", f"{actor.mention} удалил роль @{role.name} без прав. \nРоль восстановлена, нарушитель забанен.", RED_COLOR)
            except: 
                pass
    asyncio.create_task(process())

@bot.event
async def on_webhooks_update(channel):
    async def process():
        guild = channel.guild
        wl = get_g_list(WHITELIST_IDS, guild.id)
        actor = await get_audit_actor(guild, discord.AuditLogAction.webhook_create, channel.id)
        if actor and actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
            await check_nuke_threat(guild, actor, "Создание вебхуков")
            try:
                await guild.ban(actor, reason="Anti-Nuke: вебхуки")
                await send_log(guild, "Anti-Nuke (Вебхуки)", f"{actor.mention} создал вебхук в {channel.mention} без прав. \nНарушитель забанен.", RED_COLOR)
            except: 
                pass
    asyncio.create_task(process())

# ==========================================
# ВХОД И БЭКАПЫ РОЛЕЙ
# ==========================================
@bot.event
async def on_member_join(member):
    if member.bot:
        actor = await get_audit_actor(member.guild, discord.AuditLogAction.bot_add, member.id)
        if actor and (actor.id == bot.user.id or is_trusted_user(actor.id)):
            return

        try: 
            await member.ban(reason="Anti-Nuke: краш-бот")
        except: 
            pass
        wl = get_g_list(WHITELIST_IDS, member.guild.id)
        
        if actor and actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
            try: 
                await member.guild.ban(actor, reason="Anti-Nuke: боты")
                await send_log(member.guild, "Anti-Nuke (Боты)", f"{actor.mention} добавил бота {member.mention}. \nБот и нарушитель забанены.", RED_COLOR)
            except: 
                pass
        return

    if member.id in get_g_list(BLACKLIST_IDS, member.guild.id):
        await apply_blacklist_restrictions(member.guild, member.id, reason="BlackList: вход пользователя")
        return

    # Автороль для новых участников
    auto_roles = get_g_dict(AUTO_ROLES, member.guild.id)
    role_ids = auto_roles.get('role_ids', [])
    if auto_roles.get('enabled') and role_ids:
        roles_to_add = []
        for role_id in role_ids:
            role = member.guild.get_role(int(role_id))
            if role and role < member.guild.me.top_role:
                roles_to_add.append(role)
        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="Автороль при входе")
                mentions = ", ".join(role.mention for role in roles_to_add)
                await send_log(member.guild, "Автороль", f"Выданы роли {mentions} участнику {member.mention}", UI_COLOR)
            except Exception as e:
                print(f"Ошибка выдачи автороли: {e}")

    if AUTO_ROLE_ID != 0:
        role = member.guild.get_role(AUTO_ROLE_ID)
        if role:
            try: 
                await member.add_roles(role, reason="Auto-role")
            except: 
                pass

@bot.event
async def on_member_unban(guild, user):
    if is_trusted_user(user.id): return
    
    g_bans = get_g_dict(TEMP_BANS, guild.id)
    a_bans = get_g_dict(A_TEMP_BANS, guild.id) 

    if str(user.id) in g_bans or str(user.id) in a_bans:
        try: 
            await guild.ban(user, reason="Система: обход темп-бана")
            await send_log(guild, "Защита", f"Снятие бана с <@{user.id}> раньше времени. \nБот вернул бан.", RED_COLOR)
        except: 
            pass

@bot.event
async def on_voice_state_update(member, before, after):
    if after.channel is not None:
        bl = get_g_list(BLACKLIST_IDS, member.guild.id)
        if member.id in bl:
            if get_lvl(member.guild.id, member.id) < 3:
                try:
                    await member.move_to(None, reason="Blacklist")
                except: 
                    pass

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: return
    
    bl = get_g_list(BLACKLIST_IDS, message.guild.id)
    if message.author.id in bl:
        if get_lvl(message.guild.id, message.author.id) < 3:
            if not message.content.startswith(f"{bot.command_prefix}unblacklist"):
                try: 
                    await message.delete()
                except: 
                    pass
                return 
    await bot.process_commands(message)

# ==========================================
# СИСТЕМА РОЛЕЙ И ПРОТА 
# ==========================================
async def check_anti_role(guild, member, added_roles, baseline_role_ids):
    wl = get_g_list(WHITELIST_IDS, guild.id)
    actor = await get_audit_actor(guild, discord.AuditLogAction.member_role_update, member.id)
    if actor and actor.id != bot.user.id and actor.id not in wl and not is_trusted_user(actor.id):
        for attempt in range(4):
            current_member = guild.get_member(member.id)
            if current_member:
                roles_to_remove = [
                    role for role in current_member.roles
                    if role.id not in baseline_role_ids and role < guild.me.top_role and not role.is_default()
                ]
                if roles_to_remove:
                    try:
                        await current_member.remove_roles(*roles_to_remove, reason="Anti-Role: выдача без прав")
                    except:
                        pass
            await asyncio.sleep(0.35)

@bot.event
async def on_member_update(before, after):
    if before.roles == after.roles: return
    guild = after.guild

    prota = get_g_list(PROTECTED_USERS, guild.id)
    if after.id in prota:
        missing_roles = [r for r in before.roles if r not in after.roles and r < guild.me.top_role and not r.is_default()]
        if missing_roles:
            try:
                await after.add_roles(*missing_roles, reason="Prota: защита")
            except: 
                pass

    added_roles = [r for r in after.roles if r not in before.roles]
    if added_roles:
        asyncio.create_task(check_anti_role(guild, after, added_roles, {role.id for role in before.roles}))

@tasks.loop(seconds=10)
async def check_unbans():
    now = datetime.datetime.now()
    
    # Проходим по всем гильдиям бота
    for guild in bot.guilds:
        gid_str = str(guild.id)
        
        # Проверяем временные баны (pusy)
        temp_bans = get_g_dict(TEMP_BANS, guild.id)
        to_remove = []
        
        for uid, exp in list(temp_bans.items()):
            try: 
                exp_time = datetime.datetime.strptime(exp, "%d.%m.%Y %H:%M:%S")
            except ValueError:
                try: 
                    exp_time = datetime.datetime.strptime(exp, "%d.%m.%Y %H:%M")
                except: 
                    continue
            if now >= exp_time: 
                to_remove.append(uid)
        
        for uid in to_remove:
            if uid in temp_bans:
                del temp_bans[uid]
                try: 
                    await guild.unban(discord.Object(id=int(uid)), reason="Система: срок бана истек")
                    await send_log(guild, "Разбан", f"бан pusy снят с <@{uid}>.", UI_COLOR)
                except: 
                    pass
        
        # Проверяем жесткие баны (apusy)
        hard_bans = get_g_dict(A_TEMP_BANS, guild.id)
        to_remove = []
        
        for uid, exp in list(hard_bans.items()):
            try: 
                exp_time = datetime.datetime.strptime(exp, "%d.%m.%Y %H:%M:%S")
            except ValueError:
                try: 
                    exp_time = datetime.datetime.strptime(exp, "%d.%m.%Y %H:%M")
                except: 
                    continue
            if now >= exp_time: 
                to_remove.append(uid)
        
        for uid in to_remove:
            if uid in hard_bans:
                del hard_bans[uid]
                try: 
                    await guild.unban(discord.Object(id=int(uid)), reason="Система: срок бана истек")
                    await send_log(guild, "Разбан", f"бан apusy снят с <@{uid}>.", UI_COLOR)
                except: 
                    pass

# ==========================================
# --- КОМАНДЫ ---
# ==========================================
@bot.hybrid_command(name="setlog", description="Установить канал для логов защиты")
@app_commands.rename(channel="канал")
@app_commands.describe(channel="Канал, куда бот будет отправлять логи")
async def setlog_cmd(ctx, channel: discord.TextChannel = None):
    if not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Команда доступна только доверенным пользователям.")
    if not channel: return await reply_err(ctx, "Укажите канал")

    LOG_CHANNELS[str(ctx.guild.id)] = channel.id
    await reply_ok(ctx, f"Канал логов установлен на {channel.mention}")

@bot.hybrid_command(name="help", description="Показать доступные вам команды")
async def help_cmd(ctx):
    guild_id = ctx.guild.id if ctx.guild else None
    lvl = get_lvl(guild_id, ctx.author.id)
    is_trusted = is_trusted_user(ctx.author.id)

    emb = discord.Embed(title="Справочник команд", color=RED_COLOR)
    emb.set_footer(text=f"Ваш уровень: {'owner' if is_trusted else lvl}")
    emb.add_field(name="[ user ]", value="```\nhelp\nlist\n```", inline=False)

    if lvl >= 1 or is_trusted:
        emb.add_field(name="[ low ]", value="```\nblacklist\nunblacklist\n```", inline=False)
    if lvl >= 2 or is_trusted:
        emb.add_field(name="[ mid ]", value="```\nclear\npusy\nunpusy\nunrole\nпрота\nанпрота\n```", inline=False)
    if lvl >= 3 or is_trusted:
        emb.add_field(name="[ pusy ]", value="```\nmod\nunmod\nwl_add\nwl_remove\ngiverole\ngiveall\n```", inline=False)
    if is_trusted:
        emb.add_field(name="[ owner ]", value="```\nsetlog\napusy\naunpusy\nsay\nsave_server\nlist_backups\nload_server\n```", inline=False)

    await send_ctx(ctx, embed=emb, ephemeral=True)

@bot.hybrid_command(name="list", description="Показать списки доступов, WL, защиты, BL и банов")
async def list_cmd(ctx):
    if not ctx.guild:
        return await reply_err(ctx, "Эта команда доступна только на сервере.")
    emb = discord.Embed(title=f"{ctx.guild.name}", color=RED_COLOR)

    def safe_fmt(lst):
        if not lst: return "```Пусто```"
        res = "\n".join(lst)
        return res[:1015] + "\n...и другие" if len(res) > 1024 else res

    def get_name(uid):
        return f"<@{uid}>"

    weights = {"pusy": 3, "mid": 2, "low": 1}
    g_access = get_g_dict(USER_ACCESS, ctx.guild.id).copy()

    # Добавляем доверенных пользователей как pusy
    if str(OWNER_ID) not in g_access: g_access[str(OWNER_ID)] = "pusy"
    if str(TEXADMIN_ID) not in g_access: g_access[str(TEXADMIN_ID)] = "pusy"

    acc_sorted = sorted(g_access.items(), key=lambda x: weights.get(x[1], 0), reverse=True)

    acc = [f"{get_name(u)} - {l}" for u, l in acc_sorted]
    emb.add_field(name=f"Доступы [{len(acc)}]", value=safe_fmt(acc), inline=False)

    wl = [f"{get_name(u)}" for u in get_g_list(WHITELIST_IDS, ctx.guild.id)]
    emb.add_field(name=f"Вайтлист [{len(wl)}]", value=safe_fmt(wl), inline=False)

    prota = [f"{get_name(u)}" for u in get_g_list(PROTECTED_USERS, ctx.guild.id)]
    emb.add_field(name=f"Прота [{len(prota)}]", value=safe_fmt(prota), inline=False)

    bl = [f"{get_name(u)}" for u in get_g_list(BLACKLIST_IDS, ctx.guild.id)]
    emb.add_field(name=f"Черный список [{len(bl)}]", value=safe_fmt(bl), inline=False)

    combined_bans = get_g_dict(TEMP_BANS, ctx.guild.id).copy()
    combined_bans.update(get_g_dict(A_TEMP_BANS, ctx.guild.id))

    tb = [f"{get_name(u)} (до {exp})" for u, exp in combined_bans.items()]
    emb.add_field(name=f"Временные баны [{len(tb)}]", value=safe_fmt(tb), inline=False)

    await send_ctx(ctx, embed=emb, ephemeral=True)

@bot.hybrid_command(name="clear", description="Удалить сообщения в текущем канале")
@app_commands.rename(amount="количество")
@app_commands.describe(amount="Количество сообщений для удаления")
async def clear(ctx, amount: int = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 2:
        return await reply_err(ctx, "Недостаточно прав для очистки сообщений.")
    if not amount or amount < 1: return await reply_err(ctx, "Укажите количество сообщений")
    if amount > 1000: amount = 1000

    try:
        is_slash = supports_ephemeral(ctx)
        purge_limit = amount if is_slash else amount + 1
        deleted = await ctx.channel.purge(limit=purge_limit)
        deleted_count = len(deleted) if is_slash else max(len(deleted) - 1, 0)
        msg = await reply_ok(ctx, f"Удалено сообщений: **{deleted_count}**")
        await send_log(ctx.guild, "Очистка чата", f"{ctx.author.mention} удалил {deleted_count} сообщений в {ctx.channel.mention}.")
        await asyncio.sleep(10)
        try: 
            await msg.delete()
        except: 
            pass
    except discord.Forbidden:
        await reply_err(ctx, "Нет прав на удаление!")
    except Exception as e:
        await reply_err(ctx, f"Ошибка: {e}")

@bot.hybrid_command(name="blacklist", description="Добавить пользователя в черный список")
@app_commands.rename(user="пользователь")
@app_commands.describe(user="Пользователь для добавления в черный список")
async def blacklist(ctx, user: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 1:
        return await reply_err(ctx, "Недостаточно прав для черного списка.")
    if not user: return await reply_err(ctx, "Укажите пользователя")
    user_id, _ = await resolve_user(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    user_mention = f"<@{user_id}>"
    if is_trusted_user(user_id): return await reply_err(ctx, "Нельзя добавить создателя")

    bl = get_g_list(BLACKLIST_IDS, ctx.guild.id)
    if user_id not in bl:
        bl.append(user_id)
        ok, warning = await apply_blacklist_restrictions(
            ctx.guild,
            user_id,
            reason=f"BlackList: {ctx.author.name}"
        )
        
        member = ctx.guild.get_member(user_id)
        if member and member.voice:
            try: 
                await member.move_to(None, reason="Blacklist")
            except: 
                pass
            
        suffix = f" ({warning})" if warning else ""
        await reply_ok(ctx, f"{user_mention} добавлен в черный список{suffix}")
        await send_log(ctx.guild, "BlackList", f"{ctx.author.mention} добавил {user_mention} в BlackList.", RED_COLOR)
    else: 
        await reply_err(ctx, f"{user_mention} уже в черном списке")

@bot.hybrid_command(name="unblacklist", description="Удалить пользователя из черного списка")
@app_commands.rename(user="пользователь")
@app_commands.describe(user="Пользователь для удаления из черного списка")
async def unblacklist(ctx, user: str = None):
    if not user: return await reply_err(ctx, "Укажите пользователя")
    user_id, _ = await resolve_user(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    user_mention = f"<@{user_id}>"
    is_self_unban = (user_id == ctx.author.id)
    if not is_self_unban and get_lvl(ctx.guild.id, ctx.author.id) < 1:
        return await reply_err(ctx, "Недостаточно прав для черного списка.")

    bl = get_g_list(BLACKLIST_IDS, ctx.guild.id)
    if user_id in bl:
        bl.remove(user_id)
        ok, warning = await restore_blacklist_roles(
            ctx.guild,
            user_id,
            reason=f"BlackList снят: {ctx.author.name}"
        )
        suffix = f" ({warning})" if warning else ""
        await reply_ok(ctx, f"{user_mention} удален из черного списка{suffix}")
        await send_log(ctx.guild, "BlackList", f"{ctx.author.mention} вынес {user_mention} из BlackList.")
    else: 
        await reply_err(ctx, "Этого пользователя нет в черном списке")

@bot.hybrid_command(name="прота", description="Добавить пользователя в защищенные")
@app_commands.rename(user="пользователь")
@app_commands.describe(user="Пользователь для защиты")
async def prota_cmd(ctx, user: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 2:
        return await reply_err(ctx, "Недостаточно прав для защиты пользователей.")
    if not user: return await reply_err(ctx, "Укажите пользователя")
    user_id, _ = await resolve_user(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    user_mention = f"<@{user_id}>"

    prota = get_g_list(PROTECTED_USERS, ctx.guild.id)
    if user_id not in prota:
        prota.append(user_id)
        await reply_ok(ctx, f"{user_mention} теперь прота")
        await send_log(ctx.guild, "Прота", f"{ctx.author.mention} выдал проту {user_mention}.")
    else: 
        await reply_err(ctx, f"{user_mention} уже в проте")

@bot.hybrid_command(name="анпрота", description="Удалить пользователя из защищенных")
@app_commands.rename(user="пользователь")
@app_commands.describe(user="Пользователь для снятия защиты")
async def unprota_cmd(ctx, user: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 2:
        return await reply_err(ctx, "Недостаточно прав для снятия защиты.")
    if not user: return await reply_err(ctx, "Укажите пользователя")
    user_id, _ = await resolve_user(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    user_mention = f"<@{user_id}>"

    prota = get_g_list(PROTECTED_USERS, ctx.guild.id)
    if user_id in prota:
        prota.remove(user_id)
        await reply_ok(ctx, f"{user_mention} убран из проты")
        await send_log(ctx.guild, "Прота", f"{ctx.author.mention} снял проту с {user_mention}.")
    else: 
        await reply_err(ctx, f"{user_mention} не в проте")

@bot.hybrid_command(name="pusy", description="Выдать временный pusy-бан")
@app_commands.rename(target="цель", duration="длительность", reason="причина")
@app_commands.describe(target="ID или упоминание пользователя", duration="Срок: 10s, 30m, 2h, 7d", reason="Причина бана")
async def pusy(ctx, target: str = None, duration: str = None, *, reason: str = "Не указана"):
    if get_lvl(ctx.guild.id, ctx.author.id) < 2:
        return await reply_err(ctx, "Недостаточно прав для выдачи бана.")
    if not target or not duration:
        return await reply_err(ctx, "Укажите пользователя и время (s, m, h, d).")

    try:
        target_id = int(re.sub(r'[^\d]', '', target))
    except:
        return await reply_err(ctx, "Неверный ID.")
        
    if is_trusted_user(target_id):
        return await reply_err(ctx, "Данного пользователя нельзя забанить!")

    match = re.match(r"(\d+)([smhd])", duration.lower())
    if not match: 
        return await reply_err(ctx, "Формат: s, m, h, d.")

    amt = int(match.group(1))
    unit = match.group(2)
    delta = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}[unit]
    end = datetime.datetime.now() + datetime.timedelta(**{delta: amt})
    end_str = end.strftime("%d.%m.%Y %H:%M:%S")

    try:
        await ctx.guild.ban(discord.Object(id=target_id), reason=f"pusy бан. Модератор: {ctx.author.name}. Причина: {reason}")
    except discord.Forbidden:
        return await reply_err(ctx, "Нет прав!")
    except Exception:
        pass 
        
    g_bans = get_g_dict(TEMP_BANS, ctx.guild.id)
    g_bans[str(target_id)] = end_str

    time_fmt = end.strftime('%H:%M:%S %d.%m')
    await reply_ok(ctx, f"<@{target_id}> забанен до {time_fmt}")
    await send_log(ctx.guild, "Pusy", f"{ctx.author.mention} забанил <@{target_id}>.\nСрок: до {time_fmt}\nПричина: {reason}", RED_COLOR)

@bot.hybrid_command(name="unpusy", description="Снять pusy-бан")
@app_commands.rename(uid="айди")
@app_commands.describe(uid="ID пользователя")
async def unpusy(ctx, uid: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 2:
        return await reply_err(ctx, "Недостаточно прав для снятия бана.")
    if not uid: return await reply_err(ctx, "Укажите ID")
    user_id = parse_user_id(uid)
    if not user_id: return await reply_err(ctx, "Неверный ID.")

    a_bans = get_g_dict(A_TEMP_BANS, ctx.guild.id)
    if str(user_id) in a_bans:
        return await reply_err(ctx, "Нет прав на снятие этого бана.")

    g_bans = get_g_dict(TEMP_BANS, ctx.guild.id)
    if str(user_id) in g_bans: 
        del g_bans[str(user_id)]
        
    try: 
        await ctx.guild.unban(discord.Object(id=user_id), reason=f"unpusy. Модератор: {ctx.author.name}")
        await reply_ok(ctx, f"бан снят с <@{user_id}>")
        await send_log(ctx.guild, "Unpusy", f"{ctx.author.mention} снял pusy бан с <@{user_id}>.")
    except: 
        await reply_ok(ctx, f"<@{user_id}> удален из базы")

@bot.hybrid_command(name="apusy", description="Выдать усиленный временный apusy-бан")
@app_commands.rename(target="цель", duration="длительность", reason="причина")
@app_commands.describe(target="ID или упоминание пользователя", duration="Срок: 10s, 30m, 2h, 7d", reason="Причина бана")
async def apusy_cmd(ctx, target: str = None, duration: str = None, *, reason: str = "Не указана"):
    if not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Команда доступна только доверенным пользователям.")
    if not target or not duration:
        return await reply_err(ctx, "Укажите пользователя и время (s, m, h, d).")

    try:
        target_id = int(re.sub(r'[^\d]', '', target))
    except:
        return await reply_err(ctx, "Неверный ID.")
        
    if is_trusted_user(target_id):
        return await reply_err(ctx, "Данного пользователя нельзя забанить!")

    match = re.match(r"(\d+)([smhd])", duration.lower())
    if not match: 
        return await reply_err(ctx, "Формат: s, m, h, d.")

    amt = int(match.group(1))
    unit = match.group(2)
    delta = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}[unit]
    end = datetime.datetime.now() + datetime.timedelta(**{delta: amt})
    end_str = end.strftime("%d.%m.%Y %H:%M:%S")

    try:
        await ctx.guild.ban(discord.Object(id=target_id), reason=f"apusy бан. Выдал: {ctx.author.name}. Причина: {reason}")
    except discord.Forbidden:
        return await reply_err(ctx, "Нет прав!")
    except Exception:
        pass 
        
    a_bans = get_g_dict(A_TEMP_BANS, ctx.guild.id)
    a_bans[str(target_id)] = end_str

    time_fmt = end.strftime('%H:%M:%S %d.%m')
    await reply_ok(ctx, f"<@{target_id}> темпбан до {time_fmt}")
    await send_log(ctx.guild, "Apusy", f"{ctx.author.mention} выдал apusy <@{target_id}>.\nСрок: до {time_fmt}\nПричина: {reason}", RED_COLOR)

@bot.hybrid_command(name="aunpusy", description="Снять apusy-бан")
@app_commands.rename(uid="айди")
@app_commands.describe(uid="ID пользователя")
async def aunpusy_cmd(ctx, uid: str = None):
    if not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Команда доступна только доверенным пользователям.")
    if not uid: return await reply_err(ctx, "Укажите ID")
    user_id = parse_user_id(uid)
    if not user_id: return await reply_err(ctx, "Неверный ID.")

    a_bans = get_g_dict(A_TEMP_BANS, ctx.guild.id)
    if str(user_id) in a_bans: 
        del a_bans[str(user_id)]
        
    try: 
        await ctx.guild.unban(discord.Object(id=user_id), reason=f"aunpusy. Снял: {ctx.author.name}")
        await reply_ok(ctx, f"apusy бан снят с <@{user_id}>")
        await send_log(ctx.guild, "Aunpusy", f"{ctx.author.mention} снял apusy бан с <@{user_id}>.")
    except: 
        await reply_ok(ctx, f"<@{user_id}> удален из базы")

@bot.hybrid_command(name="giverole", description="Выдать роль пользователю")
@app_commands.rename(user="пользователь", r_id="роль")
@app_commands.describe(user="Пользователь", r_id="ID роли")
async def giverole(ctx, user: str = None, r_id: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 3:
        return await reply_err(ctx, "Недостаточно прав для выдачи ролей.")
    if not user or not r_id: return await reply_err(ctx, "Укажите пользователя и ID роли")
    user_id, member = resolve_member(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    if not member: return await reply_err(ctx, "Пользователь не найден на сервере")

    role_id = parse_user_id(r_id)
    if not role_id: return await reply_err(ctx, "Неверный ID роли.")
    role = ctx.guild.get_role(role_id)
    if not role: return await reply_err(ctx, "Роль не найдена")

    try:
        await member.add_roles(role, reason=f"giverole: {ctx.author.name}")
        await reply_ok(ctx, f"роль **{role.name}** выдана {member.mention}")
        await send_log(ctx.guild, "Giverole", f"{ctx.author.mention} выдал роль **{role.name}** {member.mention}.")
    except discord.Forbidden:
        await reply_err(ctx, "Ошибка прав.")
    except Exception as e:
        await reply_err(ctx, f"Ошибка: {e}")

@bot.hybrid_command(name="giveall", description="Выдать роль всем участникам")
@app_commands.rename(r_id="роль")
@app_commands.describe(r_id="ID роли")
async def giveall(ctx, r_id: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 3:
        return await reply_err(ctx, "Недостаточно прав для массовой выдачи ролей.")
    if not r_id: return await reply_err(ctx, "Укажите ID роли")
    
    role_id = parse_user_id(r_id)
    if not role_id: return await reply_err(ctx, "Неверный ID роли.")
    role = ctx.guild.get_role(role_id)
    if not role: return await reply_err(ctx, "Роль не найдена")

    msg = await reply_info(ctx, f"Выдаю роль **{role.name}**...")

    for m in ctx.guild.members:
        if role not in m.roles:
            try: 
                await m.add_roles(role, reason=f"giveall: {ctx.author.name}")
                await asyncio.sleep(0.05)
            except: 
                continue
            
    await msg.edit(content=f"> **( + )** Роль **{role.name}** выдана всем")
    await send_log(ctx.guild, "Giveall", f"{ctx.author.mention} выдал всем роль **{role.name}**.")

@bot.hybrid_command(name="unrole", description="Снять роль с пользователя")
@app_commands.rename(user="пользователь", r_id="роль")
@app_commands.describe(user="Пользователь", r_id="ID роли")
async def unrole(ctx, user: str = None, r_id: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 2:
        return await reply_err(ctx, "Недостаточно прав для снятия ролей.")
    if not user or not r_id: return await reply_err(ctx, "Укажите пользователя и ID роли")
    user_id, member = resolve_member(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    if not member: return await reply_err(ctx, "Пользователь не найден на сервере")
    
    role_id = parse_user_id(r_id)
    if not role_id: return await reply_err(ctx, "Неверный ID роли.")
    role = ctx.guild.get_role(role_id)
    if not role: return await reply_err(ctx, "Роль не найдена")
    
    if role in member.roles:
        try:
            await member.remove_roles(role, reason=f"unrole: {ctx.author.name}")
            await reply_ok(ctx, f"роль {role.name} снята у {member.mention}")
            await send_log(ctx.guild, "Unrole", f"{ctx.author.mention} снял роль {role.name} у {member.mention}.")
        except: 
            await reply_err(ctx, "Ошибка прав")

@bot.hybrid_command(name="mod", description="Выдать уровень доступа пользователю")
@app_commands.rename(user="пользователь", lvl="уровень")
@app_commands.describe(user="Пользователь", lvl="Уровень: low, mid или pusy")
async def mod(ctx, user: str = None, lvl: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 3:
        return await reply_err(ctx, "Недостаточно прав для управления доступами.")
    if not user or not lvl: return await reply_err(ctx, "Укажите пользователя и уровень (low/mid/pusy)")
    user_id, _ = await resolve_user(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    user_mention = f"<@{user_id}>"

    target_lvl = get_lvl(ctx.guild.id, user_id)
    if target_lvl >= 3 and not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Нельзя менять права pusy")

    lvl = lvl.lower()
    if lvl not in ["low", "mid", "pusy"]: 
        return await reply_err(ctx, "Такого уровня нет")

    if not is_trusted_user(ctx.author.id) and lvl != "low":
        return await reply_err(ctx, "Вы можете выдать только 'low'")

    g_access = get_g_dict(USER_ACCESS, ctx.guild.id)
    g_access[str(user_id)] = lvl
    await reply_ok(ctx, f"{user_mention} выдан доступ **{lvl}**")
    await send_log(ctx.guild, "Mod", f"{ctx.author.mention} выдал доступ **{lvl}** для {user_mention}.")

@bot.hybrid_command(name="unmod", description="Снять уровень доступа с пользователя")
@app_commands.rename(user="пользователь")
@app_commands.describe(user="Пользователь")
async def unmod(ctx, user: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 3:
        return await reply_err(ctx, "Недостаточно прав для управления доступами.")
    if not user: return await reply_err(ctx, "Укажите пользователя")
    user_id, _ = await resolve_user(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    user_mention = f"<@{user_id}>"

    target_lvl = get_lvl(ctx.guild.id, user_id)
    if target_lvl >= 3 and not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Нельзя снять доступ у pusy")

    g_access = get_g_dict(USER_ACCESS, ctx.guild.id)
    if str(user_id) in g_access:
        del g_access[str(user_id)]
        await reply_ok(ctx, f"доступ аннулирован для {user_mention}")
        await send_log(ctx.guild, "Unmod", f"{ctx.author.mention} снял доступ у {user_mention}.")
    else:
        await reply_err(ctx, "У пользователя нет доступа")

@bot.hybrid_command(name="wl_add", description="Добавить пользователя в whitelist")
@app_commands.rename(user="пользователь")
@app_commands.describe(user="Пользователь")
async def wl_add(ctx, user: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 3:
        return await reply_err(ctx, "Недостаточно прав для whitelist.")
    if not user: return await reply_err(ctx, "Укажите пользователя")
    user_id, _ = await resolve_user(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    user_mention = f"<@{user_id}>"

    wl = get_g_list(WHITELIST_IDS, ctx.guild.id)
    if user_id not in wl:
        wl.append(user_id)
        await reply_ok(ctx, f"{user_mention} добавлен в вайтлист")
        await send_log(ctx.guild, "Вайтлист", f"{ctx.author.mention} добавил {user_mention} в вайтлист.")

@bot.hybrid_command(name="wl_remove", description="Удалить пользователя из whitelist")
@app_commands.rename(user="пользователь")
@app_commands.describe(user="Пользователь")
async def wl_remove(ctx, user: str = None):
    if get_lvl(ctx.guild.id, ctx.author.id) < 3:
        return await reply_err(ctx, "Недостаточно прав для whitelist.")
    if not user: return await reply_err(ctx, "Укажите пользователя")
    user_id, _ = await resolve_user(ctx, user)
    if not user_id: return await reply_err(ctx, "Неверный ID.")
    user_mention = f"<@{user_id}>"

    wl = get_g_list(WHITELIST_IDS, ctx.guild.id)
    if user_id in wl:
        wl.remove(user_id)
        await reply_ok(ctx, f"{user_mention} удален из вайтлиста")
        await send_log(ctx.guild, "Вайтлист", f"{ctx.author.mention} удалил {user_mention} из вайтлиста.")

@bot.command(name="say")
async def say(ctx, *, text: str = None):
    if not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Команда доступна только доверенным пользователям.")
    if not text:
        return await reply_err(ctx, "Укажите текст сообщения.")
    try: 
        await ctx.message.delete()
    except: 
        pass
    await ctx.send(text)

# ==========================================
# ИДЕАЛЬНЫЙ БЭКАП: КОПИЯ 1:1 
# ==========================================
def get_overwrites(obj):
    ovrs = {}
    for target, overwrite in obj.overwrites.items():
        if isinstance(target, discord.Role) and not target.managed:
            t_type = "everyone" if target.is_default() else "role"
            ovrs[str(target.id)] = {
                "type": t_type,
                "allow": overwrite.pair()[0].value,
                "deny": overwrite.pair()[1].value
            }
    return ovrs

@bot.hybrid_command(name="save_server", description="Сохранить бэкап структуры сервера")
@app_commands.rename(name="название")
@app_commands.describe(name="Название бэкапа")
async def save_server(ctx, name: str = "default"):
    if not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Команда доступна только доверенным пользователям.")

    msg = await reply_info(ctx, f"Создаю бэкап **{name}**...")

    backup_data = {
        "server_name": ctx.guild.name,
        "server_icon": str(ctx.guild.icon.url) if ctx.guild.icon else None,
        "roles": [], 
        "categories": [], 
        "text_channels": [], 
        "voice_channels": []
    }

    for r in sorted(ctx.guild.roles, key=lambda x: x.position, reverse=True):
        if not r.managed and r.id != ctx.guild.default_role.id:
            backup_data["roles"].append({
                "old_id": str(r.id),
                "name": r.name, 
                "color": r.color.value, 
                "hoist": r.hoist, 
                "mentionable": r.mentionable, 
                "permissions": r.permissions.value,
                "pos": r.position
            })
            
    for c in sorted(ctx.guild.categories, key=lambda x: x.position): 
        backup_data["categories"].append({
            "old_id": str(c.id), 
            "name": c.name, 
            "pos": c.position,
            "overwrites": get_overwrites(c)
        })
        
    for c in sorted(ctx.guild.text_channels, key=lambda x: x.position): 
        backup_data["text_channels"].append({
            "name": c.name, 
            "cat_id": str(c.category.id) if c.category else None, 
            "pos": c.position,
            "topic": c.topic,
            "nsfw": c.is_nsfw(),
            "slowmode": c.slowmode_delay,
            "overwrites": get_overwrites(c)
        })
        
    for c in sorted(ctx.guild.voice_channels, key=lambda x: x.position): 
        backup_data["voice_channels"].append({
            "name": c.name, 
            "cat_id": str(c.category.id) if c.category else None, 
            "pos": c.position,
            "bitrate": c.bitrate,
            "user_limit": c.user_limit,
            "overwrites": get_overwrites(c)
        })
    
    # Сохраняем бэкап в JSON файл
    if save_backup(f"backup_{name}.json", backup_data):
        await msg.edit(content=f"> **( + )** Бэкап **{name}** сохранен")
    else:
        await msg.edit(content=f"> **( - )** Ошибка сохранения бэкапа **{name}**")

@bot.hybrid_command(name="list_backups", description="Показать сохраненные бэкапы")
async def list_backups(ctx):
    """Показать список всех бэкапов"""
    if not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Команда доступна только доверенным пользователям.")
    
    import glob
    backups = glob.glob("backup_*.json")
    
    if not backups:
        return await reply_err(ctx, "Нет сохраненных бэкапов")
    
    backup_names = [b.replace("backup_", "").replace(".json", "") for b in backups]
    backup_list = "\n".join([f"• {name}" for name in backup_names])
    
    emb = discord.Embed(title="📦 Список бэкапов", description=backup_list, color=UI_COLOR)
    emb.set_footer(text=f"Всего: {len(backups)} | Использование: /load_server <название>")
    await send_ctx(ctx, embed=emb, ephemeral=True)

@bot.hybrid_command(name="load_server", description="Восстановить сервер из бэкапа")
@app_commands.rename(name="название")
@app_commands.describe(name="Название бэкапа")
async def load_server(ctx, name: str = "default"):
    if not is_trusted_user(ctx.author.id):
        return await reply_err(ctx, "Команда доступна только доверенным пользователям.")

    backup = load_data(f"backup_{name}.json", None)
    if not backup: return await reply_err(ctx, f"Бэкап '{name}' не найден")

    msg = await reply_info(ctx, f"Восстанавливаю **{name}**...")

    server_name = backup.get("server_name", ctx.guild.name)
    server_icon_url = backup.get("server_icon")
    icon_bytes = None

    if server_icon_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(server_icon_url) as resp:
                    if resp.status == 200: 
                        icon_bytes = await resp.read()
        except: 
            pass

    try:
        edit_kwargs = {"name": server_name}
        if icon_bytes: edit_kwargs["icon"] = icon_bytes
        await ctx.guild.edit(**edit_kwargs)
        await asyncio.sleep(1)
    except: 
        pass

    for channel in ctx.guild.channels:
        if channel.id != ctx.channel.id:
            try: 
                await channel.delete(reason="Backup: подготовка")
                await asyncio.sleep(0.1)
            except: 
                pass

    for role in ctx.guild.roles:
        if not role.managed and role.id != ctx.guild.default_role.id and role < ctx.guild.me.top_role:
            try: 
                await role.delete(reason="Backup: подготовка")
                await asyncio.sleep(0.1)
            except: 
                pass

    await msg.edit(content="> **( ⏳ )** Воссоздаю роли...")

    role_map = {}       
    category_map = {}   

    def build_overwrites(saved_ovrs):
        new_ovrs = {}
        for t_id_str, data in saved_ovrs.items():
            if data["type"] == "everyone":
                target = ctx.guild.default_role
            else:
                target = role_map.get(t_id_str)
            
            if target:
                new_ovrs[target] = discord.PermissionOverwrite.from_pair(
                    discord.Permissions(data["allow"]),
                    discord.Permissions(data["deny"])
                )
        return new_ovrs

    for r_data in backup.get("roles", []): 
        try:
            new_role = await ctx.guild.create_role(
                name=r_data["name"],
                color=discord.Color(r_data["color"]),
                hoist=r_data["hoist"],
                mentionable=r_data["mentionable"],
                permissions=discord.Permissions(r_data["permissions"]),
                reason="Backup: восстановление"
            )
            role_map[r_data["old_id"]] = new_role
            await asyncio.sleep(0.2)
        except Exception as e: 
            pass

    await msg.edit(content="> **( ⏳ )** Воссоздаю каналы...")

    for cat_data in backup.get("categories", []):
        try: 
            new_cat = await ctx.guild.create_category(
                name=cat_data["name"],
                overwrites=build_overwrites(cat_data.get("overwrites", {})),
                reason="Backup: восстановление"
            )
            category_map[cat_data["old_id"]] = new_cat
            await asyncio.sleep(0.2)
        except Exception as e: 
            pass

    for tc in backup.get("text_channels", []):
        try:
            target_category = category_map.get(tc.get("cat_id")) if tc.get("cat_id") else None
            await ctx.guild.create_text_channel(
                name=tc["name"], 
                category=target_category, 
                topic=tc.get("topic"), 
                nsfw=tc.get("nsfw", False), 
                slowmode_delay=tc.get("slowmode", 0),
                overwrites=build_overwrites(tc.get("overwrites", {})),
                reason="Backup: восстановление"
            )
            await asyncio.sleep(0.2)
        except: 
            pass
        
    for vc in backup.get("voice_channels", []):
        try:
            target_category = category_map.get(vc.get("cat_id")) if vc.get("cat_id") else None
            await ctx.guild.create_voice_channel(
                name=vc["name"], 
                category=target_category, 
                bitrate=vc.get("bitrate", 64000), 
                user_limit=vc.get("user_limit", 0),
                overwrites=build_overwrites(vc.get("overwrites", {})),
                reason="Backup: восстановление"
            )
            await asyncio.sleep(0.2)
        except: 
            pass

    await msg.edit(content=f"> **( + )** Сервер **{server_name}** восстановлен")

@bot.event
async def on_ready():
    global COMMANDS_SYNCED
    if not check_unbans.is_running(): 
        check_unbans.start()
    if not COMMANDS_SYNCED:
        try:
            removed_global, synced_guilds = await sync_application_commands()
            COMMANDS_SYNCED = True
            print(f"Синхронизировано slash-команд: removed_global={removed_global}, guild={synced_guilds}")
        except Exception as e:
            print(f"Ошибка синхронизации slash-команд: {e}")
    print(f"Бот запущен как {bot.user}")
    print(f"📊 Подключен к {len(bot.guilds)} серверам")

# ==========================================
# --- FLASK ВЕБ-СЕРВЕР ---
# ==========================================
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "luxygluzz228")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error="Неверный пароль")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

def parse_log_date(date_value, *, end_of_day=False):
    if not date_value:
        return None
    try:
        parsed = datetime.datetime.strptime(date_value, "%Y-%m-%d")
        if end_of_day:
            parsed += datetime.timedelta(days=1)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def get_log_request_filters():
    return {
        "search": (request.args.get("search") or "").strip() or None,
        "category": (request.args.get("category") or "").strip() or None,
        "level": (request.args.get("level") or "").strip() or None,
        "source": (request.args.get("source") or "").strip() or None,
        "sort": (request.args.get("sort") or "desc").strip().lower(),
        "limit": request.args.get("limit", 200),
        "date_from": parse_log_date(request.args.get("date_from")),
        "date_to": parse_log_date(request.args.get("date_to"), end_of_day=True)
    }

def format_panel_log_entry(entry):
    created_at = entry.get("created_at")
    created_at_display = created_at
    try:
        dt = datetime.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        created_at_display = dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        pass

    return {
        **entry,
        "created_at_display": created_at_display
    }

@app.route('/api/guilds')
@login_required
def api_guilds():
    guilds = []
    for guild in bot.guilds:
        icon_url = None
        if guild.icon:
            icon_url = f"https://cdn.discordapp.com/icons/{guild.id}/{guild.icon}.png"
        guilds.append({
            'id': str(guild.id),
            'name': guild.name,
            'icon': icon_url,
            'member_count': guild.member_count,
            'owner_id': str(guild.owner_id)
        })
    return jsonify(guilds)

@app.route('/api/guild/<guild_id>/details')
@login_required
def api_guild_details(guild_id):
    """Получить детальную информацию о сервере"""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({'error': 'Guild not found'}), 404
    
    # Подсчет онлайн участников
    online_count = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)
    
    # Получение всех ролей с цветами
    roles = []
    for role in sorted(guild.roles, key=lambda x: x.position, reverse=True):
        if not role.is_default():
            roles.append({
                'id': str(role.id),
                'name': role.name,
                'color': f'#{role.color.value:06x}' if role.color.value != 0 else '#99aab5',
                'position': role.position,
                'members': len(role.members)
            })
    
    icon_url = None
    if guild.icon:
        icon_url = f"https://cdn.discordapp.com/icons/{guild.id}/{guild.icon}.png"
    
    banner_url = None
    if guild.banner:
        banner_url = f"https://cdn.discordapp.com/banners/{guild.id}/{guild.banner}.png"
    
    return jsonify({
        'id': str(guild.id),
        'name': guild.name,
        'icon': icon_url,
        'banner': banner_url,
        'member_count': guild.member_count,
        'online_count': online_count,
        'bot_count': sum(1 for m in guild.members if m.bot),
        'text_channels': len(guild.text_channels),
        'voice_channels': len(guild.voice_channels),
        'roles': roles,
        'created_at': guild.created_at.strftime('%d.%m.%Y'),
        'owner_id': str(guild.owner_id),
        'boost_level': guild.premium_tier,
        'boost_count': guild.premium_subscription_count
    })

@app.route('/api/access/<guild_id>')
@login_required
def api_access(guild_id):
    guild_access = get_g_dict(USER_ACCESS, int(guild_id))
    result = []
    for user_id, level in guild_access.items():
        user = bot.get_user(int(user_id))
        avatar_url = None
        if user and user.avatar:
            if isinstance(user.avatar, str):
                avatar_url = f"https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png"
            else:
                avatar_url = user.avatar.url
        result.append({
            'user_id': user_id,
            'level': level,
            'username': user.name if user else f'User {user_id}',
            'avatar': avatar_url
        })
    return jsonify(result)

@app.route('/api/whitelist/<guild_id>')
@login_required
def api_whitelist(guild_id):
    guild_wl = get_g_list(WHITELIST_IDS, int(guild_id))
    result = []
    for user_id in guild_wl:
        user = bot.get_user(int(user_id))
        avatar_url = None
        if user and user.avatar:
            if isinstance(user.avatar, str):
                avatar_url = f"https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png"
            else:
                avatar_url = user.avatar.url
        result.append({
            'user_id': str(user_id),
            'username': user.name if user else f'User {user_id}',
            'avatar': avatar_url
        })
    return jsonify(result)

@app.route('/api/blacklist/<guild_id>')
@login_required
def api_blacklist(guild_id):
    guild_bl = get_g_list(BLACKLIST_IDS, int(guild_id))
    result = []
    for user_id in guild_bl:
        user = bot.get_user(int(user_id))
        avatar_url = None
        if user and user.avatar:
            if isinstance(user.avatar, str):
                avatar_url = f"https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png"
            else:
                avatar_url = user.avatar.url
        result.append({
            'user_id': str(user_id),
            'username': user.name if user else f'User {user_id}',
            'avatar': avatar_url
        })
    return jsonify(result)

@app.route('/api/protected/<guild_id>')
@login_required
def api_protected(guild_id):
    guild_prot = get_g_list(PROTECTED_USERS, int(guild_id))
    result = []
    for user_id in guild_prot:
        user = bot.get_user(int(user_id))
        avatar_url = None
        if user and user.avatar:
            if isinstance(user.avatar, str):
                avatar_url = f"https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png"
            else:
                avatar_url = user.avatar.url
        result.append({
            'user_id': str(user_id),
            'username': user.name if user else f'User {user_id}',
            'avatar': avatar_url
        })
    return jsonify(result)

@app.route('/api/bans/<guild_id>')
@login_required
def api_bans(guild_id):
    guild_bans = get_g_dict(TEMP_BANS, int(guild_id))
    guild_abans = get_g_dict(A_TEMP_BANS, int(guild_id))
    result = []
    
    for user_id, expire in guild_bans.items():
        user = bot.get_user(int(user_id))
        avatar_url = None
        if user and user.avatar:
            if isinstance(user.avatar, str):
                avatar_url = f"https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png"
            else:
                avatar_url = user.avatar.url
        result.append({
            'user_id': user_id,
            'username': user.name if user else f'User {user_id}',
            'avatar': avatar_url,
            'expire': expire,
            'type': 'pusy',
            'issued_by': 'Модератор',
            'issued_at': 'N/A'
        })
    
    for user_id, expire in guild_abans.items():
        user = bot.get_user(int(user_id))
        avatar_url = None
        if user and user.avatar:
            if isinstance(user.avatar, str):
                avatar_url = f"https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png"
            else:
                avatar_url = user.avatar.url
        result.append({
            'user_id': user_id,
            'username': user.name if user else f'User {user_id}',
            'avatar': avatar_url,
            'expire': expire,
            'type': 'apusy',
            'issued_by': 'Администратор',
            'issued_at': 'N/A'
        })
    
    return jsonify(result)

@app.route('/api/logs/<guild_id>')
@login_required
def api_logs(guild_id):
    try:
        filters = get_log_request_filters()
        logs = get_panel_logs(
            guild_id,
            search=filters["search"],
            category=filters["category"],
            level=filters["level"],
            source=filters["source"],
            date_from=filters["date_from"],
            date_to=filters["date_to"],
            sort=filters["sort"],
            limit=filters["limit"]
        )
        summary = get_panel_log_summary(
            guild_id,
            search=filters["search"],
            category=filters["category"],
            level=filters["level"],
            source=filters["source"],
            date_from=filters["date_from"],
            date_to=filters["date_to"]
        )
        if summary.get("last_entry"):
            summary["last_entry"] = format_panel_log_entry(summary["last_entry"])
        filter_values = get_panel_log_filters(guild_id)

        return jsonify({
            "logs": [format_panel_log_entry(entry) for entry in logs],
            "summary": summary,
            "filters": filter_values
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/members/<guild_id>')
@login_required
def api_members(guild_id):
    """Получить всех участников сервера с полной информацией"""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify([])
    
    guild_access = get_g_dict(USER_ACCESS, int(guild_id))
    guild_wl = get_g_list(WHITELIST_IDS, int(guild_id))
    guild_bl = get_g_list(BLACKLIST_IDS, int(guild_id))
    guild_prot = get_g_list(PROTECTED_USERS, int(guild_id))
    guild_bans = get_g_dict(TEMP_BANS, int(guild_id))
    guild_abans = get_g_dict(A_TEMP_BANS, int(guild_id))
    
    result = []
    for member in guild.members:
        if member.bot:
            continue
            
        avatar_url = None
        if member.avatar:
            # Проверяем тип - может быть строка или объект Asset
            if isinstance(member.avatar, str):
                avatar_url = f"https://cdn.discordapp.com/avatars/{member.id}/{member.avatar}.png"
            else:
                avatar_url = member.avatar.url
        
        # Получаем роли с цветами
        roles = []
        for r in sorted(member.roles, key=lambda x: x.position, reverse=True):
            if not r.is_default():
                roles.append({
                    'id': str(r.id),
                    'name': r.name,
                    'color': f'#{r.color.value:06x}' if r.color.value != 0 else '#99aab5'
                })
        
        # Проверяем статусы
        access_level = guild_access.get(str(member.id), None)
        is_whitelist = member.id in guild_wl
        is_blacklist = member.id in guild_bl
        is_protected = member.id in guild_prot
        
        ban_info = None
        if str(member.id) in guild_bans:
            ban_info = {'type': 'pusy', 'expire': guild_bans[str(member.id)]}
        elif str(member.id) in guild_abans:
            ban_info = {'type': 'apusy', 'expire': guild_abans[str(member.id)]}
        
        result.append({
            'user_id': str(member.id),
            'username': member.name,
            'display_name': member.display_name,
            'avatar': avatar_url,
            'roles': roles,
            'access_level': access_level,
            'is_whitelist': is_whitelist,
            'is_blacklist': is_blacklist,
            'is_protected': is_protected,
            'ban_info': ban_info,
            'joined_at': member.joined_at.strftime('%d.%m.%Y') if member.joined_at else 'N/A'
        })
    
    return jsonify(result)

@app.route('/api/action', methods=['POST'])
@login_required
def api_action():
    """Выполнить действие (бан, выдача доступа и т.д.)"""
    data = request.json
    action = data.get('action')
    guild_id = int(data.get('guild_id'))
    user_id = data.get('user_id')
    user_ref = f"<@{user_id}>" if user_id else "неизвестный пользователь"
    
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify({'success': False, 'error': 'Сервер не найден'})
    
    try:
        if action == 'add_access':
            level = data.get('level')
            g_access = get_g_dict(USER_ACCESS, guild_id)
            g_access[str(user_id)] = level
            save_web_log(guild_id, "Panel Access", f"Web panel выдал доступ **{level}** пользователю {user_ref}.", level="info", category="access")
            return jsonify({'success': True, 'message': f'Доступ {level} выдан'})
        
        elif action == 'remove_access':
            g_access = get_g_dict(USER_ACCESS, guild_id)
            if str(user_id) in g_access:
                del g_access[str(user_id)]
                save_web_log(guild_id, "Panel Access", f"Web panel снял доступ у {user_ref}.", level="success", category="access")
            return jsonify({'success': True, 'message': 'Доступ удален'})
        
        elif action == 'add_whitelist':
            wl = get_g_list(WHITELIST_IDS, guild_id)
            if int(user_id) not in wl:
                wl.append(int(user_id))
                save_web_log(guild_id, "Panel Whitelist", f"Web panel добавил {user_ref} в WhiteList.", level="info", category="access")
            return jsonify({'success': True, 'message': 'Добавлен в вайтлист'})
        
        elif action == 'remove_whitelist':
            wl = get_g_list(WHITELIST_IDS, guild_id)
            if int(user_id) in wl:
                wl.remove(int(user_id))
                save_web_log(guild_id, "Panel Whitelist", f"Web panel удалил {user_ref} из WhiteList.", level="success", category="access")
            return jsonify({'success': True, 'message': 'Удален из вайтлиста'})
        
        elif action == 'add_blacklist':
            bl = get_g_list(BLACKLIST_IDS, guild_id)
            warning = None
            if int(user_id) not in bl:
                bl.append(int(user_id))
                future = asyncio.run_coroutine_threadsafe(
                    apply_blacklist_restrictions(guild, user_id, reason="Веб-панель: BlackList"),
                    bot.loop
                )
                _, warning = future.result(timeout=10)
                save_web_log(guild_id, "Panel BlackList", f"Web panel добавил {user_ref} в BlackList.", level="danger", category="blacklist")
            message = 'Добавлен в BlackList'
            if warning:
                message += f' ({warning})'
            return jsonify({'success': True, 'message': message})
        
        elif action == 'remove_blacklist':
            bl = get_g_list(BLACKLIST_IDS, guild_id)
            warning = None
            if int(user_id) in bl:
                bl.remove(int(user_id))
                future = asyncio.run_coroutine_threadsafe(
                    restore_blacklist_roles(guild, user_id, reason="Веб-панель: снятие BlackList"),
                    bot.loop
                )
                _, warning = future.result(timeout=10)
                save_web_log(guild_id, "Panel BlackList", f"Web panel удалил {user_ref} из BlackList.", level="success", category="blacklist")
            message = 'Удален из BlackList'
            if warning:
                message += f' ({warning})'
            return jsonify({'success': True, 'message': message})
        
        elif action == 'add_protected':
            prot = get_g_list(PROTECTED_USERS, guild_id)
            if int(user_id) not in prot:
                prot.append(int(user_id))
                save_web_log(guild_id, "Panel Protection", f"Web panel выдал защиту пользователю {user_ref}.", level="info", category="protection")
            return jsonify({'success': True, 'message': 'Добавлен в проту'})
        
        elif action == 'remove_protected':
            prot = get_g_list(PROTECTED_USERS, guild_id)
            if int(user_id) in prot:
                prot.remove(int(user_id))
                save_web_log(guild_id, "Panel Protection", f"Web panel снял защиту с {user_ref}.", level="success", category="protection")
            return jsonify({'success': True, 'message': 'Удален из проты'})

        elif action in ('give_role', 'remove_role'):
            role_id = data.get('role_id')
            if not role_id:
                return jsonify({'success': False, 'error': 'Роль не указана'})
            member = guild.get_member(int(user_id))
            role = guild.get_role(int(role_id))
            if not member:
                return jsonify({'success': False, 'error': 'Пользователь не найден на сервере'})
            if not role:
                return jsonify({'success': False, 'error': 'Роль не найдена'})
            if role >= guild.me.top_role:
                return jsonify({'success': False, 'error': 'Эта роль выше роли бота'})

            if action == 'give_role':
                future = asyncio.run_coroutine_threadsafe(
                    member.add_roles(role, reason="Веб-панель: выдача роли"),
                    bot.loop
                )
                future.result(timeout=10)
                save_web_log(guild_id, "Panel Roles", f"Web panel выдал роль **{role.name}** пользователю {member.mention}.", level="info", category="roles")
                return jsonify({'success': True, 'message': f'Роль {role.name} выдана'})

            future = asyncio.run_coroutine_threadsafe(
                member.remove_roles(role, reason="Веб-панель: снятие роли"),
                bot.loop
            )
            future.result(timeout=10)
            save_web_log(guild_id, "Panel Roles", f"Web panel снял роль **{role.name}** у {member.mention}.", level="success", category="roles")
            return jsonify({'success': True, 'message': f'Роль {role.name} снята'})
        
        elif action == 'ban':
            duration = data.get('duration', '1d')
            ban_type = data.get('ban_type', 'pusy')
            
            match = re.match(r"(\d+)([smhd])", duration.lower())
            if not match:
                return jsonify({'success': False, 'error': 'Неверный формат времени'})
            
            amt = int(match.group(1))
            unit = match.group(2)
            delta = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}[unit]
            end = datetime.datetime.now() + datetime.timedelta(**{delta: amt})
            end_str = end.strftime("%d.%m.%Y %H:%M:%S")
            
            member = guild.get_member(int(user_id))
            if member:
                asyncio.run_coroutine_threadsafe(
                    guild.ban(member, reason=f"Веб-панель: {ban_type} бан"),
                    bot.loop
                )
            
            if ban_type == 'apusy':
                g_bans = get_g_dict(A_TEMP_BANS, guild_id)
                g_bans[str(user_id)] = end_str
            else:
                g_bans = get_g_dict(TEMP_BANS, guild_id)
                g_bans[str(user_id)] = end_str

            save_web_log(
                guild_id,
                "Panel Ban",
                f"Web panel выдал {ban_type} бан пользователю {user_ref} до {end_str}.",
                level="danger",
                category="moderation"
            )
            
            return jsonify({'success': True, 'message': f'Бан выдан до {end_str}'})
        
        elif action == 'unban':
            g_bans = get_g_dict(TEMP_BANS, guild_id)
            g_abans = get_g_dict(A_TEMP_BANS, guild_id)
            
            if str(user_id) in g_bans:
                del g_bans[str(user_id)]
            if str(user_id) in g_abans:
                del g_abans[str(user_id)]
            
            asyncio.run_coroutine_threadsafe(
                guild.unban(discord.Object(id=int(user_id)), reason="Веб-панель: разбан"),
                bot.loop
            )
            save_web_log(guild_id, "Panel Unban", f"Web panel снял бан с пользователя {user_ref}.", level="success", category="moderation")
            return jsonify({'success': True, 'message': 'Бан снят'})
        
        else:
            return jsonify({'success': False, 'error': 'Неизвестное действие'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/autorole/<guild_id>', methods=['GET'])
@login_required
def api_get_autorole(guild_id):
    """Получить настройки автороли для сервера"""
    try:
        auto_roles = get_g_dict(AUTO_ROLES, int(guild_id))
        guild = bot.get_guild(int(guild_id))
        
        result = {
            'enabled': auto_roles.get('enabled', False),
            'role_id': auto_roles.get('role_id', None),
            'role_ids': auto_roles.get('role_ids', []),
            'role_name': None
        }
        
        if result['role_id'] and guild:
            role = guild.get_role(int(result['role_id']))
            if role:
                result['role_name'] = role.name
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/autorole/<guild_id>', methods=['POST'])
@login_required
def api_set_autorole(guild_id):
    """Установить настройки автороли для сервера"""
    try:
        data = request.json
        enabled = data.get('enabled', False)
        role_ids = data.get('role_ids', [])
        if not role_ids and data.get('role_id'):
            role_ids = [data.get('role_id')]
        role_ids = [str(role_id) for role_id in role_ids if role_id]
        
        auto_roles = get_g_dict(AUTO_ROLES, int(guild_id))
        auto_roles['enabled'] = enabled
        auto_roles['role_ids'] = role_ids
        auto_roles['role_id'] = role_ids[0] if role_ids else None
        AUTO_ROLES[int(guild_id)] = auto_roles
        mode_text = "включил" if enabled else "выключил"
        roles_text = ", ".join(f"<@&{role_id}>" for role_id in role_ids) if role_ids else "без ролей"
        save_web_log(
            guild_id,
            "Panel Autorole",
            f"Web panel {mode_text} автороль. Роли: {roles_text}.",
            level="info" if enabled else "success",
            category="roles"
        )
        
        return jsonify({'success': True, 'message': 'Настройки автороли обновлены'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def get_panel_url(port):
    public_url = os.getenv("PUBLIC_PANEL_URL")
    if public_url:
        return public_url.rstrip("/")

    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if railway_domain:
        return f"https://{railway_domain}"

    return f"http://127.0.0.1:{port}"

def run_flask():
    port = int(os.getenv("PORT", 5000))
    try:
        print(f"Web panel listening on {get_panel_url(port)}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"Web panel crashed: {e}")
        os._exit(1)

def start_public_tunnel():
    provider = os.getenv("PUBLIC_TUNNEL_PROVIDER", "").strip().lower()
    if not provider:
        return None

    port = int(os.getenv("PORT", 5000))

    if provider == "pagekite":
        kite_name = os.getenv("PAGEKITE_KITE", "").strip()
        if not kite_name:
            print("PAGEKITE_KITE не задан. Укажите, например, yourname.pagekite.me")
            return None

        pagekite_bin = os.getenv("PAGEKITE_BIN", "pagekite.py")
        cmd = [pagekite_bin, str(port), kite_name]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
        except FileNotFoundError:
            print("pagekite.py не найден. Установите PageKite и добавьте его в PATH.")
            return None
        except Exception as e:
            print(f"Не удалось запустить PageKite: {e}")
            return None

        def read_tunnel_output():
            for line in proc.stdout:
                line = line.strip()
                if line:
                    print(f"pagekite: {line}")

        threading.Thread(target=read_tunnel_output, daemon=True).start()
        return proc

    print(f"Неизвестный PUBLIC_TUNNEL_PROVIDER: {provider}")
    return None

# Запуск Flask в отдельном потоке
flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
public_tunnel_process = start_public_tunnel()


# ==========================================
# --- ЗАПУСК БОТА ---
# ==========================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    print("ОШИБКА: Токен Discord не найден! Установите переменную окружения DISCORD_TOKEN")
    exit(1)

bot.run(DISCORD_TOKEN)
