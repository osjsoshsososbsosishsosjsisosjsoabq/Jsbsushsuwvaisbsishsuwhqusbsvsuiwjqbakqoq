#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gift Roulette Bot (Python only) - Termux friendly (no Pillow)
English UI + button-based menu

Buttons:
- 🎡 Spin
- 🎁 Gifts (lists ONLY the 4 gifts: Frog, Hat, Bear, Rocket)
- 🛒 Buy Spins (instructions + shows cost)
- 🔗 Referral Link (shows link)
- 📣 Channel (URL button to required channel)
- 👑 Admin Panel (only admins)

Admin can change required channel and other settings.
"""

import os
import sqlite3
import random
import asyncio
from datetime import datetime, date
from typing import List, Dict, Optional

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")
DB_PATH = os.getenv("DB_PATH", "gift_roulette.db").strip()

ADMIN_IDS = set()
raw_admins = (os.getenv("ADMIN_IDS", "") or "").strip()
if raw_admins:
    for part in raw_admins.split(","):
        part = part.strip()
        if part.isdigit():
            ADMIN_IDS.add(int(part))
if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def table_columns(con: sqlite3.Connection, table: str) -> set:
    cur = con.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def ensure_column(con: sqlite3.Connection, table: str, col: str, col_type: str, default_sql: str = "") -> None:
    if col in table_columns(con, table):
        return
    cur = con.cursor()
    sql = f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
    if default_sql:
        sql += f" DEFAULT {default_sql}"
    cur.execute(sql)


def init_db() -> None:
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS config(
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
      user_id INTEGER PRIMARY KEY,
      username TEXT,
      first_name TEXT,
      referrer_id INTEGER,
      free_spins INTEGER NOT NULL DEFAULT 0,
      paid_spins INTEGER NOT NULL DEFAULT 0,
      last_free_date TEXT,
      created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS spins(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      used_type TEXT NOT NULL,
      result_idx INTEGER NOT NULL,
      result_name TEXT NOT NULL,
      result_sticker TEXT,
      created_at TEXT NOT NULL
    )
    """)

    # Auto-migrate
    ensure_column(con, "users", "username", "TEXT")
    ensure_column(con, "users", "first_name", "TEXT")
    ensure_column(con, "users", "referrer_id", "INTEGER")
    ensure_column(con, "users", "free_spins", "INTEGER", "0")
    ensure_column(con, "users", "paid_spins", "INTEGER", "0")
    ensure_column(con, "users", "last_free_date", "TEXT")
    ensure_column(con, "users", "created_at", "TEXT")

    ensure_column(con, "spins", "used_type", "TEXT")
    ensure_column(con, "spins", "result_idx", "INTEGER")
    ensure_column(con, "spins", "result_name", "TEXT")
    ensure_column(con, "spins", "result_sticker", "TEXT")
    ensure_column(con, "spins", "created_at", "TEXT")

    con.commit()

    defaults = {
        "required_channel": "@YOUR_CHANNEL",
        "daily_free_spins": "1",
        "ref_bonus_spins": "1",
        "spin_cost_paid": "1",

        "contact_username": "@YourUsername",
        "lose_name": "❌ Better luck next time 🍀",
        "lose_weight": "999996",

        "gift1_name": "🐸 Frog",
        "gift1_weight": "1",
        "gift1_sticker": "CAACAgQAAxkBAANDaVwubFAKAbQ0B995A7Z_uVQwRkQAAlEVAAKRsGhSdWvnThzmAT44BA",

        "gift2_name": "🎩 Hat",
        "gift2_weight": "1",
        "gift2_sticker": "CAACAgQAAxkBAAMwaVu0TKSGzZ1Toee912YYD09c8ZUAAsEXAAJJOhhS-kc7biMyTbM4BA",

        "gift3_name": "🧸 Bear",
        "gift3_weight": "1",
        "gift3_sticker": "CAACAgQAAxkBAANHaVwuc5sIOGwIJ5WCvTBvbs6THcgAAr8VAALCaChRf_q3xzMsSfY4BA",

        "gift4_name": "🚀 Rocket",
        "gift4_weight": "1",
        "gift4_sticker": "CAACAgQAAxkBAANJaVwuhGDyQolwEtGYj7lUJmFNzAwAAvUhAAKSvChRB_8-1v1glj84BA",
    }
    for k, v in defaults.items():
        cur.execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", (k, v))

    con.commit()
    con.close()


