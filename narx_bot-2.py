"""
USDT / TON / Telegram Stars narx va kalkulyator boti
- TON/USD: Binance/OKX/Bybit, USDT/so'm: CoinGecko — ikkalasi har so'rovda jonli
- Stars: 1 Star = 0.015 USD (qat'iy), so'mga USDT kursi bo'yicha hisoblanadi.

O'rnatish:  pip install aiogram aiohttp
Ishga tushirish:  BOT_TOKEN="123:ABC" python narx_bot.py
"""
import asyncio
import json
import logging
import os
import re
import time
from html import unescape

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

BOT_TOKEN = os.getenv("BOT_TOKEN", "BOT_TOKEN_NI_SHU_YERGA_YOZING")
ADMIN_ID = 8437153691
USERS_FILE = "users.json"
UPDATE_EVERY = 600  # 10 daqiqa (soniyada)
STAR_USD = 0.015  # 1 Star = 0.015 USD (qat'iy belgilangan)

COINGECKO = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=tether,the-open-network&vs_currencies=usd,uzs"
)
FRAGMENT_STARS = "https://fragment.com/stars"

# Umumiy narxlar keshi
PRICES = {
    "usdt_uzs": None,
    "usdt_usd": 1.0,
    "ton_ts": 0,
    "ton_time": None,
    "ton_usd": None,
    "star_usd": STAR_USD,
    "star_source": "$0.015 qat'iy",
    "usdt_ts": 0,
    "usdt_time": None,
    "updated": None,
}


USDT_CACHE_SEC = 10  # CoinGecko bepul limiti uchun (~6 so'rov/daqiqa)
CBU_USD = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/"
_usdt_lock = asyncio.Lock()


