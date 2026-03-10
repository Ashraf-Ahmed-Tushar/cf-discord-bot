import discord
import aiohttp
import asyncio
import json
import os
from datetime import datetime, timezone
import pytz

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
REMINDER_CHANNEL_ID = int(os.getenv("REMINDER_CHANNEL_ID"))

DATA_FILE = "sent_contests.json"
BD_TZ = pytz.timezone("Asia/Dhaka")

intents = discord.Intents.default()
client = discord.Client(intents=intents)

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        sent_contests = json.load(f)
else:
    sent_contests = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(sent_contests, f)

async def check_contests():
    await client.wait_until_ready()

    announce_channel = client.get_channel(CHANNEL_ID)
    reminder_channel = client.get_channel(REMINDER_CHANNEL_ID)

    while not client.is_closed():
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

            # sort by start time
            upcoming.sort(key=lambda x: x["startTimeSeconds"])

            for contest in upcoming:

                contest_id = str(contest["id"])

                start_time_utc = datetime.fromtimestamp(
                    contest["startTimeSeconds"], tz=timezone.utc
                )

                start_time_bd = start_time_utc.astimezone(BD_TZ)

                diff = (start_time_utc - now_utc).total_seconds()

                if contest_id not in sent_contests:

                    time_text = start_time_bd.strftime("%d-%m-%Y  < %I:%M %p >")

                    embed = discord.Embed(
                        title="📢 Upcoming Codeforces Contest",
                        description=f"\n**{contest['name']}**\n",
                        color=discord.Color.blue()
                    )

                    embed.add_field(
                        name="🕒 Start Time",
                        value=time_text,
                        inline=False
                    )

                    embed.add_field(
                        name="🔗 Contest Link",
                        value=f"https://codeforces.com/contest/{contest['id']}",
                        inline=False
                    )

                    message = "\n"

                    await announce_channel.send(message, embed=embed)

                    await announce_channel.send("\nBest of luck ||@everyone|| 🍀\n")

                    sent_contests[contest_id] = {
                        "announced": True,
                        "reminder_sent": False
                    }

                    save_data()

                # 8 hour reminder
                if contest_id in sent_contests:

                    if not sent_contests[contest_id]["reminder_sent"] and 28000 < diff < 29000:

                        time_text = start_time_bd.strftime("%d-%m-%Y  < %I:%M %p >")

                        embed = discord.Embed(
                            title="⏰ Codeforces Contest Reminder (8 Hours)",
                            description=f"\n**{contest['name']}**\n",
                            color=discord.Color.orange()
                        )

                        embed.add_field(
                            name="🕒 Start Time",
                            value=time_text,
                            inline=False
                        )

                        embed.add_field(
                            name="🔗 Contest Link",
                            value=f"https://codeforces.com/contest/{contest['id']}",
                            inline=False
                        )

                        await reminder_channel.send("\n", embed=embed)
                        await reminder_channel.send("\nBest of luck ||@everyone|| 🍀\n")

                        sent_contests[contest_id]["reminder_sent"] = True
                        save_data()

            await asyncio.sleep(600)

        except Exception as e:
            print("Error:", e)
            await asyncio.sleep(60)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    client.loop.create_task(check_contests())

client.run(TOKEN)
