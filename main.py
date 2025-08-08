from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PhoneNumberBannedError, SessionRevokedError
import asyncio
from datetime import datetime, time as dt_time, timezone, timedelta
import logging
import os
from telethon import events

# Logging configuration (console only)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# Telegram API credentials
api_id = 16072756
api_hash = '5fc7839a0d020c256e5c901cebd21bb7'
phone = '+998335217424'
session_file = 'session_name.session'

# Telegram channel and groups
NAVOIY_UY_JOY_CHANNEL_USERNAME = "Navoiy_uy_joy_kv_barcha_elonlar"
ADDITIONAL_GROUPS = [
    "Navoiy_uy_joy_kvartira_bozori",
    "Navoiy_uy_joy_savdosi"
]

def is_working_time():
    """Check if current time is within working hours (10:00–22:00 Uzbekistan time, UTC+5)"""
    uz_timezone = timezone(timedelta(hours=5))
    now = datetime.now(uz_timezone).time()
    return dt_time(10, 0) <= now <= dt_time(23, 0)

async def ensure_connection(client):
    """Ensure the client is connected, reconnect if necessary"""
    try:
        if not client.is_connected():
            logging.info("Client is disconnected. Attempting to reconnect...")
            await client.connect()
            logging.info("Reconnected successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to reconnect: {str(e)}")
        return False

async def get_admin_groups(client):
    """Fetch groups where the user is an admin and additional groups"""
    try:
        if not await ensure_connection(client):
            raise Exception("Client is not connected")

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
                logging.info(f"Found admin group: {dialog.title}")

        for group_username in ADDITIONAL_GROUPS:
            try:
                group = await client.get_entity(group_username)
                admin_groups.append(group)
                logging.info(f"Added additional group: {group.title}")
            except Exception as e:
                logging.error(f"Error adding group {group_username}: {str(e)}")

        return admin_groups
    except FloodWaitError as e:
        logging.warning(f"FloodWaitError in get_admin_groups: Waiting {e.seconds} seconds")
        await asyncio.sleep(e.seconds + 5)
        return []
    except Exception as e:
        logging.error(f"Error fetching groups: {str(e)}")
        return []

async def get_navoiy_uy_joy_posts(client, min_id=0, limit=100000):
    """Fetch posts from the source channel starting from min_id"""
    try:
        if not await ensure_connection(client):
            raise Exception("Client is not connected")

        channel = await client.get_entity(NAVOIY_UY_JOY_CHANNEL_USERNAME)
        messages = await client.get_messages(channel, limit=limit, min_id=min_id)

        if not messages:
            logging.info("No posts found.")
            return [], min_id

        # Group posts by grouped_id
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
        logging.info(f"Fetched {len(sorted_groups)} post groups, next min_id: {next_min_id}")
        return sorted_groups, next_min_id
    except FloodWaitError as e:
        logging.warning(f"FloodWaitError in get_navoiy_uy_joy_posts: Waiting {e.seconds} seconds")
        await asyncio.sleep(e.seconds + 5)
        return [], min_id
    except Exception as e:
        logging.error(f"Error fetching posts: {str(e)}")
        return [], min_id

