import discord
from discord.ext import commands
import aiohttp
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta

TOKEN = "YOUR_DISCORD_BOT_TOKEN"

UPCOMING_CHANNEL_ID = 123456789
PAST_CHANNEL_ID = 123456789
REMINDER_CHANNEL_ID = 123456789

CF_API = "https://codeforces.com/api/contest.list"

DATA_FILE = "cf_data.json"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# STORAGE
# =========================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"past": [], "upcoming": [], "reminded": []}

    with open(DATA_FILE) as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


data = load_data()

# =========================
# FETCH CODEFORCES
# =========================

async def fetch_contests():

    async with aiohttp.ClientSession() as session:
        async with session.get(CF_API) as resp:
            js = await resp.json()

    return js["result"]


# =========================
# TIME FORMAT
# =========================

def bd_time(ts):

    utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    bd = utc + timedelta(hours=6)

    return bd.strftime("%d %b %Y • %I:%M %p")


# =========================
# EMBED BUILDERS
# =========================

def upcoming_embed(contest, num):

    e = discord.Embed(
        title=f"#{num} {contest['name']}",
        color=0x2f3136
    )

    e.add_field(
        name="Start",
        value=bd_time(contest["startTimeSeconds"]),
        inline=False
    )

    e.add_field(
        name="Link",
        value=f"https://codeforces.com/contest/{contest['id']}",
        inline=False
    )

    return e


def past_embed(contest, num):

    e = discord.Embed(
        title=f"#{num} {contest['name']}",
        color=0x202225
    )

    e.add_field(
        name="Finished",
        value=bd_time(contest["startTimeSeconds"]),
        inline=False
    )

    e.add_field(
        name="Link",
        value=f"https://codeforces.com/contest/{contest['id']}",
        inline=False
    )

    return e


def reminder_embed(contest):

    e = discord.Embed(
        title="⏰ Contest in 2 Hours",
        description=contest["name"],
        color=0xffcc00
    )

    e.add_field(
        name="Start Time",
        value=bd_time(contest["startTimeSeconds"]),
        inline=False
    )

    e.add_field(
        name="Link",
        value=f"https://codeforces.com/contest/{contest['id']}",
        inline=False
    )

    return e


# =========================
# UPDATE UPCOMING
# =========================

async def update_upcoming():

    global data

    contests = await fetch_contests()

    upcoming = [
        c for c in contests
        if c["phase"] == "BEFORE"
    ]

    upcoming = sorted(
        upcoming,
        key=lambda x: x["startTimeSeconds"]
    )

    channel = bot.get_channel(UPCOMING_CHANNEL_ID)

    ids = [c["id"] for c in upcoming]

    for cid in ids:

        if cid not in data["upcoming"]:

            contest = next(c for c in upcoming if c["id"] == cid)

            num = len(data["upcoming"]) + 1

            msg = await channel.send(embed=upcoming_embed(contest, num))

            data["upcoming"].append({
                "id": cid,
                "msg": msg.id
            })

    save_data(data)


# =========================
# MOVE FINISHED
# =========================

async def update_finished():

    global data

    contests = await fetch_contests()

    finished_ids = [
        c["id"] for c in contests
        if c["phase"] == "FINISHED"
    ]

    upcoming_channel = bot.get_channel(UPCOMING_CHANNEL_ID)
    past_channel = bot.get_channel(PAST_CHANNEL_ID)

    for entry in list(data["upcoming"]):

        if entry["id"] in finished_ids:

            contest = next(c for c in contests if c["id"] == entry["id"])

            num = len(data["past"]) + 1

            await past_channel.send(
                embed=past_embed(contest, num)
            )

            try:
                msg = await upcoming_channel.fetch_message(entry["msg"])
                await msg.delete()
            except:
                pass

            data["past"].append(entry["id"])
            data["upcoming"].remove(entry)

    save_data(data)


# =========================
# REMINDERS
# =========================

async def reminder_loop():

    await bot.wait_until_ready()

    while not bot.is_closed():

        try:

            contests = await fetch_contests()

            now = datetime.now(timezone.utc)

            for c in contests:

                if c["phase"] != "BEFORE":
                    continue

                start = datetime.fromtimestamp(
                    c["startTimeSeconds"],
                    tz=timezone.utc
                )

                diff = (start - now).total_seconds()

                # 2h reminder
                if 7100 < diff < 7300:

                    if c["id"] not in data["reminded"]:

                        ch = bot.get_channel(REMINDER_CHANNEL_ID)

                        await ch.send(embed=reminder_embed(c))

                        data["reminded"].append(c["id"])

                        save_data(data)

                # start alert
                if -20 < diff < 20:

                    ch = bot.get_channel(REMINDER_CHANNEL_ID)

                    await ch.send(
                        f"🔥 **{c['name']} is starting now!**\n"
                        f"https://codeforces.com/contest/{c['id']}"
                    )

        except Exception as e:
            print("Reminder error:", e)

        await asyncio.sleep(60)


# =========================
# MAIN LOOP
# =========================

async def contest_loop():

    await bot.wait_until_ready()

    while not bot.is_closed():

        try:

            await update_upcoming()

            await update_finished()

        except Exception as e:

            print("Loop error:", e)

        await asyncio.sleep(300)


# =========================
# READY
# =========================

@bot.event
async def on_ready():

    print("Bot online")

    bot.loop.create_task(contest_loop())

    bot.loop.create_task(reminder_loop())


bot.run(TOKEN)
