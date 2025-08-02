from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PhoneNumberBannedError
import asyncio
from datetime import datetime, time as dt_time
import logging

# Loglash sozlamalari
logging.basicConfig(
    filename='telegram_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# Telegram API ma'lumotlari
api_id = 16072756
api_hash = '5fc7839a0d020c256e5c901cebd21bb7'
phone = '+998335217424'

# Telegram session nomi
client = TelegramClient('session_name', api_id, api_hash)

# Kanal va guruhlar
NAVOIY_UY_JOY_CHANNEL_USERNAME = "Navoiy_uy_joy_kv_barcha_elonlar"
ADDITIONAL_GROUPS = [
    "Navoiy_uy_joy_kvartira_bozori",
    "Navoiy_uy_joy_savdosi"
]

def is_working_time():
    """23:00 dan 03:00 gacha ish vaqti (hozirgi vaqtga moslashtirildi)"""
    now = datetime.now().time()
    return dt_time(19, 0) <= now or now <= dt_time(4, 0)

async def get_admin_groups():
    """Admin bo‘lgan guruhlar va qo‘shimcha guruhlar"""
    try:
        dialogs = await client(GetDialogsRequest(
            offset_date=None,
            offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=200,
            hash=0
        ))

        admin_groups = []
        for dialog in dialogs.chats:
            if hasattr(dialog, 'admin_rights') and dialog.admin_rights:
                admin_groups.append(dialog)
                logging.info(f"Admin guruh topildi: {dialog.title}")

        for group_username in ADDITIONAL_GROUPS:
            try:
                group = await client.get_entity(group_username)
                admin_groups.append(group)
                logging.info(f"Qo‘shimcha guruh qo‘shildi: {group.title}")
            except Exception as e:
                logging.error(f"Qo‘shimcha guruhda xato: {group_username} - {str(e)}")

        return admin_groups
    except Exception as e:
        logging.error(f"Guruhlarni olishda xato: {str(e)}")
        return []

async def get_navoiy_uy_joy_posts(min_id=0, limit=100000):
    """Postlarni eng eskidan boshlab olish"""
    try:
        channel = await client.get_entity(NAVOIY_UY_JOY_CHANNEL_USERNAME)
        messages = await client.get_messages(channel, limit=limit, min_id=min_id)

        if not messages:
            logging.info("Postlar yo‘q.")
            return [], min_id

        # Guruhlangan postlarni tartib bilan ajratib olish
        grouped_posts = {}
        for msg in messages:
            if msg.grouped_id:
                grouped_posts.setdefault(msg.grouped_id, []).append(msg)
            else:
                grouped_posts[msg.id] = [msg]

        sorted_groups = []
        for group_id in sorted(grouped_posts.keys()):
            sorted_groups.append(grouped_posts[group_id])

        next_min_id = messages[0].id if messages else min_id
        logging.info(f"{len(sorted_groups)} ta post olindi, keyingi min_id: {next_min_id}")
        return sorted_groups, next_min_id
    except Exception as e:
        logging.error(f"Postlarni olishda xato: {str(e)}")
        return [], min_id

async def main():
    try:
        await client.start(phone)
        logging.info("Telegramga muvaffaqiyatli ulandik!")
    except SessionPasswordNeededError:
        logging.error("Ikki bosqichli autentifikatsiya talab qilinadi. Parolni kiriting.")
        return
    except PhoneNumberBannedError:
        logging.error("Telefon raqami bloklangan. Boshqa raqam ishlatish kerak.")
        return
    except Exception as e:
        logging.error(f"Telegramga ulanishda xato: {str(e)}")
        return

    try:
        navoiy_uy_joy_posts, next_min_id = await get_navoiy_uy_joy_posts(min_id=0)

        while True:
            if not is_working_time():
                logging.info("Ish vaqti emas. 60 soniya kutilyapti...")
                await asyncio.sleep(60)
                continue

            admin_groups = await get_admin_groups()
            if not admin_groups:
                logging.warning("Admin guruhlar topilmadi. 60 soniya kutilyapti...")
                await asyncio.sleep(60)
                continue

            for group in admin_groups:
                logging.info(f"⬇️ {group.title} ga yuborish boshlandi...")

                for group_messages in navoiy_uy_joy_posts:
                    message_ids = [msg.id for msg in group_messages if msg.id]

                    if message_ids:
                        try:
                            await client.forward_messages(group.id, message_ids, NAVOIY_UY_JOY_CHANNEL_USERNAME)
                            logging.info(f"✅ {group.title} ga yuborildi: {message_ids}")
                            await asyncio.sleep(10)
                        except FloodWaitError as e:
                            logging.warning(f"FloodWait: {e.seconds} soniya kutish")
                            await asyncio.sleep(e.seconds + 5)
                        except Exception as e:
                            logging.error(f"{group.title} ga forwardda xato: {str(e)}")
                            continue

                logging.info(f"✅ {group.title} ga barcha postlar yuborildi. 30 soniya kutish.")
                await asyncio.sleep(30)

            logging.info("🔄 Keyingi aylanaga tayyorlanmoqda...")
            navoiy_uy_joy_posts, next_min_id = await get_navoiy_uy_joy_posts(min_id=next_min_id)
            if not navoiy_uy_joy_posts:
                logging.info("Yangi postlar yo‘q. 5 daqiqa kutilyapti...")
                await asyncio.sleep(300)

    except Exception as e:
        logging.error(f"Umumiy xato: {str(e)}")
        await asyncio.sleep(60)

if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())