async def refresh_usdt():
    """USDT/so'm kursini har so'rovda jonli oladi (CoinGecko -> CBU zaxira)."""
    async with _usdt_lock:
        if time.time() - PRICES["usdt_ts"] < USDT_CACHE_SEC:
            return
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(COINGECKO, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    r.raise_for_status()
                    d = await r.json()
                PRICES["usdt_uzs"] = d["tether"]["uzs"]
                PRICES["usdt_usd"] = d["tether"]["usd"]
                PRICES["usdt_ts"] = time.time()
                PRICES["usdt_time"] = time.strftime("%H:%M:%S") + " · CoinGecko"
                if time.time() - PRICES["ton_ts"] > 60:  # TON jonli manbasi ishlamasa
                    PRICES["ton_usd"] = d["the-open-network"]["usd"]
                    PRICES["ton_time"] = time.strftime("%H:%M:%S") + " · CoinGecko (zaxira)"
                return
            except Exception as e:
                logging.warning("CoinGecko ishlamadi: %s", e)
            try:
                async with session.get(CBU_USD, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    r.raise_for_status()
                    rate = float((await r.json())[0]["Rate"])
                PRICES["usdt_uzs"] = rate
                PRICES["usdt_usd"] = 1.0
                PRICES["usdt_ts"] = time.time()
                PRICES["usdt_time"] = time.strftime("%H:%M:%S") + " · MB rasmiy kursi (zaxira)"
            except Exception as e:
                logging.warning("CBU ishlamadi: %s", e)


LIVE_SOURCES = [
    ("Binance", "https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT",
     lambda d: float(d["price"])),
    ("OKX", "https://www.okx.com/api/v5/market/ticker?instId=TON-USDT",
     lambda d: float(d["data"][0]["last"])),
    ("Bybit", "https://api.bybit.com/v5/market/tickers?category=spot&symbol=TONUSDT",
     lambda d: float(d["result"]["list"][0]["lastPrice"])),
]
LIVE_CACHE_SEC = 3  # bir vaqtda ko'p foydalanuvchi bo'lsa ham API'ni ortiqcha urmaslik uchun
_live_lock = asyncio.Lock()


async def refresh_ton():
    """TON/USD narxini har so'rovda jonli oladi (Binance -> OKX -> Bybit)."""
    async with _live_lock:
        if time.time() - PRICES["ton_ts"] < LIVE_CACHE_SEC:
            return
        async with aiohttp.ClientSession() as session:
            for name, url, getter in LIVE_SOURCES:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                        r.raise_for_status()
                        price = getter(await r.json())
                    if price > 0:
                        PRICES["ton_usd"] = price
                        PRICES["ton_ts"] = time.time()
                        PRICES["ton_time"] = time.strftime("%H:%M:%S") + f" · {name}"
                        return
                except Exception as e:
                    logging.warning("%s ishlamadi: %s", name, e)


async def fetch_stars(session: aiohttp.ClientSession):
    """Fragment sahifasidan '<N> Stars ... $X' ko'rinishini qidiradi (best-effort)."""
    headers = {"User-Agent": "Mozilla/5.0"}
    async with session.get(FRAGMENT_STARS, headers=headers, timeout=20) as r:
        r.raise_for_status()
        html = await r.text()
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"([\d,]+)\s*Stars.{0,120}?\$\s*([\d.]+)", text)
    if not m:
        raise ValueError("Fragment sahifasidan narx topilmadi")
    stars = int(m.group(1).replace(",", ""))
    usd = float(m.group(2))
    if stars <= 0 or usd <= 0:
        raise ValueError("Noto'g'ri qiymat")
    PRICES["star_usd"] = usd / stars
    PRICES["star_source"] = "fragment.com"


async def refresh_all():
    """Ikkala narxni parallel yangilaydi (TON/USD va USDT/so'm)."""
    await asyncio.gather(refresh_ton(), refresh_usdt())


def fmt(n, d=2):
    return f"{n:,.{d}f}".replace(",", " ")


def ready():
    return PRICES["usdt_uzs"] is not None


def price_text():
    if not ready():
        return "⏳ Narxlar hali yuklanmoqda, biroz kuting..."
    star_uzs = PRICES["star_usd"] * PRICES["usdt_uzs"]
    return (
        "📊 <b>Joriy narxlar</b>\n\n"
        f"💵 1 USDT = <b>{fmt(PRICES['usdt_uzs'])}</b> so'm\n"
        f"💎 1 TON = <b>{fmt(PRICES['ton_usd'] * PRICES['usdt_uzs'] / PRICES['usdt_usd'], 0)}</b> so'm "
        f"(${fmt(PRICES['ton_usd'], 3)})\n"
        f"⭐️ 1 Star = <b>{fmt(star_uzs)}</b> so'm "
        f"(${fmt(PRICES['star_usd'], 4)}) — <i>{PRICES['star_source']}</i>\n\n"
        f"⚡️ TON (jonli): {PRICES['ton_time']}\n"
        f"🕒 USDT/so'm (jonli): {PRICES['usdt_time']}\n"
        "🔄 USDT/so'm va TON/USD har so'rovda jonli yangilanadi"
    )


# Kalkulyator: har bir valyutani USD ga o'tkazish koeffitsiyenti
def to_usd(amount, cur):
    return {
        "usdt": amount * PRICES["usdt_usd"],
        "ton": amount * PRICES["ton_usd"],
        "star": amount * PRICES["star_usd"],
        "uzs": amount * PRICES["usdt_usd"] / PRICES["usdt_uzs"],
    }[cur]


def from_usd(usd, cur):
    return {
        "usdt": usd / PRICES["usdt_usd"],
        "ton": usd / PRICES["ton_usd"],
        "star": usd / PRICES["star_usd"],
        "uzs": usd * PRICES["usdt_uzs"] / PRICES["usdt_usd"],
    }[cur]


ALIASES = {
    "usdt": "usdt", "usd": "usdt", "dollar": "usdt", "$": "usdt",
    "ton": "ton", "gram": "ton", "toncoin": "ton",
    "star": "star", "stars": "star", "stur": "star", "yulduz": "star", "⭐️": "star", "⭐": "star",
    "uzs": "uzs", "som": "uzs", "so'm": "uzs", "sum": "uzs",
}
NAMES = {"usdt": "USDT", "ton": "TON", "star": "⭐️ Stars", "uzs": "so'm"}


def parse(part: str):
    """'1 ton', '7 gram', '123 usd', '2300 stars', 'ton', '$100' -> (miqdor, valyuta)"""
    t = part.lower().strip()
    t = re.sub(r"^\$\s*([\d.,\s]+)$", r"\1 usd", t)
    m = re.match(r"^([\d\s.,]*?)\s*([^\d\s.,].*?)$", t)
    if not m:
        return None
    num = m.group(1).replace(" ", "").replace(",", ".")
    try:
        amount = float(num) if num else 1.0
    except ValueError:
        return None
    cur = ALIASES.get(m.group(2).strip())
    return (amount, cur) if cur else None


def split_items(text: str):
    """Bir xabarda bir nechta narsa: vergul, nuqta-vergul, yangi qator yoki 'va' bilan."""
    parts = re.split(r"(?<=\D),|;|\n|\bva\b", text)
    return [p.strip() for p in parts if p.strip()]


def line_for(amount: float, cur: str) -> str:
    """Faqat aytilgan narsa haqida bitta qator."""
    usd = to_usd(amount, cur)
    uzs = from_usd(usd, "uzs")
    if cur == "uzs":
        return f"🇺🇿 <b>{fmt(amount, 0)}</b> so'm = <b>${fmt(usd)}</b>"
    icon = {"usdt": "💵", "ton": "💎", "star": "⭐️"}[cur]
    label = {"usdt": "USD", "ton": "TON", "star": "Stars"}[cur]
    a = fmt(amount, 4).rstrip("0").rstrip(".") if amount % 1 else fmt(amount, 0)
    if cur == "usdt":
        return f"{icon} <b>{a}</b> {label} = <b>{fmt(uzs, 0)}</b> so'm"
    return f"{icon} <b>{a}</b> {label} = <b>{fmt(uzs, 0)}</b> so'm (${fmt(usd)})"


HELP = (
    "👋 <b>Salom!</b> USDT, TON va Telegram Stars narxi hamda kalkulyator boti.\n\n"
    "/narx — joriy narxlar\n"
    "/help — yordam\n\n"
    "🧮 <b>Hisoblash:</b> shunchaki yozing:\n"
    "<code>100 usdt</code>\n<code>5 ton</code>\n<code>500 stars</code>\n"
    "<code>1000000 som</code>"
)

dp = Dispatcher()


def load_users():
    try:
        with open(USERS_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


USERS = load_users()


def add_user(uid: int):
    if uid not in USERS:
        USERS.add(uid)
        with open(USERS_FILE, "w") as f:
            json.dump(list(USERS), f)


@dp.message(Command("stat"), F.from_user.id == ADMIN_ID)
async def stat(m: Message):
    await m.answer(f"👥 Foydalanuvchilar: {len(USERS)}\n🕒 USDT: {PRICES['usdt_time']}\n💎 TON: {PRICES['ton_time']}")


@dp.message(Command("send"), F.from_user.id == ADMIN_ID)
async def send_all(m: Message, bot: Bot):
    text = m.text.partition(" ")[2].strip()
    if not text:
        return await m.answer("Foydalanish: /send xabar matni")
    ok = 0
    for uid in list(USERS):
        try:
            await bot.send_message(uid, text)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await m.answer(f"✅ Yuborildi: {ok}/{len(USERS)}")


@dp.message(Command("start", "help"))
async def start(m: Message):
    add_user(m.from_user.id)
    await m.answer(HELP, parse_mode="HTML")


@dp.message(Command("narx", "price"))
async def narx(m: Message):
    await refresh_all()
    await m.answer(price_text(), parse_mode="HTML")


@dp.message(F.text)
async def calc(m: Message):
    add_user(m.from_user.id)
    await refresh_all()
    if not ready():
        return await m.answer("⚠️ Narx manbalariga ulanib bo'lmadi, birozdan so'ng urinib ko'ring.")
    lines, bad = [], []
    for part in split_items(m.text):
        p = parse(part)
        if p:
            lines.append(line_for(*p))
        else:
            bad.append(part)
    if not lines:
        return await m.answer(
            "❓ Masalan: <code>1 ton</code>, <code>7 gram</code>, <code>123 usd</code>, "
            "<code>2300 stars</code>\nBir nechtasini vergul bilan yozing: "
            "<code>1 ton, 123 usd, 2300 stars</code>", parse_mode="HTML")
    text = "\n".join(lines) + f"\n\n🕒 TON: {PRICES['ton_time']}\n🕒 USDT/so'm: {PRICES['usdt_time']}"
    if bad:
        text += "\n⚠️ Tushunilmadi: " + ", ".join(bad)
    await m.answer(text, parse_mode="HTML")


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(BOT_TOKEN)
    await refresh_all()  # ishga tushganda narxlarni oldindan yuklash
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