def cfg_get(key: str) -> str:
    con = db()
    cur = con.cursor()
    cur.execute("SELECT value FROM config WHERE key=?", (key,))
    r = cur.fetchone()
    con.close()
    return r["value"] if r else ""


def cfg_set(key: str, value: str) -> None:
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO config(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()
    con.close()


def ensure_user(u) -> None:
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users(user_id, username, first_name, created_at) VALUES(?,?,?,?)",
        (u.id, u.username or "", u.first_name or "", datetime.utcnow().isoformat()),
    )
    cur.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (u.username or "", u.first_name or "", u.id))
    con.commit()
    con.close()


def get_user(user_id: int):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    con.close()
    return r


def set_referrer_if_empty(user_id: int, referrer_id: int) -> bool:
    if referrer_id == user_id:
        return False
    con = db()
    cur = con.cursor()
    cur.execute("SELECT referrer_id FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    if not r or r["referrer_id"] is not None:
        con.close()
        return False
    cur.execute("UPDATE users SET referrer_id=? WHERE user_id=?", (referrer_id, user_id))
    con.commit()
    con.close()
    return True


def add_free_spins(user_id: int, amount: int) -> None:
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET free_spins = free_spins + ? WHERE user_id=?", (amount, user_id))
    con.commit()
    con.close()


def add_paid_spins(user_id: int, amount: int) -> None:
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET paid_spins = paid_spins + ? WHERE user_id=?", (amount, user_id))
    con.commit()
    con.close()


def refresh_daily_free(user_id: int) -> None:
    daily = int(cfg_get("daily_free_spins") or "0")
    today = date.today().isoformat()
    con = db()
    cur = con.cursor()
    cur.execute("SELECT last_free_date FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    if r and r["last_free_date"] != today:
        cur.execute("UPDATE users SET free_spins=?, last_free_date=? WHERE user_id=?", (daily, today, user_id))
        con.commit()
    con.close()


def load_outcomes() -> List[Dict]:
    lose = {
        "idx": 0,
        "name": (cfg_get("lose_name") or "❌ Better luck next time 🍀").strip(),
        "weight": max(int(cfg_get("lose_weight") or "0"), 0),
        "sticker": None,
    }
    gifts = []
    for i in range(1, 5):
        gifts.append({
            "idx": i,
            "name": (cfg_get(f"gift{i}_name") or f"Gift {i}").strip(),
            "weight": max(int(cfg_get(f"gift{i}_weight") or "0"), 0),
            "sticker": (cfg_get(f"gift{i}_sticker") or "").strip() or None,
        })
    outcomes = [lose] + gifts
    if sum(o["weight"] for o in outcomes) <= 0:
        outcomes[0]["weight"] = 999996
        for g in outcomes[1:]:
            g["weight"] = 1
    return outcomes


def pick_weighted(outcomes: List[Dict]) -> Dict:
    total = sum(o["weight"] for o in outcomes)
    r = random.randint(1, total)
    s = 0
    for o in outcomes:
        s += o["weight"]
        if r <= s:
            return o
    return outcomes[0]


async def send_spin_animation(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await context.bot.send_dice(chat_id=chat_id, emoji="🎰")
        await asyncio.sleep(2.8)
    except Exception:
        msg = await context.bot.send_message(chat_id=chat_id, text="Spinning...")
        for s in ["🎡", "🌀", "🎠", "✨", "🎡"]:
            try:
                await msg.edit_text(f"{s} Spinning...")
            except Exception:
                pass
            await asyncio.sleep(0.25)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def normalize_channel_to_url(ch: str) -> str:
    ch = (ch or "").strip()
    if not ch:
        return ""
    if ch.startswith("https://t.me/"):
        return ch
    if ch.startswith("@"):
        return "https://t.me/" + ch[1:]
    return "https://t.me/" + ch


async def is_subscribed(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    ch = (cfg_get("required_channel") or "").strip()
    if not ch or ch == "@YOUR_CHANNEL":
        return True
    try:
        member = await context.bot.get_chat_member(ch, user_id)
        return member.status in ("creator", "administrator", "member")
    except Exception:
        return False


async def get_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    me = await context.bot.get_me()
    return me.username or ""


def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    ch = cfg_get("required_channel").strip()
    ch_url = normalize_channel_to_url(ch)
    rows = [
        [InlineKeyboardButton("🎡 Spin", callback_data="spin")],
        [InlineKeyboardButton("🎁 Gifts", callback_data="gifts"),
         InlineKeyboardButton("🛒 Buy Spins", callback_data="buy")],
        [InlineKeyboardButton("🔗 Referral Link", callback_data="ref")],
        [InlineKeyboardButton("💬 Contact", callback_data="contact")],
    ]
    if ch_url:
        rows.append([InlineKeyboardButton("📣 Channel", url=ch_url)])
    rows.append([InlineKeyboardButton("👤 My Account", callback_data="me"),
                 InlineKeyboardButton("🔄 Refresh", callback_data="refresh")])
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin:menu")])
    return InlineKeyboardMarkup(rows)


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Set Required Channel", callback_data="admin:setchannel")],
        [InlineKeyboardButton("💬 Set Contact Username", callback_data="admin:setcontact")],
        [InlineKeyboardButton("🗓 Daily Free Spins", callback_data="admin:setdaily"),
         InlineKeyboardButton("🔗 Referral Bonus", callback_data="admin:setref")],
        [InlineKeyboardButton("💰 Paid Spin Cost", callback_data="admin:setcost")],
        [InlineKeyboardButton("❌ Lose Weight", callback_data="admin:setlose")],
        [InlineKeyboardButton("🎁 Edit Gifts", callback_data="admin:gifts")],
        [InlineKeyboardButton("➕ Add Spins (User)", callback_data="admin:addspins")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back:menu")],
    ])


def admin_gifts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Gift 1", callback_data="admin:setgift:1"),
         InlineKeyboardButton("Gift 2", callback_data="admin:setgift:2")],
        [InlineKeyboardButton("Gift 3", callback_data="admin:setgift:3"),
         InlineKeyboardButton("Gift 4", callback_data="admin:setgift:4")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")],
    ])


def admin_addspins_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Add FREE spins", callback_data="admin:addfree")],
        [InlineKeyboardButton("Add PAID balance", callback_data="admin:addpaid")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")],
    ])


