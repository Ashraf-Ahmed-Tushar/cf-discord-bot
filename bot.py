import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
from datetime import datetime, timezone

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=";", intents=intents)

PAST_HEADER = "📜 PAST CODEFORCES CONTESTS"
UPCOMING_HEADER = "📢 UPCOMING CODEFORCES CONTESTS"

past_msgs = {}      # contest_id -> message
upcoming_msgs = {}  # contest_id -> message

reminded_2h = set()
start_alert = set()


# ==============================
# Fetch Codeforces
# ==============================

async def fetch_cf():

    url = "https://codeforces.com/api/contest.list"

    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            data = await r.json()

    return data["result"]


# ==============================
# Contest Embed
# ==============================

def contest_embed(c, number):

    ts = c["startTimeSeconds"]
    dur = c["durationSeconds"]

    hours = dur // 3600
    mins = (dur % 3600) // 60

    e = discord.Embed(
        title=f"#{number}  {c['name']}",
        color=0x5865F2
    )

    e.add_field(
        name="📅 Start",
        value=f"<t:{ts}:F>",
        inline=False
    )

    e.add_field(
        name="⏳ Countdown",
        value=f"<t:{ts}:R>",
        inline=True
    )

    e.add_field(
        name="🕐 Duration",
        value=f"{hours}h {mins}m",
        inline=True
    )

    e.add_field(
        name="🔗 Contest Link",
        value=f"https://codeforces.com/contest/{c['id']}",
        inline=False
    )

    return e


# ==============================
# Scan Channel (restart safe)
# ==============================

async def scan_channel():

    channel = bot.get_channel(CHANNEL_ID)

    async for m in channel.history(limit=300):

        if not m.embeds:
            continue

        embed = m.embeds[0]

        if not embed.fields:
            continue

        link = embed.fields[-1].value

        if "codeforces.com/contest" not in link:
            continue

        cid = int(link.split("/")[-1])

        if "📅 Start" in embed.fields[0].name:
            upcoming_msgs[cid] = m
        else:
            past_msgs[cid] = m


# ==============================
# Renumber messages
# ==============================

async def renumber():

    upcoming = sorted(
        upcoming_msgs.items(),
        key=lambda x: x[1].created_at
    )

    total = len(upcoming)

    for i, (cid, msg) in enumerate(upcoming):

        num = total - i

        embed = msg.embeds[0]
        name = embed.title.split(" ", 1)[1]

        embed.title = f"#{num} {name}"

        await msg.edit(embed=embed)


# ==============================
# Update Upcoming
# ==============================

async def update_upcoming():

    contests = await fetch_cf()

    upcoming = [
        c for c in contests
        if c["phase"] == "BEFORE"
    ]

    upcoming.sort(key=lambda x: x["startTimeSeconds"])

    channel = bot.get_channel(CHANNEL_ID)

    for c in upcoming:

        cid = c["id"]

        if cid not in upcoming_msgs:

            msg = await channel.send(
                embed=contest_embed(c, 0)
            )

            upcoming_msgs[cid] = msg

    await renumber()


# ==============================
# Move finished contests
# ==============================

async def check_finished():

    contests = await fetch_cf()

    finished = {
        c["id"] for c in contests
        if c["phase"] == "FINISHED"
    }

    channel = bot.get_channel(CHANNEL_ID)

    for cid in list(upcoming_msgs):

        if cid in finished:

            msg = upcoming_msgs[cid]

            embed = msg.embeds[0]

            await msg.delete()

            del upcoming_msgs[cid]

            num = len(past_msgs) + 1

            embed.title = f"#{num} {embed.title}"

            new_msg = await channel.send(embed=embed)

            past_msgs[cid] = new_msg

    await renumber()


# ==============================
# Reminder System
# ==============================

async def check_reminders():

    contests = await fetch_cf()

    now = datetime.now(timezone.utc).timestamp()

    channel = bot.get_channel(CHANNEL_ID)

    for c in contests:

        if c["phase"] != "BEFORE":
            continue

        ts = c["startTimeSeconds"]
        cid = c["id"]

        diff = ts - now

        # 2 hour reminder
        if 7000 < diff < 7200 and cid not in reminded_2h:

            reminded_2h.add(cid)

            await channel.send(
                f"⏰ **2 hours left** for **{c['name']}**"
            )

        # start alert
        if 0 < diff < 60 and cid not in start_alert:

            start_alert.add(cid)

            await channel.send(
                f"🔥 **{c['name']} is starting now!**"
            )


# ==============================
# Main Engine
# ==============================

@tasks.loop(minutes=5)
async def engine():

    await update_upcoming()

    await check_finished()

    await check_reminders()


# ==============================
# Commands
# ==============================

@bot.command()
async def sync(ctx):

    await scan_channel()

    await ctx.send("✅ Channel synced.")


# ==============================
# Ready Event
# ==============================

@bot.event
async def on_ready():

    print(f"✅ Logged in as {bot.user}")

    await scan_channel()

    engine.start()


# ==============================

if not TOKEN:
    raise RuntimeError("TOKEN not set")

bot.run(TOKEN)
