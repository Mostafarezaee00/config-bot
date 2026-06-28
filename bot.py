import logging
import sqlite3
import os
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

# ─── تنظیمات ───────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID   = 6493854621         # آیدی عددی ادمین تلگرام
CARD_NUMBER = "6037-2645-5555-4444"  # شماره کارت برای پرداخت
CARD_OWNER  = "نام صاحب کارت"

# مراحل مکالمه
CHOOSING_PLAN, CONFIRM_ORDER, WAITING_RECEIPT = range(3)

# پلن‌های موجود  {id: {name, price, duration, traffic, description}}
PLANS = {
    "plan_1": {
        "name": "برنزی",
        "price": 49_000,
        "duration": "۳۰ روزه",
        "traffic": "۱۰ گیگ",
        "description": "مناسب استفاده روزانه سبک",
    },
    "plan_2": {
        "name": "نقره‌ای",
        "price": 89_000,
        "duration": "۳۰ روزه",
        "traffic": "۲۰ گیگ",
        "description": "پرطرفدارترین پلن",
    },
    "plan_3": {
        "name": "طلایی",
        "price": 130_000,
        "duration": "۳۰ روزه",
        "traffic": "۳۰ گیگ",
        "description": "بهترین ارزش خرید",
    },
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─── دیتابیس ───────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("shop.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT,
            plan_id     TEXT NOT NULL,
            plan_name   TEXT NOT NULL,
            price       INTEGER NOT NULL,
            status      TEXT DEFAULT 'pending',
            config_text TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save_order(user_id, username, plan_id, plan_name, price) -> int:
    conn = sqlite3.connect("shop.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO orders (user_id, username, plan_id, plan_name, price) VALUES (?,?,?,?,?)",
        (user_id, username, plan_id, plan_name, price),
    )
    order_id = c.lastrowid
    conn.commit()
    conn.close()
    return order_id


def update_order(order_id, status, config_text=None):
    conn = sqlite3.connect("shop.db")
    c = conn.cursor()
    if config_text:
        c.execute(
            "UPDATE orders SET status=?, config_text=? WHERE id=?",
            (status, config_text, order_id),
        )
    else:
        c.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()
    conn.close()


def get_order(order_id) -> dict | None:
    conn = sqlite3.connect("shop.db")
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["id","user_id","username","plan_id","plan_name","price","status","config_text","created_at"]
    return dict(zip(cols, row))


# ─── کیبوردها ──────────────────────────────────────────────────────────────
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [["🛒 خرید کانفیگ", "👤 سفارشات من"],
         ["📖 راهنمای نصب",  "💬 پشتیبانی"]],
        resize_keyboard=True,
    )


def plans_keyboard():
    buttons = []
    for pid, p in PLANS.items():
        label = f"{p['name']} — {p['duration']} — {p['price']:,} تومان"
        buttons.append([InlineKeyboardButton(label, callback_data=f"select_{pid}")])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)


def confirm_keyboard(plan_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید و ادامه به پرداخت", callback_data=f"confirm_{plan_id}")],
        [InlineKeyboardButton("🔙 انتخاب پلن دیگر",       callback_data="back_plans")],
    ])


# ─── هندلرها ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    name = update.effective_user.first_name or "کاربر"
    await update.message.reply_text(
        f"سلام {name} عزیز! 👋\n\n"
        "به ربات فروش کانفیگ خوش اومدی.\n"
        "از منوی پایین یه گزینه انتخاب کن:",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def buy_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # اگه منوی قبلی هنوز باز بود، دکمه‌هاش رو غیرفعال کن تا منوی قدیمی بسته شه
    old_msg_id = context.user_data.get("menu_msg_id")
    if old_msg_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=update.effective_chat.id,
                message_id=old_msg_id,
                reply_markup=None,
            )
        except Exception:
            pass

    # پاک کردن اطلاعات سفارش قبلی (پلن انتخابی، order_id و ...) و شروع تازه
    context.user_data.clear()

    sent = await update.message.reply_text(
        "📦 پلن مورد نظرت رو انتخاب کن:\n\n"
        "هر پلن شامل کانفیگ V2Ray اختصاصیه.",
        reply_markup=plans_keyboard(),
    )
    context.user_data["menu_msg_id"] = sent.message_id
    return CHOOSING_PLAN


async def plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_id = query.data.replace("select_", "")
    plan = PLANS.get(plan_id)
    if not plan:
        await query.edit_message_text("پلن پیدا نشد. دوباره امتحان کن.")
        return CHOOSING_PLAN

    context.user_data["selected_plan"] = plan_id
    text = (
        f"📋 جزئیات پلن «{plan['name']}»\n\n"
        f"⏱ مدت:    {plan['duration']}\n"
        f"📊 حجم:    {plan['traffic']}\n"
        f"💰 قیمت:  {plan['price']:,} تومان\n\n"
        f"ℹ️ {plan['description']}\n\n"
        "آیا این پلن رو تایید می‌کنی؟"
    )
    await query.edit_message_text(text, reply_markup=confirm_keyboard(plan_id))
    return CONFIRM_ORDER