async def render_main(update: Update, context: ContextTypes.DEFAULT_TYPE, note: str = "") -> None:
    u = update.effective_user
    ensure_user(u)
    refresh_daily_free(u.id)

    user = get_user(u.id)
    outcomes = load_outcomes()
    gifts = [o for o in outcomes if o["idx"] != 0]

    ch = cfg_get("required_channel").strip()
    daily = cfg_get("daily_free_spins").strip()
    ref_bonus = cfg_get("ref_bonus_spins").strip()
    cost = cfg_get("spin_cost_paid").strip()

    lines = []
    lines.append("🎁 <b>Gift Roulette</b>")
    if note:
        lines.append(note)
    lines.append("")
    lines.append("👤 <b>Your account</b>")
    lines.append(f"• ID: <code>{u.id}</code>")
    lines.append(f"• Free spins today: <b>{user['free_spins']}</b>")
    lines.append(f"• Paid balance: <b>{user['paid_spins']}</b>")
    lines.append("")
    lines.append("🎁 <b>Gifts in roulette</b>")
    for g in gifts:
        lines.append(f"• {esc(g['name'])}")
    lines.append("")
    lines.append("⚙️ <b>Settings</b>")
    lines.append(f"• Required channel: <code>{esc(ch)}</code>")
    lines.append(f"• Daily free spins: <b>{esc(daily)}</b>")
    lines.append(f"• Referral bonus: <b>{esc(ref_bonus)}</b>")
    lines.append(f"• Paid spin cost: <b>{esc(cost)}</b>")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(u.id),
        disable_web_page_preview=True,
    )


async def render_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not is_admin(u.id):
        await update.effective_message.reply_text("❌ Admin panel is not available.")
        return

    outcomes = load_outcomes()
    gifts = [o for o in outcomes if o["idx"] != 0]
    lose = next(o for o in outcomes if o["idx"] == 0)

    ch = cfg_get("required_channel").strip()
    daily = cfg_get("daily_free_spins").strip()
    ref_bonus = cfg_get("ref_bonus_spins").strip()
    cost = cfg_get("spin_cost_paid").strip()

    lines = [
        "👑 <b>Admin Panel</b>",
        "",
        f"📣 Required channel: <code>{esc(ch)}</code>",
        f"🗓 Daily free spins: <b>{esc(daily)}</b>",
        f"🔗 Referral bonus: <b>{esc(ref_bonus)}</b>",
        f"💰 Paid spin cost: <b>{esc(cost)}</b>",
        f"❌ Lose weight: <b>{lose['weight']}</b>",
        "",
        "🎁 <b>Gifts</b>",
    ]
    for g in gifts:
        lines.append(f"• {esc(g['name'])} | weight: <b>{g['weight']}</b> | {'OK' if g['sticker'] else 'MISSING'}")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_kb(),
        disable_web_page_preview=True,
    )


