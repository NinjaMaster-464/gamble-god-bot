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
TRANSACTION_LOG_CHANNEL = "transactions-logs"
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
    url_get = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{to_id}"
    headers = {"Authorization": UNB_TOKEN, "Accept": "application/json"}
    
    try:
        async with session.get(url_get, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                receiver_cash = data.get("cash", 0)
                receiver_bank = data.get("bank", 0)
            else:
                return False

        # Take from cash first, then bank if needed
        remaining = amount
        new_cash = receiver_cash
        new_bank = receiver_bank
        
        if receiver_cash >= remaining:
            new_cash = receiver_cash - remaining
            remaining = 0
        else:
            remaining -= receiver_cash
            new_cash = 0
            if receiver_bank >= remaining:
                new_bank = receiver_bank - remaining
                remaining = 0
            else:
                new_bank = 0
        
        url_put = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/users/{to_id}"
        async with session.put(url_put, headers=headers, json={"cash": new_cash, "bank": new_bank}) as resp:
            if resp.status != 200:
                return False

        # Return to sender as cash
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

    # SUPER SIMPLE TEST - log ALL messages
    print(f"[MSG] Channel: {message.channel.name} | Author: {message.author.name} | Content: {message.content[:100]} | Embeds: {len(message.embeds)}")

    # Check if this is the UnbelievaBoat transaction log channel
    if message.channel.name == TRANSACTION_LOG_CHANNEL and message.embeds:
        embed = message.embeds[0]
        
        # Debug logging
        print(f"[DEBUG] Message in {TRANSACTION_LOG_CHANNEL} from {message.author.name}")
        print(f"[DEBUG] Embed title: {embed.title}")
        
        # Look for Balance updated embeds
        if embed.title and "Balance updated" in str(embed.title):
            print("[DEBUG] Found Balance updated embed!")
            
            fields = {}
            for field in embed.fields:
                fields[field.name] = field.value
            
            print(f"[DEBUG] Parsed fields: {fields}")
            
            reason = fields.get("Reason", "")
            print(f"[DEBUG] Reason: {reason}")
            
            if "give-money" not in reason.lower():
                print("[DEBUG] Not a give-money transaction, skipping.")
                await bot.process_commands(message)
                return
            
            print("[DEBUG] This is a give-money transaction!")
            
            receiver_str = fields.get("User", "")
            sender_str = fields.get("Actioned by", "")
            
            print(f"[DEBUG] Receiver str: {receiver_str}")
            print(f"[DEBUG] Sender str: {sender_str}")
            
            receiver_match = re.search(r'<@!?(\d+)>', receiver_str)
            sender_match = re.search(r'<@!?(\d+)>', sender_str)
            
            if receiver_match:
                receiver_id = int(receiver_match.group(1))
                print(f"[DEBUG] Receiver ID: {receiver_id}")
            else:
                print("[DEBUG] Could not find receiver ID!")
                await bot.process_commands(message)
                return
            
            if sender_match:
                sender_id = int(sender_match.group(1))
                print(f"[DEBUG] Sender ID: {sender_id}")
            else:
                print("[DEBUG] Could not find sender ID!")
                await bot.process_commands(message)
                return
            
            receiver = message.guild.get_member(receiver_id)
            sender = message.guild.get_member(sender_id)
            
            if not receiver:
                print("[DEBUG] Receiver member not found!")
                return
            if not sender:
                print("[DEBUG] Sender member not found!")
                return
            
            loan_role = message.guild.get_role(LOAN_BLACKLIST_ROLE_ID)
            if not loan_role:
                print("[DEBUG] Loan blacklist role not found!")
                return
            
            print(f"[DEBUG] Receiver has loan blacklist: {loan_role in receiver.roles}")
            
            if loan_role not in receiver.roles:
                print("[DEBUG] Receiver not blacklisted, skipping.")
                await bot.process_commands(message)
                return
            
            amount_str = fields.get("Amount", "")
            print(f"[DEBUG] Amount str: {amount_str}")
            
            cash_match = re.search(r'Cash:\s*\+?([\d,]+)', amount_str)
            bank_match = re.search(r'Bank:\s*\+?([\d,]+)', amount_str)
            
            amount = 0
            if cash_match:
                amount += int(cash_match.group(1).replace(',', ''))
            if bank_match:
                amount += int(bank_match.group(1).replace(',', ''))
            
            print(f"[DEBUG] Extracted amount: {amount}")
            
            if amount <= 0:
                print("[DEBUG] Amount is 0 or less, skipping.")
                return
            
            print(f"[DEBUG] Reversing payment: {sender.name} -> {receiver.name}, ${amount}")
            
            async with aiohttp.ClientSession() as session:
                await asyncio.sleep(0.5)
                success = await reverse_payment(session, sender_id, receiver_id, amount)
            
            print(f"[DEBUG] Reversal success: {success}")
            
            if success:
                await message.channel.send(
                    f"🚫 **Payment Blocked!** {receiver.mention} is Loan Blacklisted.\n"
                    f"${amount:,} has been returned to {sender.mention}."
                )
                await send_cmd_log(
                    title="🚫 Payment Blocked",
                    description=f"**{sender.name}** tried to send ${amount:,} to **{receiver.name}** (Loan Blacklisted)\nMoney returned.",
                    color=0xff0000
                )
            else:
                await message.channel.send(
                    f"⚠️ **Warning!** {receiver.mention} is Loan Blacklisted but payment reversal failed. Staff please check."
                )
                await send_cmd_log(
                    title="⚠️ Reversal Failed",
                    description=f"**{sender.name}** sent ${amount:,} to **{receiver.name}** (Loan Blacklisted). Could not reverse!",
                    color=0xff0000
                )
            return
    
    # Process commands normally for all other messages
    await bot.process_commands(message)


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
    
    # DEBUG: Check if transaction log channel exists
    guild = bot.get_guild(GUILD_ID)
    channel = discord.utils.get(guild.text_channels, name=TRANSACTION_LOG_CHANNEL)
    if channel:
        print(f"[DEBUG] Found '{TRANSACTION_LOG_CHANNEL}' channel: {channel.id}")
        print(f"[DEBUG] Bot can read: {channel.permissions_for(guild.me).read_messages}")
        print(f"[DEBUG] Bot can read history: {channel.permissions_for(guild.me).read_message_history}")
    else:
        print(f"[DEBUG] Channel '{TRANSACTION_LOG_CHANNEL}' NOT FOUND!")
        print(f"[DEBUG] Available channels: {[c.name for c in guild.text_channels[:20]]}")
    
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