async def send_tekin_reklama_message(client, group, message_text="Tekin Reklama"):
    """Send a message with Tekin Reklama button to a group"""
    try:
        if not await ensure_connection(client):
            raise Exception("Client is not connected")
        
        # Create inline keyboard with Tekin Reklama button
        keyboard = [
            [InlineKeyboardButton("🆓 Tekin Reklama", callback_data="tekin_reklama")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send message with inline keyboard
        await client.send_message(group.id, message_text, buttons=reply_markup)
        logging.info(f"✅ Sent Tekin Reklama message to {group.title}")
        
    except FloodWaitError as e:
        logging.warning(f"FloodWaitError in send_tekin_reklama_message: Waiting {e.seconds} seconds")
        await asyncio.sleep(e.seconds + 5)
    except Exception as e:
        logging.error(f"Error sending Tekin Reklama message to {group.title}: {str(e)}")

async def handle_tekin_reklama_callback(client, callback_query):
    """Handle callback when Tekin Reklama button is pressed"""
    try:
        user_id = callback_query.from_id
        chat_id = callback_query.chat_id
        
        # Send response message
        response_text = "🎉 Tabriklaymiz! Sizning reklamangiz tekin yuborildi.\n\n📝 Reklama qoidalari:\n• Maxsus xizmatlar uchun to'lov talab qilinadi\n• Oddiy e'lonlar bepul\n• Spam va nojo'ya kontent taqiqlanadi"
        
        await client.send_message(chat_id, response_text)
        logging.info(f"✅ Handled Tekin Reklama callback from user {user_id}")
        
    except Exception as e:
        logging.error(f"Error handling Tekin Reklama callback: {str(e)}")

async def send_periodic_tekin_reklama(client, admin_groups):
    """Send Tekin Reklama message to all admin groups periodically"""
    try:
        for group in admin_groups:
            try:
                tekin_message = """🆓 **Tekin Reklama Xizmati**

📢 O'zingizning e'loningizni bepul joylashtiring!
💡 Maxsus xizmatlar uchun admin bilan bog'laning.

🎯 **Xizmatlar:**
• Bepul e'lonlar
• Maxsus joylashtirish
• Yuqori ko'rsatkichlar

📞 Bog'lanish: @admin_username"""
                
                await send_tekin_reklama_message(client, group, tekin_message)
                await asyncio.sleep(5)  # Small delay between groups
                
            except Exception as e:
                logging.error(f"Error sending Tekin Reklama to {group.title}: {str(e)}")
                continue
                
    except Exception as e:
        logging.error(f"Error in send_periodic_tekin_reklama: {str(e)}")

async def main():
    # Initialize Telegram client
    client = TelegramClient(session_file, api_id, api_hash)

    # Check and clean session file if corrupted
    if os.path.exists(session_file):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logging.warning("Session is invalid. Removing and recreating...")
                os.remove(session_file)
        except Exception:
            logging.warning("Session file is corrupted. Removing...")
            os.remove(session_file)

    try:
        await client.start(phone)
        logging.info("Successfully connected to Telegram!")
        
        # Add callback query handler for Tekin Reklama button
        @client.on(events.CallbackQuery)
        async def callback_handler(event):
            if event.data == b'tekin_reklama':
                await handle_tekin_reklama_callback(client, event)
        
    except SessionPasswordNeededError:
        logging.error("Two-factor authentication required. Please provide the password.")
        return
    except PhoneNumberBannedError:
        logging.error("Phone number is banned. Please use another number.")
        return
    except SessionRevokedError:
        logging.error("Session revoked. Please remove session file and re-authenticate.")
        return
    except Exception as e:
        logging.error(f"Error connecting to Telegram: {str(e)}")
        return

    try:
        navoiy_uy_joy_posts, next_min_id = await get_navoiy_uy_joy_posts(client, min_id=0)

        while True:
            if not is_working_time():
                logging.info("Outside working hours. Waiting 60 seconds...")
                await asyncio.sleep(60)
                continue

            admin_groups = await get_admin_groups(client)
            if not admin_groups:
                logging.warning("No admin groups found. Waiting 60 seconds...")
                await asyncio.sleep(60)
                continue

            for group in admin_groups:
                logging.info(f"⬇️ Forwarding to {group.title}...")

                for group_messages in navoiy_uy_joy_posts:
                    message_ids = [msg.id for msg in group_messages if msg.id]

                    if message_ids:
                        try:
                            if not await ensure_connection(client):
                                logging.error("Cannot forward messages: Client disconnected")
                                continue
                            await client.forward_messages(group.id, message_ids, NAVOIY_UY_JOY_CHANNEL_USERNAME)
                            logging.info(f"✅ Forwarded to {group.title}: {message_ids}")
                            await asyncio.sleep(10)  # Avoid flooding
                        except FloodWaitError as e:
                            logging.warning(f"FloodWait: Waiting {e.seconds} seconds")
                            await asyncio.sleep(e.seconds + 5)
                        except Exception as e:
                            logging.error(f"Error forwarding to {group.title}: {str(e)}")
                            continue

                # Send Tekin Reklama message to each group
                try:
                    tekin_message = "🆓 **Tekin Reklama Xizmati**\n\n📢 O'zingizning e'loningizni bepul joylashtiring!\n💡 Maxsus xizmatlar uchun admin bilan bog'laning."
                    await send_tekin_reklama_message(client, group, tekin_message)
                    await asyncio.sleep(5)  # Small delay between messages
                except Exception as e:
                    logging.error(f"Error sending Tekin Reklama to {group.title}: {str(e)}")

                logging.info(f"✅ Completed forwarding to {group.title}. Waiting 30 seconds.")
                await asyncio.sleep(30)

            logging.info("🔄 Preparing for next cycle...")
            navoiy_uy_joy_posts, next_min_id = await get_navoiy_uy_joy_posts(client, min_id=next_min_id)
            if not navoiy_uy_joy_posts:
                logging.info("No new posts. Waiting 5 minutes...")
                await asyncio.sleep(300)
            
            # Send periodic Tekin Reklama messages every 6 hours
            current_hour = datetime.now().hour
            if current_hour % 6 == 0:  # Every 6 hours
                logging.info("📢 Sending periodic Tekin Reklama messages...")
                await send_periodic_tekin_reklama(client, admin_groups)
                logging.info("✅ Completed sending periodic Tekin Reklama messages")

    except Exception as e:
        logging.error(f"Main loop error: {str(e)}")
        await asyncio.sleep(60)
    finally:
        if client.is_connected():
            await client.disconnect()
            logging.info("Client disconnected gracefully.")

if __name__ == "__main__":
    asyncio.run(main())
