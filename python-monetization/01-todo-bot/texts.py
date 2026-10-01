"""Все тексты, которые видит пользователь бота, — в одном месте.

Чтобы подогнать бота под клиента (другой язык, другой тон, другое название),
достаточно поменять этот файл — в логику (bot.py) лезть не нужно.

Тексты отправляются в режиме HTML, поэтому:
  * <b>жирный</b>, <code>моноширинный</code> — работают;
  * символы < и > в обычном тексте пишем как &lt; и &gt;;
  * {num}, {text} и т.п. — места, куда bot.py подставляет значения.
"""

MENU = [
    ("add", "Add a task"),
    ("list", "Show open tasks"),
    ("done", "Mark a task as done"),
    ("delete", "Delete a task"),
    ("remind", "Set a reminder"),
    ("export", "Export tasks to Excel"),
    ("tz", "Set your time zone"),
    ("help", "How to use the bot"),
]

HELP = (
    "👋 <b>Task Bot</b>\n"
    "Send me any message — I'll save it as a task.\n\n"
    "<b>Commands</b>\n"
    "/add &lt;text&gt; — add a task\n"
    "/list — open tasks (<code>/list all</code> also shows finished ones)\n"
    "/done &lt;n&gt; — mark task n as done\n"
    "/delete &lt;n&gt; — delete task n\n"
    "/remind &lt;n&gt; &lt;when&gt; — set a reminder (<code>/remind 3 off</code> cancels it)\n"
    "/export — download all your tasks as an Excel file\n"
    "/tz &lt;zone&gt; — set your time zone, e.g. <code>/tz Europe/Berlin</code>\n\n"
    "<b>Reminder examples</b>\n"
    "<code>/remind 3 30m</code> — in 30 minutes (also <code>2h</code>, <code>1d</code>, <code>1h30m</code>)\n"
    "<code>/remind 3 18:00</code> — today at 18:00 (tomorrow if it has passed)\n"
    "<code>/remind 3 tomorrow 09:00</code>\n"
    "<code>/remind 3 31.12 20:00</code> or <code>/remind 3 2026-12-31 20:00</code>"
)

# --- добавление / список ---
ADDED = "✅ Task <b>#{num}</b> saved.\nWant a reminder? <code>/remind {num} 1h</code>"
TOO_LONG = "That's a bit long — please keep a task under {limit} characters."
USAGE_ADD = "Usage: <code>/add buy milk</code>"
LIST_HEADER = "📋 <b>Your tasks</b>"
EMPTY_LIST = "📭 No open tasks. Send me any message and I'll save it as a task."
MORE_TASKS = "…and {n} more (finish some to see them)"

# --- done / delete ---
USAGE_DONE = "Usage: <code>/done 3</code> (the number from /list)"
USAGE_DELETE = "Usage: <code>/delete 3</code> (the number from /list)"
MARKED_DONE = "✅ Task #{num} marked as done."
DELETED = "🗑 Task #{num} deleted."
NO_OPEN_TASK = "🤷 There is no open task #{num}."
NO_TASK = "🤷 There is no task #{num}."

# --- всплывающие подсказки на кнопках ---
TOAST_DONE = "Done ✅"
TOAST_DELETED = "Deleted 🗑"
TOAST_SNOOZED = "Snoozed 💤"
TOAST_GONE = "That task is already gone"

# --- напоминания ---
USAGE_REMIND = "Usage: <code>/remind 3 30m</code> — see /help for more examples."
REMINDER_SET = "⏰ Got it — I'll remind you about #{num} on <b>{when}</b> ({tz})."
REMINDER_CLEARED = "🔕 Reminder for #{num} removed."
REMINDER = "⏰ <b>Reminder</b>\n#{num}: {text}"
REMINDER_DONE = "✅ Done: #{num} {text}"
SNOOZED = "💤 Snoozed until <b>{when}</b>: #{num} {text}"
BAD_TIME = (
    "I didn't understand that time. Try one of:\n"
    "<code>30m</code>, <code>2h</code>, <code>1d</code>, <code>1h30m</code>\n"
    "<code>18:00</code>, <code>tomorrow 09:00</code>\n"
    "<code>31.12 20:00</code>, <code>2026-12-31 20:00</code>"
)
TIME_IN_PAST = "That moment is already in the past 🙂"

# --- часовой пояс ---
TZ_CURRENT = "🌍 Your time zone is <b>{tz}</b>. Change it with <code>/tz Europe/Berlin</code>."
TZ_SET = "🌍 Time zone set to <b>{tz}</b>. Your local time now: {now}."
TZ_BAD = "Unknown time zone. Use a name like <code>Europe/Berlin</code>, <code>America/New_York</code> or <code>Asia/Tokyo</code>."

# --- экспорт ---
EXPORT_EMPTY = "Nothing to export yet — add a task first."
EXPORT_CAPTION = "📊 {n} tasks exported."

UNKNOWN_COMMAND = "Unknown command. See /help."