async def back_to_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📦 پلن مورد نظرت رو انتخاب کن:",
        reply_markup=plans_keyboard(),
    )
    return CHOOSING_PLAN


async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_id = query.data.replace("confirm_", "")
    plan = PLANS.get(plan_id)
    user = update.effective_user

    order_id = save_order(
        user_id=user.id,
        username=user.username or "",
        plan_id=plan_id,
        plan_name=plan["name"],
        price=plan["price"],
    )
    context.user_data["order_id"] = order_id

    payment_text = (
        f"✅ سفارش شماره #{order_id} ثبت شد!\n\n"
        f"💳 لطفاً مبلغ {plan['price']:,} تومان رو به حساب زیر واریز کن:\n\n"
        f"🏦 شماره کارت:\n`{CARD_NUMBER}`\n"
        f"👤 به نام: {CARD_OWNER}\n\n"
        "📸 بعد از پرداخت، تصویر رسید یا کد پیگیری رو اینجا بفرست.\n\n"
        "⏳ سفارشت حداکثر تا ۳۰ دقیقه بررسی و کانفیگت ارسال میشه."
    )
    await query.edit_message_text(payment_text, parse_mode="Markdown")
    return WAITING_RECEIPT


async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = context.user_data.get("order_id")
    user = update.effective_user
    if not order_id:
        await update.message.reply_text(
            "سفارش فعالی پیدا نشد. لطفاً دوباره از /start شروع کن.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    update_order(order_id, "receipt_received")

    # ارسال اطلاع به ادمین
    order = get_order(order_id)
    admin_text = (
        f"🔔 رسید جدید برای سفارش #{order_id}\n\n"
        f"👤 کاربر: @{user.username or 'بدون یوزرنیم'} (ID: {user.id})\n"
        f"📦 پلن: {order['plan_name']}\n"
        f"💰 مبلغ: {order['price']:,} تومان\n\n"
        "برای تایید یا رد:"
    )
    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید پرداخت", callback_data=f"approve_{order_id}_{user.id}"),
            InlineKeyboardButton("❌ رد پرداخت",   callback_data=f"reject_{order_id}_{user.id}"),
        ]
    ])

    if update.message.photo:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=update.message.photo[-1].file_id,
            caption=admin_text,
            reply_markup=admin_keyboard,
        )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text + f"\n\n📝 متن کاربر: {update.message.text or '—'}",
            reply_markup=admin_keyboard,
        )

    await update.message.reply_text(
        "✅ رسید دریافت شد! در حال بررسی...\n\n"
        "⏳ کانفیگت حداکثر تا ۳۰ دقیقه ارسال میشه.\n"
        "اگه سوالی داشتی از بخش پشتیبانی بپرس.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# ─── پنل ادمین ─────────────────────────────────────────────────────────────
async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer("دسترسی ندارید.", show_alert=True)
        return
    await query.answer()

    _, order_id_str, user_id_str = query.data.split("_", 2)
    order_id = int(order_id_str)
    user_id  = int(user_id_str)

    # ادمین باید کانفیگ رو بنویسه
    context.bot_data[f"pending_config_{order_id}"] = user_id
    await query.edit_message_caption(
        caption=query.message.caption + "\n\n✅ تایید شد. لطفاً متن کانفیگ رو ارسال کن:",
        reply_markup=None,
    )
    context.user_data["awaiting_config_for"] = order_id


async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer("دسترسی ندارید.", show_alert=True)
        return
    await query.answer()

    _, order_id_str, user_id_str = query.data.split("_", 2)
    order_id = int(order_id_str)
    user_id  = int(user_id_str)

    update_order(order_id, "rejected")
    await query.edit_message_caption(
        caption=query.message.caption + "\n\n❌ رد شد.",
        reply_markup=None,
    )
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"❌ متاسفانه پرداخت سفارش #{order_id} تایید نشد.\n\n"
            "لطفاً با پشتیبانی تماس بگیر یا دوباره رسید معتبر بفرست."
        ),
    )


