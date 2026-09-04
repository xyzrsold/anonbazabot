# ========== ИМПОРТЫ ==========
import logging
import sqlite3
import random
import os
import html
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = -1004294922207
GROUP_ID = -1004452867464
OWNER_ID = 5265325991
DB_NAME = "comments_bot.db"
BOT_USERNAME = "anonbazabot"
CHANNEL_USERNAME = "pdbaza"

def escape_html(value) -> str:
    """Безопасно вставляет пользовательский текст в HTML-сообщения Telegram."""
    return html.escape("" if value is None else str(value), quote=True)

# ========== БАЗА ДАННЫХ ==========
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute('ALTER TABLE comments ADD COLUMN comment_link TEXT')
        conn.commit()
        logging.info("✅ Колонка comment_link добавлена")
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE comments ADD COLUMN post_link TEXT')
        conn.commit()
        logging.info("✅ Колонка post_link добавлена")
    except sqlite3.OperationalError:
        pass
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            emoji TEXT NOT NULL,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)', (OWNER_ID, OWNER_ID))
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bans (
            user_id INTEGER PRIMARY KEY,
            ban_until TIMESTAMP,
            banned_by INTEGER,
            reason TEXT,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            parent_comment_id INTEGER,
            author_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            post_link TEXT,
            comment_link TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def save_user_emoji(user_id: int, emoji: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO users (user_id, emoji) VALUES (?, ?)', (user_id, emoji))
    conn.commit()
    conn.close()

def get_user_emoji(user_id: int) -> Optional[str]:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT emoji FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    return result['emoji'] if result else None

def is_admin(user_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    return bool(result)

def add_admin(user_id: int, added_by: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO admins (user_id, added_by) VALUES (?, ?)', (user_id, added_by))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remove_admin(user_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def get_admins() -> List[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    results = cursor.execute('SELECT * FROM admins').fetchall()
    conn.close()
    return [dict(row) for row in results]

def ban_user(user_id: int, ban_until: Optional[datetime] = None, banned_by: int = None, reason: str = None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO bans (user_id, ban_until, banned_by, reason) VALUES (?, ?, ?, ?)',
                   (user_id, ban_until, banned_by, reason))
    conn.commit()
    conn.close()

def unban_user(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM bans WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def is_banned(user_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT ban_until FROM bans WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    
    if not result:
        return False
    
    ban_until = datetime.fromisoformat(result['ban_until']) if result['ban_until'] else None
    
    if ban_until is None:
        return True
    
    if datetime.now() > ban_until:
        unban_user(user_id)
        return False
    
    return True

def get_ban_info(user_id: int) -> Optional[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT * FROM bans WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    return dict(result) if result else None

def save_comment(author_id: int, post_id: str, message_id: int, group_id: int, post_link: str = None, comment_link: str = None, parent_comment_id: int = None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO comments (author_id, post_id, message_id, group_id, post_link, comment_link, parent_comment_id) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (author_id, post_id, message_id, group_id, post_link, comment_link, parent_comment_id))
    comment_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return comment_id

def get_author_by_message_id(message_id: int) -> Optional[int]:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT author_id FROM comments WHERE message_id = ?', (message_id,)).fetchone()
    conn.close()
    return result['author_id'] if result else None

def get_comment_by_id(comment_id: int) -> Optional[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT * FROM comments WHERE id = ?', (comment_id,)).fetchone()
    conn.close()
    return dict(result) if result else None

def get_comment_by_message_id(message_id: int) -> Optional[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT * FROM comments WHERE message_id = ?', (message_id,)).fetchone()
    conn.close()
    return dict(result) if result else None

def get_post_link_by_post_id(post_id: str) -> Optional[str]:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT post_link FROM comments WHERE post_id = ? AND post_link IS NOT NULL LIMIT 1', (post_id,)).fetchone()
    conn.close()
    return result['post_link'] if result else None
    
def get_user_comment_count(user_id: int) -> int:
    conn = get_db()
    cursor = conn.cursor()
    result = cursor.execute('SELECT COUNT(*) FROM comments WHERE author_id = ?', (user_id,)).fetchone()
    conn.close()
    return result[0] if result else 0

# ========== СПИСОК ЭМОДЗИ ==========
EMOJIS = [
    '🐶', '🐱', '🐭', '🐹', '🐰', '🦊', '🐻', '🐼', '🐨', '🐯',
    '🦁', '🐮', '🐷', '🐸', '🐵', '🐔', '🐧', '🐦', '🐤', '🐣',
    '🦆', '🦅', '🦉', '🐺', '🐗', '🐴', '🦄', '🐝', '🐛', '🦋',
    '🐌', '🐞', '🐜', '🪲', '🪳', '🦟', '🦗', '🪰', '🪱', '🐙',
    '🦑', '🦐', '🦞', '🦀', '🐡', '🐠', '🐟', '🐬', '🐳', '🐋',
    '🦈', '🐊', '🐅', '🐆', '🦓', '🦍', '🦧', '🐘', '🦛', '🦏',
    '🐪', '🐫', '🦒', '🐄', '🐃', '🐂', '🐏', '🐑', '🐐', '🦌',
    '🍎', '🍐', '🍊', '🍋', '🍌', '🍉', '🍇', '🍓', '🫐', '🍈',
    '🍒', '🍑', '🥭', '🍍', '🥥', '🥝', '🍅', '🍆', '🥑', '🫑',
    '🌽', '🥕', '🫒', '🧄', '🧅', '🥔', '🍠', '🥐', '🥨', '🥖',
    '🚗', '🚕', '🚙', '🚌', '🚎', '🏎️', '🚓', '🚑', '🚒', '🚐',
    '🛻', '🚚', '🚛', '🚜', '🏍️', '🛵', '🚲', '🛴', '🛹', '🚁',
    '✈️', '🛩️', '🛫', '🛬', '🪂', '💺', '🚀', '🛸', '🛶', '⛵',
    '⚽', '🏀', '🏈', '⚾', '🥎', '🎾', '🏐', '🏉', '🥏', '🎱',
    '🪀', '🏓', '🏸', '🏒', '🏑', '🥍', '🏏', '⛳', '🏹', '🎣'
]

user_states: Dict[int, dict] = {}

def generate_emoji() -> str:
    return ''.join(random.choices(EMOJIS, k=5))

def parse_ban_args(args: list) -> tuple:
    ban_until = None
    reason = None
    
    if not args:
        return ban_until, reason
    
    if args[0].endswith('d') and args[0][:-1].isdigit():
        days = int(args[0][:-1])
        ban_until = datetime.now() + timedelta(days=days)
        reason = ' '.join(args[1:]) if len(args) > 1 else None
    else:
        reason = ' '.join(args)
    
    return ban_until, reason

    # ========== ОТПРАВКА УВЕДОМЛЕНИЙ О БАНЕ/РАЗБАНЕ ==========
async def send_ban_notification(context: ContextTypes.DEFAULT_TYPE, user_id: int, admin_name: str, duration: str, reason: str):
    """Отправляет уведомление пользователю о бане"""
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"⛔ <b>Вас заблокировали!</b>\n\n"
                f"👮 Администратор: {admin_name}\n"
                f"⏳ Срок: {duration}\n"
                f"📝 Причина: {reason}\n\n"
                f"❓ Если вы считаете, что это ошибка, свяжитесь с администрацией канала."
            ),
            parse_mode='HTML'
        )
        logging.info(f"✅ Уведомление о бане отправлено пользователю {user_id}")
    except Exception as e:
        logging.error(f"❌ Не удалось отправить уведомление о бане {user_id}: {e}")

async def send_unban_notification(context: ContextTypes.DEFAULT_TYPE, user_id: int, admin_name: str = None):
    """Отправляет уведомление пользователю о разбане"""
    try:
        if admin_name:
            text = (
                f"✅ <b>Вас разблокировали!</b>\n\n"
                f"👮 Администратор: {admin_name}\n\n"
                f"🎉 Вы снова можете оставлять анонимные комментарии."
            )
        else:
            text = (
                f"✅ <b>Срок вашей блокировки истёк!</b>\n\n"
                f"🎉 Вы снова можете оставлять анонимные комментарии."
            )
        
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode='HTML'
        )
        logging.info(f"✅ Уведомление о разбане отправлено пользователю {user_id}")
    except Exception as e:
        logging.error(f"❌ Не удалось отправить уведомление о разбане {user_id}: {e}")

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Здравствуйте, я бот для анонимных комментариев.\n\n"
        "📌 Чтобы оставить комментарий, нажмите кнопку под постом в канале.\n"
        "🔒 Все комментарии анонимны.\n"
        "🔔 Вы будете получать уведомления, если кто-то ответит на Ваш комментарий."
    )

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Ваш ID: `{update.effective_user.id}`", parse_mode='Markdown')

async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Только владелец бота может добавлять админов.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ℹ️ Используйте: /addadmin <user_id>")
        return
    
    new_admin_id = int(context.args[0])
    if add_admin(new_admin_id, OWNER_ID):
        await update.message.reply_text(f"✅ Пользователь {new_admin_id} добавлен в админы.")
    else:
        await update.message.reply_text("❌ Пользователь уже является админом.")

async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Только владелец бота может удалять админов.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ℹ️ Используйте: /removeadmin <user_id>")
        return
    
    admin_id = int(context.args[0])
    if admin_id == OWNER_ID:
        await update.message.reply_text("⛔ Нельзя удалить владельца.")
        return
    
    if remove_admin(admin_id):
        await update.message.reply_text(f"✅ Админ {admin_id} удалён.")
    else:
        await update.message.reply_text("❌ Пользователь не является админом.")

async def listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет прав.")
        return
    
    admins = get_admins()
    if not admins:
        await update.message.reply_text("ℹ️ Нет назначенных админов.")
        return
    
    text = "👥 Список админов:\n\n"
    for admin in admins:
        text += f"• `{admin['user_id']}` (добавлен: {admin['added_at']})\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ========== КОМАНДА BANANON ==========
async def bananon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды.")
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text("ℹ️ Ответьте на сообщение нарушителя.")
        return
    
    msg_id = update.message.reply_to_message.message_id
    user_id = get_author_by_message_id(msg_id)
    
    if not user_id:
        await update.message.reply_text("❌ Не удалось найти автора.")
        return
    
    ban_until, reason = parse_ban_args(context.args)
    ban_user(user_id, ban_until, update.effective_user.id, reason)
    
    if ban_until is None:
        duration = "навсегда"
    else:
        duration = f"до {ban_until.strftime('%d.%m.%Y %H:%M')}"
    
    admin_name = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name or "Администратор"
    
    await send_ban_notification(context, user_id, admin_name, duration, reason or "не указана")
    
    await update.message.reply_text(
        f"✅ Пользователь забанен {duration}.\n"
        f"📝 Причина: {reason or 'не указана'}\n"
        f"🔔 Уведомление отправлено.",
        parse_mode='Markdown'
    )

# ========== КОМАНДА UNBANANON ==========
async def unbananon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет прав.")
        return
    
    admin_name = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name or "Администратор"
    
    if update.message.reply_to_message:
        msg_id = update.message.reply_to_message.message_id
        user_id = get_author_by_message_id(msg_id)
        
        if not user_id:
            await update.message.reply_text("❌ Не удалось найти автора комментария.")
            return
        
        if not is_banned(user_id):
            await update.message.reply_text("ℹ️ Пользователь не в бане.")
            return
        
        unban_user(user_id)
        await send_unban_notification(context, user_id, admin_name)
        await update.message.reply_text("✅ Пользователь разбанен.\n🔔 Уведомление отправлено.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "ℹ️ Используйте:\n"
            "• /unbananon (ответьте на сообщение нарушителя)\n"
            "• /unbananon <user_id> (если знаете ID)"
        )
        return
    
    user_id = int(context.args[0])
    
    if not is_banned(user_id):
        await update.message.reply_text("ℹ️ Пользователь не в бане.")
        return

    unban_user(user_id)
    await send_unban_notification(context, user_id, admin_name)
    await update.message.reply_text(f"✅ Пользователь {user_id} разбанен.\n🔔 Уведомление отправлено.")
    
# ========== КОМАНДА WHOIS (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА) ==========
async def whois(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "ℹ️ Используйте:\n"
            "• /whois <ссылка_на_комментарий> — по ссылке\n"
            "• /whois <user_id> — по ID\n"
            "• /whois @username — по юзернейму\n\n"
            "Примеры:\n"
            "/whois https://t.me/c/4358843811/114\n"
            "/whois 5265325991\n"
            "/whois @username"
        )
        return
    
    arg = context.args[0]
    author_id = None
    link = None
    search_display = "неизвестно"
    
    match = re.search(r'/c/(\d+)/(\d+)', arg)
    if match:
        group_id = int(match.group(1))
        message_id = int(match.group(2))
        link = arg
        search_display = f"по <a href='{link}'>комментарию</a>"
        
        expected_group_id = str(GROUP_ID)[4:]
        
        if str(group_id) != expected_group_id:
            await update.message.reply_text(
                f"❌ Это сообщение не из вашей группы комментариев.\n"
                f"Ваша группа: {expected_group_id}\n"
                f"Ссылка ведёт в: {group_id}"
            )
            return
        
        comment = get_comment_by_message_id(message_id)
        if not comment:
            await update.message.reply_text("❌ Не удалось найти автора этого комментария.")
            return
        
        author_id = comment['author_id']
    
    elif arg.isdigit():
        author_id = int(arg)
        search_display = f"по ID: <code>{author_id}</code>"
    
    elif arg.startswith('@'):
        try:
            user = await context.bot.get_chat(arg)
            author_id = user.id
            search_display = f"по юзернейму <code>{arg}</code>"
        except Exception as e:
            await update.message.reply_text(
                f"❌ Не удалось найти пользователя с юзернеймом {arg}.\n"
                f"Возможно, бот не в контакте с ним, или юзернейм указан неверно."
            )
            return
    
    else:
        await update.message.reply_text(
            "❌ Неверный формат.\n\n"
            "Используйте:\n"
            "• Ссылку на комментарий: https://t.me/c/4358843811/114\n"
            "• ID пользователя: 5265325991\n"
            "• Юзернейм: @username"
        )
        return
    
    try:
        user = await context.bot.get_chat(author_id)
        
        username = f"@{user.username}" if user.username else "Не указан"
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Не указано"
        comment_count = get_user_comment_count(author_id)
        ban_info = get_ban_info(author_id)
        
        if ban_info:
            ban_until = ban_info.get('ban_until')
            banned_by = ban_info.get('banned_by')
            reason = ban_info.get('reason') or 'Не указана'
            
            if ban_until is None:
                ban_status = "Забанен навсегда"
            else:
                ban_until_dt = datetime.fromisoformat(ban_until)
                remaining = ban_until_dt - datetime.now()
                days = remaining.days
                hours = remaining.seconds // 3600
                if days > 0:
                    ban_status = f"Забанен до {ban_until_dt.strftime('%d.%m.%Y %H:%M')} (осталось {days} дн. {hours} ч.)"
                else:
                    ban_status = f"Забанен до {ban_until_dt.strftime('%d.%m.%Y %H:%M')} (осталось {hours} ч.)"
            
            if banned_by:
                try:
                    admin = await context.bot.get_chat(banned_by)
                    admin_name = f"@{admin.username}" if admin.username else admin.first_name or str(banned_by)
                except:
                    admin_name = str(banned_by)
            else:
                admin_name = "Неизвестен"
            
            await update.message.reply_text(
                f"🔍 <b>Информация о пользователе</b>\n\n"
                f"🆔 ID: <code>{author_id}</code>\n"
                f"👤 Имя: {full_name}\n"
                f"📛 Юзернейм: {username}\n"
                f"💬 Комментариев: {comment_count}\n"
                f"\n"
                f"🚫 Статус бана:\n"
                f"{ban_status}\n"
                f"👮 Забанил: {admin_name}\n"
                f"📝 Причина: {reason}\n"
                f"\n"
                f"🔎 Найден {search_display}",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"🔍 <b>Информация о пользователе</b>\n\n"
                f"🆔 ID: <code>{author_id}</code>\n"
                f"👤 Имя: {full_name}\n"
                f"📛 Юзернейм: {username}\n"
                f"💬 Комментариев: {comment_count}\n"
                f"\n"
                f"✅ Статус: Не в бане\n"
                f"\n"
                f"🔎 Найден {search_display}",
                parse_mode='HTML'
            )
    except Exception as e:
        comment_count = get_user_comment_count(author_id)
        ban_info = get_ban_info(author_id)
        
        if ban_info:
            ban_until = ban_info.get('ban_until')
            banned_by = ban_info.get('banned_by')
            reason = ban_info.get('reason') or 'Не указана'
              
            if ban_until is None:
                ban_status = "Забанен навсегда"
            else:
                ban_until_dt = datetime.fromisoformat(ban_until)
                ban_status = f"Забанен до {ban_until_dt.strftime('%d.%m.%Y %H:%M')}"
            
            await update.message.reply_text(
                f"🔍 <b>Информация о пользователе</b>\n\n"
                f"🆔 ID: <code>{author_id}</code>\n"
                f"💬 Комментариев: {comment_count}\n"
                f"\n"
                f"🚫 {ban_status}\n"
                f"👮 Забанил: {banned_by or 'Неизвестен'}\n"
                f"📝 Причина: {reason}\n"
                f"\n"
                f"🔎 Найден {search_display}\n\n"
                f"⚠️ Не удалось получить данные пользователя.",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"🔍 <b>Информация о пользователе</b>\n\n"
                f"🆔 ID: <code>{author_id}</code>\n"
                f"💬 Комментариев: {comment_count}\n"
                f"\n"
                f"✅ Статус: Не в бане\n"
                f"\n"
                f"🔎 Найден {search_display}\n\n"
                f"⚠️ Не удалось получить данные пользователя.",
                parse_mode='HTML'
            )
            
# ========== ОТПРАВКА УВЕДОМЛЕНИЯ ОТВЕТА ==========
async def send_notification(context: ContextTypes.DEFAULT_TYPE, user_id: int, comment_link: str, reply_text: str, reply_emoji: str):
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"🔔 <b>Кто-то ответил на Ваш комментарий!</b>\n\n"
                f"📝 <b>Ответ:</b>\n<blockquote>{reply_text}</blockquote>\n\n"
                f"──────\n"
                f"<blockquote>Автор: {reply_emoji}</blockquote>\n\n"
                f"📎 <a href='{comment_link}'>Посмотреть комментарий</a>"
            ),
            parse_mode='HTML',
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"❌ Не удалось отправить уведомление {user_id}: {e}")

# ========== АВТОМАТИЧЕСКИЙ РАЗБАН ==========
async def check_expired_bans(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет и разбанивает пользователей с истёкшим сроком бана"""
    conn = get_db()
    cursor = conn.cursor()
    
    now = datetime.now()
    cursor.execute('''
        SELECT user_id FROM bans 
        WHERE ban_until IS NOT NULL AND ban_until <= ?
    ''', (now.isoformat(),))
    
    expired_users = cursor.fetchall()
    conn.close()
    
    for row in expired_users:
        user_id = row['user_id']
        unban_user(user_id)
        logging.info(f"✅ Автоматический разбан пользователя {user_id} (срок истёк).")
        await send_unban_notification(context, user_id)

# ========== ОБРАБОТЧИК КНОПОК ==========
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if is_banned(user_id):
        await query.edit_message_text("🚫 Вы забанены.")
        return
    
    data = query.data
    
    if data.startswith("write_comment"):
        post_id = data.split("|")[1]
        user_states[user_id] = {"action": "new_comment", "post_id": post_id}
        
        post_link = get_post_link_by_post_id(post_id)
        if not post_link:
            post_link = f"https://t.me/{CHANNEL_USERNAME}/{post_id}"
        
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"📝 Напишите свой анонимный комментарий\n\n"
                f"Я опубликую его под [этим постом]({post_link})."
            ),
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    
    elif data.startswith("reply_comment"):
        parts = data.split("|")
        comment_id = int(parts[1])
        post_id = parts[2]
        user_states[user_id] = {"action": "reply", "parent_id": comment_id, "post_id": post_id}
        
        comment = get_comment_by_id(comment_id) if comment_id != 0 else None
        
        if comment and comment.get('comment_link'):
            comment_link = comment['comment_link']
        else:
            comment_link = get_post_link_by_post_id(post_id)
            if not comment_link:
                comment_link = f"https://t.me/{CHANNEL_USERNAME}/{post_id}"
        
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"📝 Напишите свой ответ\n\n"
                f"Я опубликую его под [этим комментарием]({comment_link})."
            ),
            parse_mode='Markdown',
            disable_web_page_preview=True
        )

# ========== ОБРАБОТЧИК ЛИЧНЫХ СООБЩЕНИЙ ==========
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if is_banned(user_id):
        await update.message.reply_text("🚫 Вы забанены.")
        return
    
    if user_id not in user_states:
        await update.message.reply_text("❌ Нажмите кнопку под постом.")
        return
    
    state = user_states.pop(user_id)
    text = update.message.text
    
    emoji = get_user_emoji(user_id)
    if not emoji:
        emoji = generate_emoji()
        save_user_emoji(user_id, emoji)
    
    try:
        if state["action"] == "new_comment":
            post_id = state["post_id"]
            
            post_link = get_post_link_by_post_id(post_id)
            
            formatted_text = (
                f"<blockquote>{text}</blockquote>\n\n"
                f"──────\n"
                f"<blockquote>Автор: {emoji}</blockquote>"
            )
            
            sent_msg = await context.bot.send_message(
                chat_id=GROUP_ID,
                text=formatted_text,
                parse_mode='HTML',
                reply_to_message_id=int(post_id)
            )
            
            comment_link = f"https://t.me/c/{str(GROUP_ID)[4:]}/{sent_msg.message_id}"
            comment_id = save_comment(user_id, post_id, sent_msg.message_id, GROUP_ID, post_link, comment_link)
            
            keyboard = [[
                InlineKeyboardButton(
                    "💬 Ответить", 
                    url=f"https://t.me/{BOT_USERNAME}?start=reply_{comment_id}_{post_id}"
                )
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.edit_message_reply_markup(
                chat_id=GROUP_ID,
                message_id=sent_msg.message_id,
                reply_markup=reply_markup
            )
            
            await update.message.reply_text("✅ Ваш комментарий опубликован!")
        
        elif state["action"] == "reply":
            parent_id = state["parent_id"]
            post_id = state["post_id"]
            
            parent_comment = get_comment_by_id(parent_id) if parent_id != 0 else None
            
            post_link = get_post_link_by_post_id(post_id)
            
            formatted_text = (
                f"<blockquote>{text}</blockquote>\n\n"
                f"──────\n"
                f"<blockquote>Автор: {emoji}</blockquote>"
            )
            
            if parent_comment:
                reply_to_msg_id = parent_comment['message_id']
                logging.info(f"✅ Ответ на комментарий (ID в БД: {parent_id}, message_id: {reply_to_msg_id})")
            else:
                reply_to_msg_id = int(post_id)
                logging.info(f"✅ Ответ на пост (post_id: {reply_to_msg_id})")
            
            sent_msg = await context.bot.send_message(
                chat_id=GROUP_ID,
                text=formatted_text,
                parse_mode='HTML',
                reply_to_message_id=reply_to_msg_id
            )
            
            comment_link = f"https://t.me/c/{str(GROUP_ID)[4:]}/{sent_msg.message_id}"
            comment_id = save_comment(user_id, post_id, sent_msg.message_id, GROUP_ID, post_link, comment_link, parent_id)
            
            keyboard = [[
                InlineKeyboardButton(
                    "💬 Ответить", 
                    url=f"https://t.me/{BOT_USERNAME}?start=reply_{comment_id}_{post_id}"
                )
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.edit_message_reply_markup(
                chat_id=GROUP_ID,
                message_id=sent_msg.message_id,
                reply_markup=reply_markup
            )
            
            if parent_comment:
                await send_notification(
                    context,
                    parent_comment['author_id'],
                    comment_link,
                    text,
                    emoji
                )
            
            await update.message.reply_text("✅ Ваш ответ опубликован!")
    
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка. Попробуйте позже.")

# ========== ОБРАБОТЧИК СООБЩЕНИЙ В ГРУППЕ ==========
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != GROUP_ID:
        return
    
    if not update.message.sender_chat:
        return
    
    if update.message.sender_chat.id != CHANNEL_ID:
        return
    
    post_id = str(update.message.message_id)
    post_link = update.message.link
    
    keyboard = [[
        InlineKeyboardButton(
            "✉️ Написать комментарий", 
            url=f"https://t.me/{BOT_USERNAME}?start=comment_{post_id}"
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📍 Чтобы оставить анонимный комментарий к этому посту, нажмите на кнопку."
    
    try:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown',
            reply_to_message_id=update.message.message_id
        )
        
        save_comment(0, post_id, 0, GROUP_ID, post_link)
        logging.info(f"✅ Ответ на пост {post_id} отправлен. Ссылка: {post_link}.")
    except Exception as e:
        logging.error(f"❌ Ошибка: {e}")

# ========== ОБРАБОТЧИК DEEP LINK ==========
async def handle_deep_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text and update.message.text.startswith('/start'):
        args = update.message.text.split()
        if len(args) > 1:
            param = args[1]
            user_id = update.effective_user.id
            
            if is_banned(user_id):
                await update.message.reply_text("🚫 Вы забанены.")
                return
            
            if param.startswith('comment_'):
                post_id = param.replace('comment_', '')
                user_states[user_id] = {"action": "new_comment", "post_id": post_id}
                
                post_link = get_post_link_by_post_id(post_id)
                if not post_link:
                    post_link = f"https://t.me/{CHANNEL_USERNAME}/{post_id}"
                
                await update.message.reply_text(
                    f"📝 Напишите свой анонимный комментарий.\n\n"
                    f"Я опубликую его под [этим постом]({post_link}).",
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
            
            elif param.startswith('reply_'):
                parts = param.replace('reply_', '').split('_')
                if len(parts) == 2:
                    comment_id = int(parts[0])
                    post_id = parts[1]
                    user_states[user_id] = {"action": "reply", "parent_id": comment_id, "post_id": post_id}
                    
                    comment = get_comment_by_id(comment_id) if comment_id != 0 else None
                    
                    if comment and comment.get('comment_link'):
                        comment_link = comment['comment_link']
                    else:
                        comment_link = get_post_link_by_post_id(post_id)
                        if not comment_link:
                            comment_link = f"https://t.me/{CHANNEL_USERNAME}/{post_id}"
                    
                    await update.message.reply_text(
                        f"📝 Напишите свой ответ.\n\n"
                        f"Я опубликую его под [этим комментарием]({comment_link}).",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )

# ========== ЗАПУСК ==========
def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    init_db()
    logger.info("База данных инициализирована")
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", handle_deep_link))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("addadmin", addadmin))
    application.add_handler(CommandHandler("removeadmin", removeadmin))
    application.add_handler(CommandHandler("listadmins", listadmins))
    application.add_handler(CommandHandler("bananon", bananon))
    application.add_handler(CommandHandler("unbananon", unbananon))
    application.add_handler(CommandHandler("whois", whois))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))
    application.add_handler(MessageHandler(filters.TEXT & filters.Chat(chat_id=GROUP_ID), handle_group_message))
    
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_expired_bans, interval=3600, first=10)
        logger.info("✅ Запланирована автоматическая проверка истекших банов")
    
    logger.info("Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
