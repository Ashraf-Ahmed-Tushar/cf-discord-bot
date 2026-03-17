import discord
from discord.ext import commands
import aiohttp
import asyncio
import json
import os
from datetime import datetime, timezone
import pytz

from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()


TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
REMINDER_CHANNEL_ID = int(os.getenv("REMINDER_CHANNEL_ID"))

BD_TZ = pytz.timezone("Asia/Dhaka")

intents = discord.Intents.default()
intents.message_content = True  # প্রয়োজন command এর জন্য

bot = commands.Bot(command_prefix=';', intents=intents)


# ================= AUTO CHECK TASK =================
async def check_contests():
    await bot.wait_until_ready()

    announce_channel = bot.get_channel(CHANNEL_ID)
    reminder_channel = bot.get_channel(REMINDER_CHANNEL_ID)

    while not bot.is_closed():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://codeforces.com/api/contest.list") as resp:
                    data = await resp.json()

            contests = data["result"]
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
                start_time_bd = start_time_utc.astimezone(BD_TZ)
                diff = (start_time_utc - now_utc).total_seconds()

                # ===== ANNOUNCE CHECK =====
                messages = [msg async for msg in announce_channel.history(limit=10)]
                already_sent = any(
                    f"https://codeforces.com/contest/{contest['id']}" in msg.content for msg in messages
                )

                if not already_sent:
                    time_text = start_time_bd.strftime("%d-%m-%Y  < %I:%M %p >")
                    embed = discord.Embed(
                        title="📢 Upcoming Codeforces Contest",
                        description=f"\n**{contest['name']}**\n",
                        color=discord.Color.blue()
                    )
                    embed.add_field(name="🕒 Start Time", value=time_text, inline=False)
                    embed.add_field(
                        name="🔗 Contest Link",
                        value=f"https://codeforces.com/contest/{contest['id']}",
                        inline=False
                    )

                    await announce_channel.send(embed=embed)
                    await announce_channel.send("\nBest of luck ||@everyone|| 🍀\n")

                # ===== REMINDER CHECK =====
                messages_rem = [msg async for msg in reminder_channel.history(limit=10)]
                already_reminder_sent = any(
                    f"https://codeforces.com/contest/{contest['id']}" in msg.content for msg in messages_rem
                )

                if not already_reminder_sent and 28000 < diff < 29000:
                    time_text = start_time_bd.strftime("%d-%m-%Y  < %I:%M %p >")
                    embed = discord.Embed(
                        title="⏰ Codeforces Contest Reminder (8 Hours)",
                        description=f"\n**{contest['name']}**\n",
                        color=discord.Color.orange()
                    )
                    embed.add_field(name="🕒 Start Time", value=time_text, inline=False)
                    embed.add_field(
                        name="🔗 Contest Link",
                        value=f"https://codeforces.com/contest/{contest['id']}",
                        inline=False
                    )

                    await reminder_channel.send(embed=embed)
                    await reminder_channel.send("\nBest of luck ||@everyone|| 🍀\n")

            await asyncio.sleep(600)

        except Exception as e:
            print("Error:", e)
            await asyncio.sleep(60)


# ================= COMMAND =================
@bot.command()
async def upcoming(ctx):
    async with aiohttp.ClientSession() as session:
        async with session.get("https://codeforces.com/api/contest.list") as resp:
            data = await resp.json()

    contests = data["result"]
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

    for contest in upcoming[:5]:
        start_time_utc = datetime.fromtimestamp(
            contest["startTimeSeconds"], tz=timezone.utc
        )
        start_time_bd = start_time_utc.astimezone(BD_TZ)

        time_text = start_time_bd.strftime("%d-%m-%Y  < %I:%M %p >")

        embed = discord.Embed(
            title="📢 Upcoming Codeforces Contest",
            description=f"\n**{contest['name']}**\n",
            color=discord.Color.green()
        )
        embed.add_field(name="🕒 Start Time", value=time_text, inline=False)
        embed.add_field(
            name="🔗 Contest Link",
            value=f"https://codeforces.com/contest/{contest['id']}",
            inline=False
        )

        await ctx.send(embed=embed)


# ================= READY =================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.loop.create_task(check_contests())


keep_alive()
bot.run(TOKEN)
