"""Telegram-бот «Список задач».

Запуск:  python bot.py   (токен берётся из файла .env — см. README.md)

Как это устроено:
  * Telegram присылает боту «обновления» (сообщения, нажатия кнопок). Мы получаем их через polling —
    библиотека сама спрашивает у Telegram «есть новое?», поэтому белый IP-адрес и домен не нужны.
  * На каждое обновление библиотека вызывает подходящую функцию-обработчик (handler) из этого файла.
  * Раз в 30 секунд работает фоновая задача: смотрит в базу, не пора ли кому-то напомнить.
    Напоминания лежат в базе, а не в памяти — после перезапуска бота они не пропадают.
"""
import logging
import os
import sys
from datetime import timedelta
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import texts
from db import Database
from exporter import build_xlsx
from timeutil import format_dt, parse_when, utcnow

logger = logging.getLogger(__name__)

MAX_TASK_LEN = 500       # длиннее — просим сократить
LIST_LIMIT = 15          # сколько задач показываем в /list (у каждой своя строка кнопок)
PREVIEW_LEN = 120        # длинный текст задачи в списке обрезаем
CHECK_INTERVAL = 30      # как часто (сек) проверяем, не пора ли напомнить
MAX_SNOOZE_MIN = 7 * 24 * 60


# ---------------------------------------------------------------- вспомогательное

def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def parse_num(arg: str) -> int | None:
    """'3' -> 3. Всё остальное («abc», «-1», «99999999999999999999») -> None.

    Ограничение по длине защищает базу: SQLite не принимает числа больше 64 бит.
    """
    return int(arg) if arg.isdecimal() and len(arg) <= 9 else None