def set_await(context: ContextTypes.DEFAULT_TYPE, data: Optional[dict]) -> None:
    if data is None:
        context.user_data.pop("await", None)
    else:
        context.user_data["await"] = data


def get_await(context: ContextTypes.DEFAULT_TYPE) -> Optional[dict]:
    return context.user_data.get("await")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    ensure_user(u)

    if context.args:
        try:
            ref = int(context.args[0])
        except Exception:
            ref = 0
        if ref:
            if set_referrer_if_empty(u.id, ref) and ref != u.id:
                bonus = int(cfg_get("ref_bonus_spins") or "0")
                if bonus > 0:
                    add_free_spins(ref, bonus)
                    try:
                        await context.bot.send_message(
                            chat_id=ref,
                            text=f"🎉 New referral! You received <b>{bonus}</b> free spin(s).",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass

    set_await(context, None)
    await render_main(update, context)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    if not is_admin(u.id):
        await update.effective_message.reply_text("❌ This command is for admins only.")
        return
    await render_admin_menu(update, context)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = update.effective_user
    ensure_user(u)
    refresh_daily_free(u.id)

    data = q.data or ""

    if data == "back:menu":
        set_await(context, None)
        await render_main(update, context)
        return

    if data in ("refresh", "me"):
        user = get_user(u.id)
        note = ""
        if data == "me":
            note = f"👤 Free: <b>{user['free_spins']}</b> • Paid: <b>{user['paid_spins']}</b>"
        await render_main(update, context, note=note)
        return

    if data == "gifts":
        gifts = [o for o in load_outcomes() if o["idx"] != 0]
        txt = ["🎁 <b>Roulette Gifts</b>", ""]
        for g in gifts:
            txt.append(f"• {esc(g['name'])}")
        await q.message.reply_text("\n".join(txt), parse_mode=ParseMode.HTML)
        return

    if data == "buy":
        contact = cfg_get("contact_username").strip()
        text = (
            "🛒 <b>Buy Spins</b>\n\n"
            "💰 <b>Prices</b>\n"
            "• 3 Spins   = 3 USDT\n"
            "• 7 Spins   = 7 USDT\n"
            "• 20 Spins  = 15 USDT\n"
            "• 100 Spins = 60 USDT\n\n"
            f"📩 Contact: <code>{esc(contact)}</code>\n\n"
            "After payment, the owner/admin will add your spins."
        )
        buttons = []
        if contact.startswith("@"):
            buttons = [[InlineKeyboardButton("💬 Contact to Buy", url=f"https://t.me/{contact[1:]}")]]
        elif contact.startswith("https://t.me/"):
            buttons = [[InlineKeyboardButton("💬 Contact to Buy", url=contact)]]
        await q.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            disable_web_page_preview=True,
        )
        return

    if data == "contact":
        contact = cfg_get("contact_username").strip()
        text = (
            "🛒 <b>Buy Spins</b>\n\n"
            "💰 <b>Prices</b>\n"
            "• 3 Spins   = 3 USDT\n"
            "• 7 Spins   = 7 USDT\n"
            "• 20 Spins  = 15 USDT\n"
            "• 100 Spins = 60 USDT\n\n"
            f"📩 Contact: <code>{esc(contact)}</code>"
        )
        buttons = []
        if contact.startswith("@"):
            buttons = [[InlineKeyboardButton("💬 Open Chat", url=f"https://t.me/{contact[1:]}")]]
        elif contact.startswith("https://t.me/"):
            buttons = [[InlineKeyboardButton("💬 Open Chat", url=contact)]]
        await q.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            disable_web_page_preview=True,
        )
        return

    if data == "ref":
        bot_username = await get_bot_username(context)
        ref_link = f"https://t.me/{bot_username}?start={u.id}"
        bonus = cfg_get("ref_bonus_spins").strip()
        txt = (
            "🔗 <b>Your Referral Link</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"Referrer bonus: <b>{esc(bonus)}</b> free spin(s)."
        )
        await q.message.reply_text(txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    if data == "spin":
        if not await is_subscribed(context, u.id):
            ch = cfg_get("required_channel").strip()
            await q.message.reply_text(
                f"🚫 You must join the required channel first:\n<code>{esc(ch)}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        gifts = [o for o in load_outcomes() if o["idx"] != 0]
        if any(not g["sticker"] for g in gifts):
            await q.message.reply_text(
                "⚠️ Some gifts are missing sticker file_id.\nAdmins must set them in the Admin Panel.",
                parse_mode=ParseMode.HTML,
            )
            return

        user = get_user(u.id)
        free_spins = int(user["free_spins"])
        paid_spins = int(user["paid_spins"])
        cost = int(cfg_get("spin_cost_paid") or "1")

        if free_spins > 0:
            used_type = "free"
        elif paid_spins >= cost:
            used_type = "paid"
        else:
            await q.message.reply_text(
                "🚫 Not enough spins.\n"
                f"Free spins: <b>{free_spins}</b>\n"
                f"Paid balance: <b>{paid_spins}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        outcomes = load_outcomes()
        outcome = pick_weighted(outcomes)

        con = db()
        cur = con.cursor()
        if used_type == "free":
            cur.execute("UPDATE users SET free_spins = free_spins - 1 WHERE user_id=?", (u.id,))
        else:
            cur.execute("UPDATE users SET paid_spins = paid_spins - ? WHERE user_id=?", (cost, u.id))
        cur.execute(
            "INSERT INTO spins(user_id, used_type, result_idx, result_name, result_sticker, created_at) VALUES(?,?,?,?,?,?)",
            (u.id, used_type, outcome["idx"], outcome["name"], outcome["sticker"] or "", datetime.utcnow().isoformat()),
        )
        con.commit()
        con.close()

        await send_spin_animation(chat_id=u.id, context=context)

        if outcome["sticker"]:
            try:
                await context.bot.send_sticker(chat_id=u.id, sticker=outcome["sticker"])
            except Exception:
                pass
            await q.message.reply_text(
                f"🎉 <b>You won!</b>\nGift: <b>{esc(outcome['name'])}</b>\nSpin type: <code>{used_type}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await q.message.reply_text(
                f"🍀 <b>Better luck next time!</b>\n{esc(outcome['name'])}\nSpin type: <code>{used_type}</code>",
                parse_mode=ParseMode.HTML,
            )
        return

    if data.startswith("admin:"):
        if not is_admin(u.id):
            await q.message.reply_text("❌ Not allowed.")
            return

        if data == "admin:menu":
            set_await(context, None)
            await render_admin_menu(update, context)
            return

        if data == "admin:setchannel":
            set_await(context, {"type": "setchannel"})
            await q.message.reply_text("📣 Send channel username starting with @ (example: @MyChannel)", parse_mode=ParseMode.HTML)
            return

        if data == "admin:setcontact":
            set_await(context, {"type": "setcontact"})
            await q.message.reply_text(
                "💬 Send the contact username (example: @YourSupport)\n"
                "Or a full link: https://t.me/YourSupport",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "admin:setdaily":
            set_await(context, {"type": "setdaily"})
            await q.message.reply_text("🗓 Send daily free spins (example: 3)", parse_mode=ParseMode.HTML)
            return

        if data == "admin:setref":
            set_await(context, {"type": "setref"})
            await q.message.reply_text("🔗 Send referral bonus spins (example: 2)", parse_mode=ParseMode.HTML)
            return

        if data == "admin:setcost":
            set_await(context, {"type": "setcost"})
            await q.message.reply_text("💰 Send paid spin cost (example: 1)", parse_mode=ParseMode.HTML)
            return

        if data == "admin:setlose":
            set_await(context, {"type": "setlose"})
            await q.message.reply_text("❌ Send lose weight (example: 999996)", parse_mode=ParseMode.HTML)
            return

        if data == "admin:gifts":
            set_await(context, None)
            await q.message.reply_text("🎁 Choose a gift to edit:", reply_markup=admin_gifts_kb())
            return

        if data.startswith("admin:setgift:"):
            try:
                idx = int(data.split(":")[-1])
                if idx not in (1, 2, 3, 4):
                    raise ValueError()
            except Exception:
                return
            set_await(context, {"type": "setgift", "idx": idx})
            await q.message.reply_text(
                f"🎁 Edit Gift {idx}\n"
                "Send 3 lines:\n<code>Name</code>\n<code>Weight</code>\n<code>sticker_file_id</code>\n"
                "Or one line separated by |",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "admin:addspins":
            set_await(context, None)
            await q.message.reply_text("➕ Choose:", reply_markup=admin_addspins_kb())
            return

        if data == "admin:addfree":
            set_await(context, {"type": "addfree"})
            await q.message.reply_text("➕ Send: user_id amount   Example: 123456 5", parse_mode=ParseMode.HTML)
            return

        if data == "admin:addpaid":
            set_await(context, {"type": "addpaid"})
            await q.message.reply_text("➕ Send: user_id amount   Example: 123456 5", parse_mode=ParseMode.HTML)
            return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    txt = (update.effective_message.text or "").strip()
    state = get_await(context)
    if not state:
        return

    if not is_admin(u.id):
        set_await(context, None)
        await update.effective_message.reply_text("❌ Not allowed.")
        return

    if txt.lower() in ("cancel", "إلغاء", "الغاء"):
        set_await(context, None)
        await update.effective_message.reply_text("✅ Cancelled.")
        return

    t = state.get("type")
    try:
        if t == "setchannel":
            if not txt.startswith("@"):
                raise ValueError("Must start with @")
            cfg_set("required_channel", txt)
            set_await(context, None)
            await update.effective_message.reply_text(f"✅ Required channel set to: {txt}")
            return

        if t == "setcontact":
            if not (txt.startswith("@") or txt.startswith("https://t.me/")):
                raise ValueError("Send @Username or https://t.me/Username")
            cfg_set("contact_username", txt)
            set_await(context, None)
            await update.effective_message.reply_text(f"✅ Contact username set to: {txt}")
            return

        if t == "setdaily":
            n = int(txt)
            if n < 0 or n > 1000000:
                raise ValueError("Invalid number")
            cfg_set("daily_free_spins", str(n))
            set_await(context, None)
            await update.effective_message.reply_text(f"✅ Daily free spins = {n}")
            return

        if t == "setref":
            n = int(txt)
            if n < 0 or n > 1000000:
                raise ValueError("Invalid number")
            cfg_set("ref_bonus_spins", str(n))
            set_await(context, None)
            await update.effective_message.reply_text(f"✅ Referral bonus = {n}")
            return

        if t == "setcost":
            n = int(txt)
            if n < 1 or n > 1000000:
                raise ValueError("Invalid number")
            cfg_set("spin_cost_paid", str(n))
            set_await(context, None)
            await update.effective_message.reply_text(f"✅ Paid spin cost = {n}")
            return

        if t == "setlose":
            n = int(txt)
            if n < 0 or n > 10**12:
                raise ValueError("Invalid number")
            cfg_set("lose_weight", str(n))
            set_await(context, None)
            await update.effective_message.reply_text(f"✅ Lose weight = {n}")
            return

        if t == "setgift":
            idx = int(state["idx"])
            parts = [p.strip() for p in txt.splitlines() if p.strip()]
            if len(parts) == 1 and "|" in parts[0]:
                parts = [p.strip() for p in parts[0].split("|")]
            if len(parts) != 3:
                raise ValueError("Send: Name / Weight / sticker_file_id")
            name, weight_s, sticker = parts
            weight = int(weight_s)
            if weight < 0:
                raise ValueError("Weight must be >= 0")
            cfg_set(f"gift{idx}_name", name)
            cfg_set(f"gift{idx}_weight", str(weight))
            cfg_set(f"gift{idx}_sticker", sticker)
            set_await(context, None)
            await update.effective_message.reply_text(f"✅ Gift {idx} updated: {name}")
            return

        if t in ("addfree", "addpaid"):
            parts = txt.split()
            if len(parts) != 2:
                raise ValueError("Format: user_id amount")
            uid = int(parts[0]); amt = int(parts[1])
            if amt <= 0:
                raise ValueError("Amount must be > 0")
            if not get_user(uid):
                raise ValueError("User not found (they must start the bot first).")
            if t == "addfree":
                add_free_spins(uid, amt)
                msg = f"✅ Added {amt} FREE spins to user {uid}"
            else:
                add_paid_spins(uid, amt)
                msg = f"✅ Added {amt} PAID balance to user {uid}"
            set_await(context, None)
            await update.effective_message.reply_text(msg)
            return

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Error: {esc(str(e))}\nType <code>cancel</code> to cancel.",
            parse_mode=ParseMode.HTML,
        )


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN env var first.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
