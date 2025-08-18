# -*- coding: utf-8 -*-
"""
Forward bot: avval manba kanalda e’lonlarni tahrirlaydi (keraksiz linklarni o‘chiradi),
pastiga TELEGRAM/INSTAGRAM so'zlarini hyperlink qilib qo‘shadi, keyin tarqatadi.
- Ish vaqti: 10:00–23:00 (Asia/Samarkand, UTC+5)
- Har siklda SOURCE kanal to‘liq o‘qiladi (eski+yangi)
- Forward taqiqlansa: manual copy (matn/media/album) fallback (HTML parse bilan)
"""

import asyncio
import logging
import os
import re
import html
from datetime import datetime, time as dt_time, timezone, timedelta
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    SessionPasswordNeededError,
    PhoneNumberBannedError,
    SessionRevokedError,
    RPCError,
)
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import (
    InputPeerEmpty,
    Channel, Chat, ChannelForbidden, ChatForbidden
)

# ====================== SETTINGS ======================
POST_DELAY_SECONDS = 8
GROUP_DELAY_SECONDS = 30
CHECK_EVERY_SECONDS = 20
REPLAY_LAST_N_GROUPS = 300   # None -> hammasi; tavsiya: 200–500

# Telegram API credentials
api_id = 16072756
api_hash = '5fc7839a0d020c256e5c901cebd21bb7'
phone = '+998335217424'
session_file = 'session_name.session'

# Manba kanal
SOURCE_CHANNEL = "https://t.me/Navoiy_uy_barcha_elonlar_bazasi"

# Qo‘shimcha target guruhlar
ADDITIONAL_GROUPS = [
    "https://t.me/Navoiy_uy_joy_kvartira_savdosi",
    "https://t.me/Navoiy_uy_joy_savdosi",
]

# Exclude (hech qachon yuborilmaydi)
EXCLUDED_TARGETS = [
    "https://t.me/Navoiy_uy_barcha_elonlar_bazasi",
    "https://t.me/Navoiy_uy_joy_kv_barcha_elonlar",
    "https://t.me/+6e1vw3QlFLg1Zjcy",
    "https://t.me/+N9nRAUGPEl43YmFi",
    "https://t.me/Navoiy_1_xona_kvartira",
    "https://t.me/Navoiy_2_xona_kvartira",
    "https://t.me/Navoiy_3_4_5_xona_kvartira",
    "https://t.me/Navoiy_ijaraga_kv_uy",
    "https://t.me/Navoiy_hovli_katedj_dacha",
]

# Imzo uchun rasmiy havolalar
SIG_TELEGRAM = "https://t.me/Navoiy_uy_barcha_elonlar_bazasi"
SIG_INSTAGRAM = "https://www.instagram.com/rieltor_farxodjon_navoiy_uyjoy?utm_source=qr&igsh=c2V0MDRvZW4zbjR5"

# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# ====================== UTILITIES ======================
def normalize_channel(value: str) -> str:
    value = value.strip()
    if value.startswith("http"):
        path = urlparse(value).path.strip("/")
        return path
    return value.lstrip("@")

def is_working_time() -> bool:
    uz_tz = timezone(timedelta(hours=5))
    now = datetime.now(uz_tz).time()
    return dt_time(10, 0) <= now <= dt_time(23, 0)

async def ensure_connection(client: TelegramClient) -> bool:
    try:
        if not client.is_connected():
            logging.info("Client disconnected. Reconnecting...")
            await client.connect()
            logging.info("Reconnected.")
        return True
    except Exception as e:
        logging.error(f"Reconnect failed: {e}")
        return False

async def resolve_excluded_ids(client: TelegramClient) -> set:
    excluded_ids = set()
    for item in EXCLUDED_TARGETS + [SOURCE_CHANNEL]:
        try:
            uname = normalize_channel(item)
            if uname.startswith("+"):
                logging.warning(f"Skip exclude (private invite not joined): {item}")
                continue
            ent = await client.get_entity(uname)
            excluded_ids.add(ent.id)
            title = getattr(ent, "title", getattr(ent, "username", str(ent.id)))
            logging.info(f"[Exclude] {title} (id={ent.id})")
        except Exception as e:
            logging.warning(f"Could not resolve excluded: {item} -> {e}")
    return excluded_ids