async def reply(update: Update, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def safe_edit(query, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Меняет текст сообщения с кнопкой. Telegram ругается, если текст не изменился — это не ошибка."""
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def build_list(db: Database, user_id: int, include_all: bool) -> tuple[str, InlineKeyboardMarkup | None]:
    """Собирает текст списка задач и кнопки ✅/🗑 под ним."""
    tasks = db.list_tasks(user_id, include_done=include_all)
    if not tasks:
        return texts.EMPTY_LIST, None

    zone = db.get_zone(user_id)
    flag = int(include_all)  # зашиваем в кнопку, чтобы после нажатия показать тот же вид списка
    lines = [texts.LIST_HEADER, ""]
    keyboard = []

    for task in tasks[:LIST_LIMIT]:
        preview = task.text if len(task.text) <= PREVIEW_LEN else task.text[: PREVIEW_LEN - 1] + "…"
        line = f"{'✅' if task.done else '⬜'} <b>{task.num}.</b> {escape(preview)}"
        if task.remind_at and not task.done and not task.reminded:
            line += f"  ⏰ {format_dt(task.remind_at, zone)}"
        lines.append(line)

        buttons = []
        if not task.done:
            buttons.append(InlineKeyboardButton(f"✅ {task.num}", callback_data=f"done:{task.num}:{flag}"))
        buttons.append(InlineKeyboardButton(f"🗑 {task.num}", callback_data=f"del:{task.num}:{flag}"))
        keyboard.append(buttons)

    if len(tasks) > LIST_LIMIT:
        lines.append("")
        lines.append(texts.MORE_TASKS.format(n=len(tasks) - LIST_LIMIT))

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------- команды

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, texts.HELP)


async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Общая часть для /add и для обычных сообщений."""
    text = text.strip()
    if not text:
        await reply(update, texts.USAGE_ADD)
        return
    if len(text) > MAX_TASK_LEN:
        await reply(update, texts.TOO_LONG.format(limit=MAX_TASK_LEN))
        return
    num = get_db(context).add_task(update.effective_user.id, text)
    await reply(update, texts.ADDED.format(num=num))


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await save_task(update, context, " ".join(context.args))


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Любое сообщение, не являющееся командой, — это новая задача."""
    await save_task(update, context, update.message.text)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    include_all = bool(context.args) and context.args[0].lower() == "all"
    text, markup = build_list(get_db(context), update.effective_user.id, include_all)
    await reply(update, text, markup)


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    num = parse_num(context.args[0]) if context.args else None
    if num is None:
        await reply(update, texts.USAGE_DONE)
        return
    ok = get_db(context).mark_done(update.effective_user.id, num)
    await reply(update, (texts.MARKED_DONE if ok else texts.NO_OPEN_TASK).format(num=num))


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    num = parse_num(context.args[0]) if context.args else None
    if num is None:
        await reply(update, texts.USAGE_DELETE)
        return
    ok = get_db(context).delete_task(update.effective_user.id, num)
    await reply(update, (texts.DELETED if ok else texts.NO_TASK).format(num=num))


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/remind 3 30m — напомнить про задачу №3 через 30 минут."""
    db = get_db(context)
    user_id = update.effective_user.id
    num = parse_num(context.args[0]) if context.args else None
    if num is None or len(context.args) < 2:
        await reply(update, texts.USAGE_REMIND)
        return

    when_text = " ".join(context.args[1:])
    if when_text.lower() in ("off", "cancel", "стоп", "отмена"):
        ok = db.set_reminder(user_id, num, None)
        await reply(update, (texts.REMINDER_CLEARED if ok else texts.NO_OPEN_TASK).format(num=num))
        return

    zone = db.get_zone(user_id)
    try:
        when = parse_when(when_text, utcnow(), zone)
    except ValueError as exc:  # текст ошибки уже написан для пользователя
        await reply(update, str(exc))
        return

    if db.set_reminder(user_id, num, when):
        await reply(update, texts.REMINDER_SET.format(num=num, when=format_dt(when, zone), tz=zone.key))
    else:
        await reply(update, texts.NO_OPEN_TASK.format(num=num))


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db(context)
    user_id = update.effective_user.id
    tasks = db.list_tasks(user_id, include_done=True)
    if not tasks:
        await reply(update, texts.EXPORT_EMPTY)
        return
    data = build_xlsx(tasks, db.get_zone(user_id))
    await update.message.reply_document(
        document=data,
        filename=f"tasks_{utcnow():%Y-%m-%d}.xlsx",
        caption=texts.EXPORT_CAPTION.format(n=len(tasks)),
    )


async def tz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db(context)
    user_id = update.effective_user.id
    if not context.args:
        await reply(update, texts.TZ_CURRENT.format(tz=db.get_zone(user_id).key))
        return
    try:
        zone = ZoneInfo(context.args[0])
    except (ZoneInfoNotFoundError, ValueError, OSError):
        await reply(update, texts.TZ_BAD)
        return
    db.set_tz(user_id, zone.key)
    await reply(update, texts.TZ_SET.format(tz=zone.key, now=format_dt(utcnow(), zone)))


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, texts.UNKNOWN_COMMAND)


# ---------------------------------------------------------------- нажатия на кнопки

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Все inline-кнопки. В callback_data зашито «действие:номер[:параметр]», напр. 'done:3:0'.

    done / del   — кнопки под списком /list;
    rdone/snooze — кнопки под сообщением-напоминанием.
    """
    query = update.callback_query
    db = get_db(context)
    user_id = query.from_user.id

    parts = (query.data or "").split(":")
    action = parts[0]
    num = parse_num(parts[1]) if len(parts) > 1 else None
    if num is None:
        await query.answer()  # на каждое нажатие надо ответить, иначе у кнопки «крутится часики»
        return

    if action in ("done", "del"):
        include_all = len(parts) > 2 and parts[2] == "1"
        if action == "done":
            ok, toast = db.mark_done(user_id, num), texts.TOAST_DONE
        else:
            ok, toast = db.delete_task(user_id, num), texts.TOAST_DELETED
        await query.answer(toast if ok else texts.TOAST_GONE)
        text, markup = build_list(db, user_id, include_all)
        await safe_edit(query, text, markup)

    elif action == "rdone":
        task = db.get_task(user_id, num)
        if task is None:
            await query.answer(texts.TOAST_GONE)
            return
        db.mark_done(user_id, num)
        await query.answer(texts.TOAST_DONE)
        await safe_edit(query, texts.REMINDER_DONE.format(num=num, text=escape(task.text)))

    elif action == "snooze":
        minutes = parse_num(parts[2]) if len(parts) > 2 else None
        task = db.get_task(user_id, num)
        if minutes is None or not 0 < minutes <= MAX_SNOOZE_MIN or task is None or task.done:
            await query.answer(texts.TOAST_GONE)
            return
        when = utcnow() + timedelta(minutes=minutes)
        db.set_reminder(user_id, num, when)
        await query.answer(texts.TOAST_SNOOZED)
        await safe_edit(
            query,
            texts.SNOOZED.format(num=num, text=escape(task.text), when=format_dt(when, db.get_zone(user_id))),
        )

    else:
        await query.answer()


# ---------------------------------------------------------------- напоминания (фоновая задача)

async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускается каждые CHECK_INTERVAL секунд: рассылает напоминания, время которых пришло."""
    db = get_db(context)
    for task in db.due_reminders(utcnow()):
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Done", callback_data=f"rdone:{task.num}"),
            InlineKeyboardButton("💤 10 min", callback_data=f"snooze:{task.num}:10"),
            InlineKeyboardButton("💤 1 hour", callback_data=f"snooze:{task.num}:60"),
        ]])
        try:
            await context.bot.send_message(
                chat_id=task.user_id,  # в личных чатах id чата == id пользователя
                text=texts.REMINDER.format(num=task.num, text=escape(task.text)),
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        except (Forbidden, BadRequest) as exc:
            # Навсегда: пользователь заблокировал бота или чата больше нет. Повторять бессмысленно.
            logger.warning("Dropping reminder %s: %s", task.id, exc)
        except TelegramError as exc:
            # Временный сбой (нет сети, Telegram недоступен): не помечаем отправленным,
            # на следующей проверке попробуем снова.
            logger.warning("Could not send reminder %s (%s) — will retry", task.id, exc)
            continue
        db.mark_reminded(task)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error while processing an update", exc_info=context.error)


async def post_init(app: Application) -> None:
    """Выполняется один раз при старте: заполняет меню «/» в Telegram."""
    await app.bot.set_my_commands([BotCommand(cmd, desc) for cmd, desc in texts.MENU])


# ---------------------------------------------------------------- запуск

def main() -> None:
    load_dotenv()  # читает файл .env рядом с bot.py и кладёт значения в os.environ
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx на уровне INFO пишет в лог полный URL запроса, а в нём — токен бота. Не даём ему это делать.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    token = os.getenv("BOT_TOKEN")
    if not token:
        sys.exit("BOT_TOKEN не задан. Скопируй .env.example в .env и впиши токен от @BotFather.")

    try:
        db = Database(os.getenv("DB_PATH", "tasks.db"), default_tz=os.getenv("DEFAULT_TZ", "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        sys.exit("DEFAULT_TZ указан неверно. Пример: Europe/Berlin")

    app = Application.builder().token(token).post_init(post_init).build()
    app.bot_data["db"] = db  # так обработчики получают доступ к базе: get_db(context)

    private = filters.ChatType.PRIVATE  # бот работает только в личных сообщениях
    app.add_handler(CommandHandler(["start", "help"], help_command, filters=private))
    app.add_handler(CommandHandler("add", add_command, filters=private))
    app.add_handler(CommandHandler("list", list_command, filters=private))
    app.add_handler(CommandHandler("done", done_command, filters=private))
    app.add_handler(CommandHandler("delete", delete_command, filters=private))
    app.add_handler(CommandHandler("remind", remind_command, filters=private))
    app.add_handler(CommandHandler("export", export_command, filters=private))
    app.add_handler(CommandHandler("tz", tz_command, filters=private))
    app.add_handler(CallbackQueryHandler(on_button))
    # Порядок важен: обработчики проверяются сверху вниз, срабатывает первый подходящий.
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, text_message))
    app.add_handler(MessageHandler(private & filters.COMMAND, unknown_command))
    app.add_error_handler(on_error)

    app.job_queue.run_repeating(check_reminders, interval=CHECK_INTERVAL, first=5)

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
