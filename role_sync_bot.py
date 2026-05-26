import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_http():
    app.run(host='0.0.0.0', port=8080)

Thread(target=run_http).start()
# ===== CONFIGURATION =====
import os

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
UNB_TOKEN = os.getenv("UNB_TOKEN")
GUILD_ID = 1457641106517921824

ROLE_NAME = "Gamble God"
CASH_THRESHOLD = 10_000_000

CHECK_INTERVAL_MINUTES = 60
# =========================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def get_balance(session: aiohttp.ClientSession, user_id: int) -> int:
    """Fetch a user's TOTAL balance (cash + bank) from UnbelievaBoat API."""
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{user_id}"
    headers = {
        "Authorization": UNB_TOKEN,
        "Accept": "application/json"
    }

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("total", 0)
            elif response.status == 429:
                data = await response.json()
                wait = data.get("retry_after", 5)
                print(f"Rate limited, waiting {wait}s...")
                await asyncio.sleep(wait)
                return await get_balance(session, user_id)
            else:
                return 0
    except Exception:
        return 0


async def update_role(session: aiohttp.ClientSession, member: discord.Member):
    balance = await get_balance(session, member.id)
    role = discord.utils.get(member.guild.roles, name=ROLE_NAME)

    if not role:
        return

    has_role = role in member.roles

    if balance >= CASH_THRESHOLD and not has_role:
        await member.add_roles(role)
        print(f"Gave Gamble God to {member.name} (${balance:,})")
    elif balance < CASH_THRESHOLD and has_role:
        await member.remove_roles(role)
        print(f"Removed Gamble God from {member.name} (${balance:,})")


@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def sync_all_members():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    members = [m for m in guild.members if not m.bot]
    print(f"Starting sync for {len(members)} members...")

    async with aiohttp.ClientSession() as session:
        for i, member in enumerate(members):
            try:
                await update_role(session, member)
                await asyncio.sleep(2)
                if (i + 1) % 100 == 0:
                    print(f"Synced {i+1}/{len(members)}...")
            except Exception as e:
                print(f"Failed {member.name}: {e}")

    print("Sync complete.")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    sync_all_members.start()


@bot.command(name="check")
async def check_balance(ctx):
    async with aiohttp.ClientSession() as session:
        balance = await get_balance(session, ctx.author.id)
        await update_role(session, ctx.author)

    if balance >= CASH_THRESHOLD:
        await ctx.send(f"Your total balance is ${balance:,}. You are a Gamble God!")
    else:
        await ctx.send(f"Your total balance is ${balance:,}. You need ${CASH_THRESHOLD - balance:,} more for Gamble God.")


@bot.command(name="syncall")
@commands.has_permissions(administrator=True)
async def sync_all_command(ctx):
    await ctx.send("Syncing all members... this will take a while!")
    await sync_all_members()
    await ctx.send("Done!")


bot.run(DISCORD_TOKEN)