def group_messages(messages):
    grouped = {}
    for msg in messages:
        if not getattr(msg, "id", None):
            continue
        key = msg.grouped_id if getattr(msg, "grouped_id", None) else msg.id
        grouped.setdefault(key, []).append(msg)
    out = []
    for key in sorted(grouped.keys()):
        out.append(sorted(grouped[key], key=lambda m: m.id))
    return out

async def get_all_posts_grouped(client: TelegramClient, limit=100000):
    if not await ensure_connection(client):
        return [], None
    source_uname = normalize_channel(SOURCE_CHANNEL)
    source_ent = await client.get_entity(source_uname)
    messages = await client.get_messages(source_ent, limit=limit)
    grouped = group_messages(messages)
    if isinstance(REPLAY_LAST_N_GROUPS, int) and REPLAY_LAST_N_GROUPS > 0:
        grouped = grouped[-REPLAY_LAST_N_GROUPS:]
    logging.info(f"Fetched {len(grouped)} post groups from source.")
    return grouped, source_ent

def _is_valid_group(ent) -> bool:
    if isinstance(ent, (ChannelForbidden, ChatForbidden)):
        return False
    if isinstance(ent, Channel):
        return bool(getattr(ent, 'megagroup', False))
    if isinstance(ent, Chat):
        return True
    return False

async def get_admin_groups(client: TelegramClient, excluded_ids: set):
    if not await ensure_connection(client):
        return []
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
        limit=200, hash=0
    ))
    admin_groups, seen = [], set()
    for ent in dialogs.chats:
        try:
            if ent.id in excluded_ids: continue
            if not _is_valid_group(ent): continue
            if hasattr(ent, 'admin_rights') and ent.admin_rights:
                if ent.id not in seen:
                    admin_groups.append(ent); seen.add(ent.id)
                    logging.info(f"Admin target: {getattr(ent,'title',ent.id)} (id={ent.id})")
        except Exception:
            continue
    for username in ADDITIONAL_GROUPS:
        try:
            ent = await client.get_entity(normalize_channel(username))
            if ent.id in excluded_ids: continue
            if not _is_valid_group(ent):
                logging.info(f"Skip additional (not a group): {username}")
                continue
            if ent.id not in seen:
                admin_groups.append(ent); seen.add(ent.id)
                logging.info(f"Added additional target: {getattr(ent,'title',username)} (id={ent.id})")
        except Exception as e:
            logging.error(f"Error adding target {username}: {e}")
    return admin_groups

# ------------------ Text sanitization + signature (HTML) ------------------
# Old block lines (with or without '=' and with/without HTML link) ni tozalash
BLOCK_LINES_PAT = re.compile(
    r'(?mi)^\s*(?:<a[^>]*>)?(TELEGRAM|INSTAGRAM)(?:</a>)?\s*(?:=\s*https?://\S+)?\s*$'
)
# Eski instagram profili linki (query’lari bilan birga)
INSTAGRAM_PAT = re.compile(
    r'https?://(?:www\.)?instagram\.com/rieltor_farxodjon_navoiy_uyjoy[^\s<>\)]*',
    re.IGNORECASE
)
# Har qanday t.me linklari
TME_ANY_PAT = re.compile(r'https?://t\.me/[^\s<>\)]*', re.IGNORECASE)

def remove_unwanted_links(text: str) -> str:
    if not text:
        return ""
    # 1) eski TELEGRAM/INSTAGRAM satrlarini o'chiramiz
    text = BLOCK_LINES_PAT.sub('', text)
    # 2) eski instagram profil linklarini olib tashlaymiz
    text = INSTAGRAM_PAT.sub('', text)
    # 3) barcha t.me linklarni olib tashlaymiz (keyin faqat imzo ichida yashirin shaklda qoladi)
    text = TME_ANY_PAT.sub('', text)
    # 4) ortiqcha bo'sh satrlarni yig'amiz
    text = re.sub(r'[ \t]+\n', '\n', text)         # trailing spaces
    text = re.sub(r'\n{3,}', '\n\n', text)         # ko'p bo'sh satr -> 2 ta
    return text.strip()

def make_signature_html() -> str:
    # so'zning o'zi link: <a href="...">TELEGRAM</a>
    tel = f'<a href="{html.escape(SIG_TELEGRAM, quote=True)}">TELEGRAM</a>'
    ins = f'<a href="{html.escape(SIG_INSTAGRAM, quote=True)}">INSTAGRAM</a>'
    return f"\n\n{tel}\n{ins}"

