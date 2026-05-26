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
GAMBLER_ROLE_NAME = "The Gambler"
LOG_CHANNEL_NAME = "gamble-god-logs"
CASH_THRESHOLD = 10_000_000

CHECK_INTERVAL_MINUTES = 5
# =========================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error


async def send_log(message: str):
    """Send a message to the log channel."""
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if channel:
        await channel.send(message)


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
        msg = f"🟢 **{member.mention}** got Gamble God! (${balance:,})"
        print(msg)
        await send_log(msg)
    elif balance < CASH_THRESHOLD and has_role:
        await member.remove_roles(role)
        msg = f"🔴 **{member.mention}** lost Gamble God. (${balance:,})"
        print(msg)
        await send_log(msg)


@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def sync_gamblers():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    gambler_role = discord.utils.get(guild.roles, name=GAMBLER_ROLE_NAME)
    if not gambler_role:
        print(f"Role '{GAMBLER_ROLE_NAME}' not found!")
        return

    gamblers = [m for m in guild.members if gambler_role in m.roles and not m.bot]
    
    await send_log(f"🔄 Starting sync for {len(gamblers)} gamblers...")
    print(f"Syncing {len(gamblers)} gamblers...")

    async with aiohttp.ClientSession() as session:
        for i, member in enumerate(gamblers):
            try:
                await update_role(session, member)
                await asyncio.sleep(0.5)
                if (i + 1) % 50 == 0:
                    print(f"Synced {i+1}/{len(gamblers)}...")
            except Exception as e:
                print(f"Failed {member.name}: {e}")

    await send_log(f"✅ Sync complete! {len(gamblers)} gamblers checked.")
    print("Gambler sync complete.")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await send_log("🚀 Bot is online and watching balances!")
    sync_gamblers.start()


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
    await ctx.send("Syncing all gamblers...")
    await sync_gamblers()
    await ctx.send("Done!")


bot.run(DISCORD_TOKEN)