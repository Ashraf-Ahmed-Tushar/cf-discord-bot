import discord
from discord.ext import commands
import aiohttp
import asyncio
import os
from datetime import datetime, timezone
import pytz

from flask import Flask
from threading import Thread

# ===== KEEP ALIVE =====
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    Thread(target=run).start()

# ===== ENV =====
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
REMINDER_CHANNEL_ID = int(os.getenv("REMINDER_CHANNEL_ID"))

BD_TZ = pytz.timezone("Asia/Dhaka")

# ===== BOT =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=';', intents=intents)

# ===== GLOBAL STATE =====
UPCOMING_MSG_ID = None
PAST_MSG_ID = None
POSTED_CONTESTS = set()
PAST_COUNT = 0

# ===== HEADERS =====
PAST_HEADER = "📜 **Past Codeforces Contests**\n\n"
UPCOMING_HEADER = "📢 **Upcoming Codeforces Contests**\n\n"


# ===== FETCH =====
async def fetch_cf():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://codeforces.com/api/contest.list") as resp:
            data = await resp.json()
    return data["result"]


# ================= OLD AUTO SYSTEM (UNCHANGED) =================
async def check_contests():
    await bot.wait_until_ready()

    announce_channel = bot.get_channel(CHANNEL_ID)
    reminder_channel = bot.get_channel(REMINDER_CHANNEL_ID)

    while not bot.is_closed():
        try:
            contests = await fetch_cf()
            now_utc = datetime.now(timezone.utc)

            upcoming = []

            for contest in contests:
                if contest["phase"] != "BEFORE":
                    continue

                name = contest["name"].lower()

                if "unrated" in name:
                    continue
                if "div. 1" in name and "div. 2" not in name:
                    continue

                upcoming.append(contest)

            upcoming.sort(key=lambda x: x["startTimeSeconds"])

            for contest in upcoming:
                start_time_utc = datetime.fromtimestamp(
                    contest["startTimeSeconds"], tz=timezone.utc
                )
                diff = (start_time_utc - now_utc).total_seconds()

                # ===== ANNOUNCE =====
                messages = [msg async for msg in announce_channel.history(limit=10)]
                already_sent = any(
                    str(contest["id"]) in msg.content for msg in messages
                )

                if not already_sent:
                    embed = discord.Embed(
                        title="📢 Upcoming Codeforces Contest",
                        description=f"**{contest['name']}**",
                        color=discord.Color.blue()
                    )
                    embed.add_field(
                        name="🕒 Start Time",
                        value=f"<t:{contest['startTimeSeconds']}:F>",
                        inline=False
                    )
                    embed.add_field(
                        name="⏳ Countdown",
                        value=f"<t:{contest['startTimeSeconds']}:R>",
                        inline=False
                    )

                    await announce_channel.send(embed=embed)
                    await announce_channel.send("Best of luck ||@everyone|| 🍀")

                # ===== REMINDER =====
                messages_rem = [msg async for msg in reminder_channel.history(limit=10)]
                already_reminder = any(
                    str(contest["id"]) in msg.content for msg in messages_rem
                )

                if not already_reminder and 28000 < diff < 29000:
                    embed = discord.Embed(
                        title="⏰ Contest Reminder (8 Hours)",
                        description=f"**{contest['name']}**",
                        color=discord.Color.orange()
                    )
                    embed.add_field(
                        name="🕒 Start Time",
                        value=f"<t:{contest['startTimeSeconds']}:F>",
                        inline=False
                    )

                    await reminder_channel.send(embed=embed)
                    await reminder_channel.send("Best of luck ||@everyone|| 🍀")

            await asyncio.sleep(600)

        except Exception as e:
            print("Auto Error:", e)
            await asyncio.sleep(60)


# ================= NEW UPCOMING LIST =================
async def update_upcoming():
    global UPCOMING_MSG_ID

    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        try:
            contests = await fetch_cf()

            upcoming = []

            for c in contests:
                if c["phase"] != "BEFORE":
                    continue

                name = c["name"].lower()

                if "unrated" in name:
                    continue
                if "div. 1" in name and "div. 2" not in name:
                    continue

                upcoming.append(c)

            upcoming.sort(key=lambda x: x["startTimeSeconds"])

            total = len(upcoming)
            lines = []

            for i, c in enumerate(upcoming):
                num = total - i

                lines.append(
                    f"**{num}.** {c['name']}\n"
                    f"🕒 <t:{c['startTimeSeconds']}:F>\n"
                    f"⏳ <t:{c['startTimeSeconds']}:R>\n"
                    f"🔗 https://codeforces.com/contest/{c['id']}\n"
                )

            content = UPCOMING_HEADER + ("\n".join(lines) if lines else "No upcoming contests.")

            if UPCOMING_MSG_ID:
                try:
                    msg = await channel.fetch_message(UPCOMING_MSG_ID)
                    await msg.edit(content=content)
                except:
                    msg = await channel.send(content)
                    UPCOMING_MSG_ID = msg.id
            else:
                msg = await channel.send(content)
                UPCOMING_MSG_ID = msg.id

        except Exception as e:
            print("Upcoming Error:", e)

        await asyncio.sleep(600)


# ================= NEW PAST TRACKER =================
async def track_past():
    global PAST_MSG_ID, PAST_COUNT

    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        try:
            contests = await fetch_cf()

            for c in contests:
                if c["phase"] != "FINISHED":
                    continue

                if c["id"] in POSTED_CONTESTS:
                    continue

                POSTED_CONTESTS.add(c["id"])
                PAST_COUNT += 1

                line = f"**{PAST_COUNT}.** {c['name']}\n"

                if PAST_MSG_ID:
                    try:
                        msg = await channel.fetch_message(PAST_MSG_ID)
                        await msg.edit(content=msg.content + line)
                    except:
                        msg = await channel.send(PAST_HEADER + line)
                        PAST_MSG_ID = msg.id
                else:
                    msg = await channel.send(PAST_HEADER + line)
                    PAST_MSG_ID = msg.id

        except Exception as e:
            print("Past Error:", e)

        await asyncio.sleep(300)


# ================= COMMAND =================
@bot.command()
async def upcoming(ctx):
    await ctx.send("📌 Check the pinned Upcoming section above 👆")


# ================= READY =================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    bot.loop.create_task(check_contests())   # OLD
    bot.loop.create_task(update_upcoming())  # NEW
    bot.loop.create_task(track_past())       # NEW


# ===== RUN =====
keep_alive()
bot.run(TOKEN)