async def admin_send_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین متن کانفیگ رو می‌فرسته → به کاربر تحویل داده میشه"""
    if update.effective_user.id != ADMIN_ID:
        return
    order_id = context.user_data.get("awaiting_config_for")
    if not order_id:
        return

    config_text = update.message.text
    order = get_order(order_id)
    if not order:
        await update.message.reply_text("سفارش پیدا نشد.")
        return

    update_order(order_id, "delivered", config_text)
    user_id = order["user_id"]

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"🎉 کانفیگت آماده‌ست!\n\n"
            f"📦 پلن: {order['plan_name']}\n\n"
            f"```\n{config_text}\n```\n\n"
            "برای راهنمای نصب روی «📖 راهنمای نصب» بزن."
        ),
        parse_mode="Markdown",
    )
    await update.message.reply_text(
        f"✅ کانفیگ سفارش #{order_id} با موفقیت به کاربر ارسال شد."
    )
    context.user_data.pop("awaiting_config_for", None)


# ─── دستورات دیگر ──────────────────────────────────────────────────────────
async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("shop.db")
    c = conn.cursor()
    c.execute(
        "SELECT id, plan_name, price, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 5",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "هنوز سفارشی ثبت نکردی.\nبرای خرید روی «🛒 خرید کانفیگ» بزن.",
            reply_markup=main_menu_keyboard(),
        )
        return

    status_fa = {
        "pending":          "⏳ در انتظار رسید",
        "receipt_received": "🔍 در حال بررسی",
        "rejected":         "❌ رد شده",
        "delivered":        "✅ تحویل داده شده",
    }
    lines = ["📋 آخرین سفارشات شما:\n"]
    for row in rows:
        oid, plan, price, status, created = row
        lines.append(
            f"#{oid} — {plan} — {price:,} تومان\n"
            f"   وضعیت: {status_fa.get(status, status)}\n"
            f"   تاریخ: {created[:10]}\n"
        )
    await update.message.reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


async def install_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    guide = (
        "📖 راهنمای نصب کانفیگ V2Ray\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📱 اندروید:\n"
        "1. نصب V2RayNG از گوگل‌پلی\n"
        "2. + → Import config from clipboard\n"
        "3. لینک کانفیگ رو paste کن\n"
        "4. دکمه شروع رو بزن\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🍎 iOS:\n"
        "1. نصب Streisand از AppStore\n"
        "2. + → Import from URL\n"
        "3. لینک رو وارد کن\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🖥 ویندوز:\n"
        "1. دانلود V2RayN از GitHub\n"
        "2. سرورها → اضافه کردن سرور از کلیپبورد\n"
        "3. لینک رو paste کن\n\n"
        "❓ مشکل داری؟ با پشتیبانی تماس بگیر."
    )
    await update.message.reply_text(guide, reply_markup=main_menu_keyboard())


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬 پشتیبانی\n\n"
        "برای ارتباط با ما مستقیم پیام بده:\n\n"
        "👤 @Mosstafa2005\n\n"
        "⏰ ساعات پاسخ‌دهی: ۹ صبح تا ۱۱ شب",
        reply_markup=main_menu_keyboard(),
    )


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "لطفاً از منوی پایین یه گزینه انتخاب کن.",
        reply_markup=main_menu_keyboard(),
    )


# ─── راه‌اندازی ─────────────────────────────────────────────────────────────
async def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler برای فرایند خرید
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🛒 خرید کانفیگ$"), buy_config)],
        states={
            CHOOSING_PLAN: [
                MessageHandler(filters.Regex("^🛒 خرید کانفیگ$"), buy_config),
                CallbackQueryHandler(plan_selected, pattern="^select_"),
                CallbackQueryHandler(fallback,      pattern="^back_main"),
            ],
            CONFIRM_ORDER: [
                MessageHandler(filters.Regex("^🛒 خرید کانفیگ$"), buy_config),
                CallbackQueryHandler(confirm_order, pattern="^confirm_"),
                CallbackQueryHandler(back_to_plans, pattern="^back_plans"),
            ],
            WAITING_RECEIPT: [
                MessageHandler(filters.Regex("^🛒 خرید کانفیگ$"), buy_config),
                MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, receive_receipt)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    # دکمه‌های پنل ادمین
    app.add_handler(CallbackQueryHandler(admin_approve, pattern="^approve_"))
    app.add_handler(CallbackQueryHandler(admin_reject,  pattern="^reject_"))

    # دستورات منو
    app.add_handler(MessageHandler(filters.Regex("^👤 سفارشات من$"),   my_orders))
    app.add_handler(MessageHandler(filters.Regex("^📖 راهنمای نصب$"), install_guide))
    app.add_handler(MessageHandler(filters.Regex("^💬 پشتیبانی$"),    support))

    # ارسال کانفیگ توسط ادمین
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID),
        admin_send_config,
    ))

    print("ربات شروع به کار کرد...")
    async with app:
        await app.start()
        await app.updater.start_polling()
        print("در حال اجرا... برای توقف Ctrl+C بزن")
        import asyncio
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
