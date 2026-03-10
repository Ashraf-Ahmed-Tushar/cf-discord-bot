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

# load sent contest memory
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

            for contest in contests:

                # only upcoming contests
                if contest["phase"] != "BEFORE":
                    continue

                name = contest["name"].lower()

                # skip unrated contests
                if "unrated" in name:
                    continue

                # skip div1 only contests
                if "div. 1" in name and "div. 2" not in name:
                    continue

                contest_id = str(contest["id"])

                start_time_utc = datetime.fromtimestamp(
                    contest["startTimeSeconds"], tz=timezone.utc
                )

                start_time_bd = start_time_utc.astimezone(BD_TZ)

                diff = (start_time_utc - now_utc).total_seconds()

                # announce contest if not already sent
                if contest_id not in sent_contests:

                    embed = discord.Embed(
                        title="📢 Upcoming Codeforces Contest",
                        description=f"**{contest['name']}**",
                        color=discord.Color.blue()
                    )

                    embed.add_field(
                        name="🕒 Start Time (Bangladesh)",
                        value=start_time_bd.strftime("%Y-%m-%d %I:%M %p"),
                        inline=False
                    )

                    embed.add_field(
                        name="🌍 Start Time (UTC)",
                        value=start_time_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        inline=False
                    )

                    embed.add_field(
                        name="🔗 Contest Link",
                        value=f"https://codeforces.com/contest/{contest['id']}",
                        inline=False
                    )

                    embed.set_footer(text="Contest detected from Codeforces upcoming list")

                    await announce_channel.send("@everyone")
                    await announce_channel.send(embed=embed)

                    sent_contests[contest_id] = {
                        "announced": True,
                        "reminder_sent": False
                    }

                    save_data()

                # 8 hour reminder
                if contest_id in sent_contests:

                    reminder_sent = sent_contests[contest_id]["reminder_sent"]

                    if not reminder_sent and 28000 < diff < 29000:

                        embed = discord.Embed(
                            title="⏰ Codeforces Contest Reminder (8 Hours)",
                            description=f"**{contest['name']}**",
                            color=discord.Color.orange()
                        )

                        embed.add_field(
                            name="🕒 Start Time (BD)",
                            value=start_time_bd.strftime("%Y-%m-%d %I:%M %p"),
                            inline=False
                        )

                        embed.add_field(
                            name="🔗 Contest Link",
                            value=f"https://codeforces.com/contest/{contest['id']}",
                            inline=False
                        )

                        await reminder_channel.send("@everyone")
                        await reminder_channel.send(embed=embed)

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