def append_signature_html(text: str) -> str:
    """
    Matnni tozalaydi, HTML ga escape qiladi, pastiga TELEGRAM/INSTAGRAM hyperlink qo'shadi.
    4096 limitni ham hisobga oladi.
    """
    base = remove_unwanted_links(text)
    base_html = html.escape(base)  # foydalanuvchi matni HTML safe
    block = make_signature_html()

    # Agar allaqachon TELEGRAM/INSTAGRAM so'zlari link sifatida bor bo'lsa, eski blokni olib tashlashni urinamiz
    base_html = BLOCK_LINES_PAT.sub('', base_html).strip()
    new_text = (base_html + block).strip()

    # Telegram caption/text limiti ~4096
    if len(new_text) > 4096:
        room = 4096 - len(block) - 10
        body = (base_html[:max(0, room)] + ('\n...\n' if room > 3 else '')).rstrip()
        new_text = (body + block).strip()

    return new_text

async def try_edit_message_in_source(client: TelegramClient, source_ent, msg) -> bool:
    """
    Manba kanalidagi xabar/captionni joyida tahrirlaydi (HTML hyperlink bilan).
    Edit huquqi bo'lmasa False qaytadi.
    """
    try:
        orig = (msg.text or (msg.message if hasattr(msg, 'message') else "") or "")
        new_text = append_signature_html(orig)
        if new_text.strip() == (html.escape(orig).strip() if orig else ""):
            return True  # o'zgarmadi
        await client.edit_message(source_ent, msg, new_text, parse_mode='html')
        return True
    except Exception as e:
        logging.warning(f"Edit failed msg_id={msg.id}: {e}")
        return False

# ------------------ Manual copy (fallback, HTML) ------------------
async def send_msg_group_manually(client: TelegramClient, target_peer, msg_group):
    """
    Bitta group (album/yakka) xabarni qo'lda nusxa qilib yuborish:
    - Albom bo'lsa: send_file([...]) bilan (caption birinchida, HTML)
    - Aralash bo'lsa: elementma-element (media/text), HTML bilan
    """
    all_media = all(getattr(m, "media", None) for m in msg_group)

    if all_media and len(msg_group) > 1:
        files, first_text = [], ""
        for idx, m in enumerate(msg_group):
            text = (m.text or (m.message if hasattr(m, 'message') else "") or "")
            if idx == 0:
                first_text = append_signature_html(text)
            try:
                data = await client.download_media(m, file=bytes)
            except Exception as e:
                data = None
                logging.warning(f"Album item download failed, skipping media: {e}")
            if data: files.append(data)

        if files:
            try:
                await client.send_file(
                    entity=target_peer,
                    file=files,
                    caption=first_text or "",
                    parse_mode='html'
                )
            except Exception as e:
                logging.error(f"Album send_file failed: {e}")
        else:
            if first_text:
                try: await client.send_message(target_peer, first_text, parse_mode='html')
                except Exception as e: logging.error(f"Album-fallback text send failed: {e}")
        return

    # aralash yoki yakka
    for m in msg_group:
        text = (m.text or (m.message if hasattr(m, 'message') else "") or "")
        out_text = append_signature_html(text)
        if getattr(m, "media", None):
            try:
                data = await client.download_media(m, file=bytes)
                if data:
                    await client.send_file(target_peer, data, caption=out_text or "", parse_mode='html')
                else:
                    if out_text:
                        await client.send_message(target_peer, out_text, parse_mode='html')
            except Exception as e:
                logging.warning(f"Media copy failed, sending text only: {e}")
                if out_text:
                    try: await client.send_message(target_peer, out_text, parse_mode='html')
                    except Exception as e2: logging.error(f"Text send failed after media error: {e2}")
        else:
            if out_text:
                try: await client.send_message(target_peer, out_text, parse_mode='html')
                except Exception as e:
                    logging.error(f"Text send failed: {e}")

        await asyncio.sleep(POST_DELAY_SECONDS)

