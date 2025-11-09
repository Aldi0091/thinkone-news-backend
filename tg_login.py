import os
from telethon import TelegramClient
from dotenv import load_dotenv
load_dotenv()

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")

client = TelegramClient("tg", api_id, api_hash)

async def main():
    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username})")

with client:
    client.loop.run_until_complete(main())
