import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import re
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

# Role IDs
ROLE_ID = 1482180530882482236  # Gamble God
GAMBLER_ROLE_ID = 1483193995223105606  # The Gambler
GAMBLE_SUPERVISOR_ROLE_ID = 1514096033125109760  # Gamble Supervisor
ECONOMY_MANAGER_ROLE_ID = 1494014417841422447  # Economy Manager
ECO_BLACKLIST_ROLE_ID = 1511686789012914237  # Economy Blacklist
LOAN_BLACKLIST_ROLE_ID = 1513096621934772325  # Loan Blacklist

LOG_CHANNEL_NAME = "gamble-god-logs"
CMD_LOG_CHANNEL_NAME = "ninja-bot-logs"
CASH_THRESHOLD = 10_000_000

CHECK_INTERVAL_MINUTES = 5
# =========================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def has_any_role_by_id(*role_ids):
    """Custom check: user must have at least one of the given role IDs."""
    async def predicate(ctx):
        if not ctx.author.guild:
            return False
        for role_id in role_ids:
            role = ctx.author.guild.get_role(role_id)
            if role and role in ctx.author.roles:
                return True
        raise commands.MissingRole([str(rid) for rid in role_ids])
    return commands.check(predicate)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRole):
        await ctx.send("❌ You don't have permission to use this command.")
        return
    raise error


async def send_log_embed(title: str, description: str, color: int):
    """Send an embed to the gamble-god-logs channel."""
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if channel:
        embed = discord.Embed(title=title, description=description, color=color)
        await channel.send(embed=embed)


async def send_cmd_log(title: str, description: str, color: int):
    """Send an embed to the ninja-bot-logs channel."""
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    channel = discord.utils.get(guild.text_channels, name=CMD_LOG_CHANNEL_NAME)
    if channel:
        embed = discord.Embed(title=title, description=description, color=color)
        await channel.send(embed=embed)


async def get_balance(session: aiohttp.ClientSession, user_id: int) -> int:
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
    role = member.guild.get_role(ROLE_ID)

    if not role:
        return

    has_role = role in member.roles

    if balance >= CASH_THRESHOLD and not has_role:
        await member.add_roles(role)
        print(f"Gave Gamble God to {member.name} (${balance:,})")
        await send_log_embed(
            title="🟢 Gamble God Assigned",
            description=f"**{member.name}** now has Gamble God!\nBalance: ${balance:,}",
            color=0x00ff00
        )
    elif balance < CASH_THRESHOLD and has_role:
        await member.remove_roles(role)
        print(f"Removed Gamble God from {member.name} (${balance:,})")
        await send_log_embed(
            title="🔴 Gamble God Removed",
            description=f"**{member.name}** lost Gamble God.\nBalance: ${balance:,}",
            color=0xff0000
        )
    
    return balance