# ====================== MAIN LOOP ======================
async def main():
    client = TelegramClient(session_file, api_id, api_hash)

    # Session holati
    if os.path.exists(session_file):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logging.warning("Session invalid, removing...")
                os.remove(session_file)
        except Exception:
            logging.warning("Session file corrupted, removing...")
            os.remove(session_file)

    # Start (login)
    try:
        await client.start(phone)
        logging.info("Successfully connected to Telegram!")
    except SessionPasswordNeededError:
        logging.error("Two-factor authentication required.")
        return
    except PhoneNumberBannedError:
        logging.error("Phone number is banned.")
        return
    except SessionRevokedError:
        logging.error("Session revoked.")
        return
    except Exception as e:
        logging.error(f"Error connecting: {e}")
        return

    try:
        excluded_ids = await resolve_excluded_ids(client)
        logging.info(f"Excluded IDs count: {len(excluded_ids)}")

        while True:
            if not is_working_time():
                logging.info("Outside working hours. Waiting...")
                await asyncio.sleep(60)
                continue

            # 1) SOURCE (eski + yangi) guruhlar
            source_groups, source_ent = await get_all_posts_grouped(client)
            if not source_groups or not source_ent:
                logging.info("No source groups found. Waiting...")
                await asyncio.sleep(CHECK_EVERY_SECONDS)
                continue

            # 1b) Avval KANAL UZIDA tahrir (HTML hyperlink qo‘shish)
            for msg_group in source_groups:
                for m in msg_group:
                    _ = await try_edit_message_in_source(client, source_ent, m)
                    await asyncio.sleep(0.5)  # flood yumshatish
                await asyncio.sleep(0.5)

            # 2) Target guruhlar
            admin_groups = await get_admin_groups(client, excluded_ids)
            if not admin_groups:
                logging.warning("No admin groups found. Waiting...")
                await asyncio.sleep(60)
                continue

            # 3) Forward, bo'lmasa manual copy (HTML)
            for group in admin_groups:
                gid = getattr(group, "id", None)
                if gid in excluded_ids:
                    logging.info(f"Skipped excluded target at send loop: id={gid}")
                    continue
                try:
                    target_peer = await client.get_input_entity(group)
                except Exception as e:
                    logging.error(f"Target resolve failed (skip): {getattr(group,'title',gid)} -> {e}")
                    continue

                title = getattr(group, "title", getattr(group, "username", str(gid)))
                logging.info(f"⬇️ Sending ALL (edited) to GROUP {title} (id={gid})...")

                for msg_group in source_groups:
                    message_ids = [m.id for m in msg_group if getattr(m, "id", None)]
                    if not message_ids: continue

                    # Oddiy forwardga urinib ko'ramiz (kanal tahrirlansa, forward ham shu matn bilan keladi)
                    try:
                        if not await ensure_connection(client):
                            continue
                        await client.forward_messages(
                            entity=target_peer,
                            messages=message_ids,
                            from_peer=source_ent
                        )
                        logging.info(f"✅ Forwarded to GROUP {title}: {message_ids}")
                        await asyncio.sleep(POST_DELAY_SECONDS)
                        continue
                    except FloodWaitError as e:
                        logging.warning(f"FloodWait (group/forward): {e.seconds}s")
                        await asyncio.sleep(e.seconds + 5)
                    except RPCError as e:
                        logging.warning(f"Forward failed for {title}: {e} — fallback to manual copy...")

                    # Fallback: manual copy (HTML)
                    try:
                        await send_msg_group_manually(client, target_peer, msg_group)
                    except FloodWaitError as e:
                        logging.warning(f"FloodWait (group/manual): {e.seconds}s")
                        await asyncio.sleep(e.seconds + 5)
                    except Exception as e:
                        logging.error(f"Manual copy error to {title}: {e}")
                        continue

                logging.info(f"✅ Done with GROUP {title}. Waiting {GROUP_DELAY_SECONDS} sec.")
                await asyncio.sleep(GROUP_DELAY_SECONDS)

            logging.info(f"🔄 Cycle finished. Re-reading source in {CHECK_EVERY_SECONDS}s ...")
            await asyncio.sleep(CHECK_EVERY_SECONDS)

    except Exception as e:
        logging.error(f"Main loop error: {e}")
        await asyncio.sleep(60)
    finally:
        if client.is_connected():
            await client.disconnect()
            logging.info("Client disconnected.")

if __name__ == "__main__":
    asyncio.run(main())