async def reverse_payment(session: aiohttp.ClientSession, from_id: int, to_id: int, amount: int):
    """Take money from the receiver and return it to the sender."""
    # Get receiver's current cash
    url_get = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{to_id}"
    headers = {"Authorization": UNB_TOKEN, "Accept": "application/json"}
    
    try:
        async with session.get(url_get, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                receiver_cash = data.get("cash", 0)
            else:
                return False

        # Remove the transferred amount from receiver
        new_cash = max(0, receiver_cash - amount)
        url_put = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{to_id}"
        async with session.put(url_put, headers=headers, json={"cash": new_cash}) as resp:
            if resp.status != 200:
                return False

        # Add it back to sender
        async with session.get(f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{from_id}", headers=headers) as resp:
            if resp.status == 200:
                sender_data = await resp.json()
                sender_cash = sender_data.get("cash", 0)
            else:
                return False

        new_sender_cash = sender_cash + amount
        async with session.put(f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{from_id}", headers=headers, json={"cash": new_sender_cash}) as resp:
            if resp.status == 200:
                return True
        return False
    except Exception:
        return False


@bot.event
async def on_message(message):
    # Don't process own messages or DMs
    if message.author.bot or not message.guild:
        return
    
    # Check if message starts with payment commands
    content = message.content.strip()
    pay_patterns = [r'^!pay\s', r'^!give\s', r'^!givemoney\s']
    
    is_payment = False
    for pattern in pay_patterns:
        if re.match(pattern, content, re.IGNORECASE):
            is_payment = True
            break
    
    if not is_payment:
        # Process commands normally
        await bot.process_commands(message)
        return

    # Extract mentioned users
    mentioned = message.mentions
    if not mentioned:
        await bot.process_commands(message)
        return

    receiver = mentioned[0]
    
    # Check if receiver has Loan Blacklist
    loan_role = message.guild.get_role(LOAN_BLACKLIST_ROLE_ID)
    if not loan_role or loan_role not in receiver.roles:
        await bot.process_commands(message)
        return

    # Extract amount from message
    amount_match = re.search(r'(\d+)', content)
    if not amount_match:
        await bot.process_commands(message)
        return
    
    amount = int(amount_match.group(1))

    # Block the payment and reverse it
    async with aiohttp.ClientSession() as session:
        # Wait a moment for UnbelievaBoat to process first
        await asyncio.sleep(1)
        
        success = await reverse_payment(session, message.author.id, receiver.id, amount)
    
    if success:
        await message.channel.send(
            f"🚫 **Payment Blocked!** {receiver.mention} is Loan Blacklisted.\n"
            f"${amount:,} has been returned to {message.author.mention}."
        )
        await send_cmd_log(
            title="🚫 Payment Blocked",
            description=f"**{message.author.name}** tried to send ${amount:,} to **{receiver.name}** (Loan Blacklisted)\nMoney returned.",
            color=0xff0000
        )
    else:
        await message.channel.send(
            f"⚠️ **Warning!** {receiver.mention} is Loan Blacklisted but payment reversal failed. Staff please check."
        )
        await send_cmd_log(
            title="⚠️ Reversal Failed",
            description=f"**{message.author.name}** sent ${amount:,} to **{receiver.name}** (Loan Blacklisted). Could not reverse!",
            color=0xff0000
        )


@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def sync_gamblers():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    gambler_role = guild.get_role(GAMBLER_ROLE_ID)
    if not gambler_role:
        print(f"Role ID '{GAMBLER_ROLE_ID}' not found!")
        return

    gamblers = [m for m in guild.members if gambler_role in m.roles and not m.bot]
    
    await send_log_embed(
        title="🔄 Sync Started",
        description=f"Checking {len(gamblers)} gamblers...",
        color=0x3498db
    )
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

    await send_log_embed(
        title="✅ Sync Complete",
        description=f"{len(gamblers)} gamblers checked.",
        color=0x00ff00
    )
    print("Gambler sync complete.")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await send_log_embed(
        title="🚀 Bot Online",
        description="Bot is online and watching balances!",
        color=0x9b59b6
    )
    sync_gamblers.start()


@bot.command(name="check")
async def check_balance(ctx):
    async with aiohttp.ClientSession() as session:
        balance = await get_balance(session, ctx.author.id)
        await update_role(session, ctx.author)

    gambler_role = ctx.guild.get_role(GAMBLER_ROLE_ID)
    if gambler_role and gambler_role not in ctx.author.roles:
        await ctx.author.add_roles(gambler_role)
        await send_log_embed(
            title="🎰 Gambler Role Assigned",
            description=f"**{ctx.author.name}** got The Gambler role via `!check`",
            color=0xf1c40f
        )

    if balance >= CASH_THRESHOLD:
        await ctx.send(f"Your total balance is ${balance:,}. You are a Gamble God!")
    else:
        await ctx.send(f"Your total balance is ${balance:,}. You need ${CASH_THRESHOLD - balance:,} more for Gamble God.")


@bot.command(name="syncall")
@commands.has_permissions(administrator=True)
async def sync_all_command(ctx):
    await ctx.send("Syncing all gamblers...")
    await send_cmd_log(
        title="🔄 Sync All",
        description=f"**{ctx.author.name}** triggered a full sync.",
        color=0x3498db
    )
    await sync_gamblers()
    await ctx.send("Done!")


@bot.command(name="forcecheck")
@has_any_role_by_id(GAMBLE_SUPERVISOR_ROLE_ID, ECONOMY_MANAGER_ROLE_ID)
async def force_check(ctx, member: discord.Member):
    """Gamble Supervisor+: Check a specific user's balance and update their roles."""
    async with aiohttp.ClientSession() as session:
        balance = await update_role(session, member)

    await ctx.send(f"**{member.name}** total balance: ${balance:,} — roles updated.")


@bot.command(name="purgegods")
@has_any_role_by_id(ECONOMY_MANAGER_ROLE_ID)
async def purge_gods(ctx):
    """Economy Manager only: Remove Gamble God from everyone under $10M."""
    guild = ctx.guild
    god_role = guild.get_role(ROLE_ID)
    
    if not god_role:
        await ctx.send("❌ Gamble God role not found.")
        return

    gods = [m for m in guild.members if god_role in m.roles and not m.bot]
    await ctx.send(f"🔍 Checking {len(gods)} Gamble Gods...")
    
    removed = 0
    async with aiohttp.ClientSession() as session:
        for member in gods:
            balance = await get_balance(session, member.id)
            if balance < CASH_THRESHOLD:
                await member.remove_roles(god_role)
                removed += 1
                print(f"Purged Gamble God from {member.name} (${balance:,})")
                await send_log_embed(
                    title="🧹 Gamble God Purged",
                    description=f"**{member.name}** lost Gamble God.\nBalance: ${balance:,}",
                    color=0xff0000
                )
            await asyncio.sleep(0.5)

    await ctx.send(f"✅ Purge complete! Removed Gamble God from {removed} users.")
    await send_cmd_log(
        title="🧹 Purge Complete",
        description=f"**{ctx.author.name}** purged Gamble God from {removed}/{len(gods)} users.",
        color=0xe74c3c
    )


@bot.command(name="blacklist")
@has_any_role_by_id(GAMBLE_SUPERVISOR_ROLE_ID, ECONOMY_MANAGER_ROLE_ID)
async def blacklist(ctx, member: discord.Member, blacklist_type: str, *, reason: str = None):
    """Toggle a blacklist role on a user. Usage: !blacklist @user economy [reason] or !blacklist @user loan [reason]"""
    
    eco_role = ctx.guild.get_role(ECO_BLACKLIST_ROLE_ID)
    loan_role = ctx.guild.get_role(LOAN_BLACKLIST_ROLE_ID)

    if not eco_role or not loan_role:
        await ctx.send("❌ One or both blacklist roles not found.")
        return

    blacklist_type = blacklist_type.lower()
    reason_text = f"\n**Reason:** {reason}" if reason else ""

    if blacklist_type == "economy":
        if eco_role in member.roles:
            await member.remove_roles(eco_role)
            await ctx.send(f"✅ Removed Economy Blacklist from {member.name}{reason_text}")
            await send_cmd_log(
                title="🔓 Economy Blacklist Removed",
                description=f"**{ctx.author.name}** removed Economy Blacklist from **{member.name}**{reason_text}",
                color=0xe67e22
            )
        else:
            await member.add_roles(eco_role)
            await ctx.send(f"✅ Added Economy Blacklist to {member.name}{reason_text}")
            await send_cmd_log(
                title="🔒 Economy Blacklist Added",
                description=f"**{ctx.author.name}** added Economy Blacklist to **{member.name}**{reason_text}",
                color=0xe67e22
            )
    
    elif blacklist_type == "loan":
        if loan_role in member.roles:
            await member.remove_roles(loan_role)
            await ctx.send(f"✅ Removed Loan Blacklist from {member.name}{reason_text}")
            await send_cmd_log(
                title="🔓 Loan Blacklist Removed",
                description=f"**{ctx.author.name}** removed Loan Blacklist from **{member.name}**{reason_text}",
                color=0xe74c3c
            )
        else:
            await member.add_roles(loan_role)
            await ctx.send(f"✅ Added Loan Blacklist to {member.name}{reason_text}")
            await send_cmd_log(
                title="🔒 Loan Blacklist Added",
                description=f"**{ctx.author.name}** added Loan Blacklist to **{member.name}**{reason_text}",
                color=0xe74c3c
            )
    
    else:
        await ctx.send("❌ Invalid type. Use `economy` or `loan`.")


bot.run(DISCORD_TOKEN)