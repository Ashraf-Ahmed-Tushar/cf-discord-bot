import discord
from discord.ext import commands
import aiohttp
import asyncio
import os
from datetime import datetime, timezone

# ===== ENV =====
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
REMINDER_CHANNEL_ID = int(os.getenv("REMINDER_CHANNEL_ID", "0"))

# ===== BOT SETUP =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=";", intents=intents)

# ===== GLOBAL STATE =====
# IDs of the pinned live messages
UPCOMING_MSG_ID = None
PAST_MSG_ID = None

# Track which contest IDs have triggered a reminder already (in memory)
REMINDED_IDS: set = set()

# Track posted past contest IDs and their count
PAST_POSTED_IDS: set = set()
PAST_COUNT = 0


# ===== FETCH =====
async def fetch_cf():
    url = "https://codeforces.com/api/contest.list"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
    return data["result"]


def should_include(contest: dict) -> bool:
    """Filter: keep only rated contests that are Div.2, Div.3, Div.4, or combined Div.1+2."""
    name = contest["name"].lower()
    if "unrated" in name:
        return False
    # Skip pure Div.1 (not combined)
    if "div. 1" in name and "div. 2" not in name:
        return False
    return True


# ===== EMBEDS =====
def build_upcoming_embed(contests: list) -> discord.Embed:
    """
    Build a single rich embed listing upcoming contests.
    Numbered in reverse: closest = 1 (shown last), furthest = N (shown first).
    """
    embed = discord.Embed(
        title="📢  Upcoming Codeforces Contests",
        color=0x5865F2,  # Discord blurple
    )
    embed.set_footer(text="Updates every 10 min  •  cf.bot")

    if not contests:
        embed.description = "*No upcoming rated contests right now.*"
        return embed

    total = len(contests)
    lines = []
    for i, c in enumerate(contests):
        num = total - i  # reverse numbering
        ts = c["startTimeSeconds"]
        lines.append(
            f"**`#{num}`**  **{c['name']}**\n"
            f"⠀⠀🕒 <t:{ts}:F>  ·  ⏳ <t:{ts}:R>\n"
            f"⠀⠀🔗 [codeforces.com/contest/{c['id']}](https://codeforces.com/contest/{c['id']})"
        )

    embed.description = "\n\n".join(lines)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_past_embed(contests_lines: list, start_num: int) -> discord.Embed:
    """
    Build an embed for a page of past contests.
    contests_lines: list of contest name strings (oldest first in this chunk).
    start_num: the number of the first contest in this chunk.
    """
    embed = discord.Embed(
        title="📜  Past Codeforces Contests",
        color=0x57F287,  # green
    )
    lines = []
    for i, name in enumerate(contests_lines):
        lines.append(f"**`{start_num + i}.`**  {name}")
    embed.description = "\n".join(lines)
    embed.set_footer(text="Completed contests  •  cf.bot")
    return embed


def build_announce_embed(contest: dict) -> discord.Embed:
    ts = contest["startTimeSeconds"]
    embed = discord.Embed(
        title="📢  New Contest Announced",
        description=f"## {contest['name']}",
        color=0x5865F2,
    )
    embed.add_field(name="🕒  Start Time", value=f"<t:{ts}:F>", inline=False)
    embed.add_field(name="⏳  Countdown", value=f"<t:{ts}:R>", inline=False)
    embed.add_field(
        name="🔗  Link",
        value=f"[codeforces.com/contest/{contest['id']}](https://codeforces.com/contest/{contest['id']})",
        inline=False,
    )
    embed.set_footer(text="Codeforces  •  cf.bot")
    return embed


def build_reminder_embed(contest: dict, hours: int) -> discord.Embed:
    ts = contest["startTimeSeconds"]
    embed = discord.Embed(
        title=f"⏰  Contest Reminder — {hours}h to go!",
        description=f"## {contest['name']}",
        color=0xFEE75C,  # yellow
    )
    embed.add_field(name="🕒  Start Time", value=f"<t:{ts}:F>", inline=False)
    embed.add_field(name="⏳  Time Left", value=f"<t:{ts}:R>", inline=False)
    embed.add_field(
        name="🔗  Join Now",
        value=f"[codeforces.com/contest/{contest['id']}](https://codeforces.com/contest/{contest['id']})",
        inline=False,
    )
    embed.set_footer(text="Codeforces  •  cf.bot")
    return embed


# ===== BACKGROUND TASKS =====

async def task_update_upcoming():
    """
    Every 10 min: fetch upcoming contests, rebuild the embed, edit-in-place (or post new).
    """
    global UPCOMING_MSG_ID

    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        try:
            all_contests = await fetch_cf()
            upcoming = [c for c in all_contests if c["phase"] == "BEFORE" and should_include(c)]
            upcoming.sort(key=lambda x: x["startTimeSeconds"])

            embed = build_upcoming_embed(upcoming)

            if UPCOMING_MSG_ID:
                try:
                    msg = await channel.fetch_message(UPCOMING_MSG_ID)
                    await msg.edit(embed=embed)
                except (discord.NotFound, discord.HTTPException):
                    msg = await channel.send(embed=embed)
                    UPCOMING_MSG_ID = msg.id
            else:
                msg = await channel.send(embed=embed)
                UPCOMING_MSG_ID = msg.id

        except Exception as e:
            print(f"[upcoming] Error: {e}")

        await asyncio.sleep(600)


async def task_announce_and_remind():
    """
    Every 10 min: announce new upcoming contests + send 8h reminders.
    """
    global REMINDED_IDS

    await bot.wait_until_ready()
    announce_ch = bot.get_channel(CHANNEL_ID)
    remind_ch = bot.get_channel(REMINDER_CHANNEL_ID)

    announced_ids: set = set()

    # Pre-seed from channel history so we don't re-announce on restart
    try:
        async for msg in announce_ch.history(limit=50):
            if msg.author == bot.user and msg.embeds:
                for em in msg.embeds:
                    if em.title and "New Contest Announced" in em.title:
                        # Try to extract contest id from the link field
                        for field in em.fields:
                            if "🔗" in field.name and "contest/" in field.value:
                                cid = field.value.split("contest/")[1].split(")")[0].strip()
                                try:
                                    announced_ids.add(int(cid))
                                except ValueError:
                                    pass
    except Exception:
        pass

    while not bot.is_closed():
        try:
            all_contests = await fetch_cf()
            now_utc = datetime.now(timezone.utc)

            upcoming = [c for c in all_contests if c["phase"] == "BEFORE" and should_include(c)]
            upcoming.sort(key=lambda x: x["startTimeSeconds"])

            for c in upcoming:
                cid = c["id"]
                ts = c["startTimeSeconds"]
                start_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                diff_seconds = (start_dt - now_utc).total_seconds()

                # --- Announce new contest ---
                if cid not in announced_ids:
                    announced_ids.add(cid)
                    embed = build_announce_embed(c)
                    await announce_ch.send(embed=embed)
                    await announce_ch.send("Best of luck! ||@everyone|| 🍀")

                # --- 8-hour reminder (window: 28000s – 29000s) ---
                if cid not in REMINDED_IDS and 28000 < diff_seconds < 29000:
                    REMINDED_IDS.add(cid)
                    embed = build_reminder_embed(c, hours=8)
                    await remind_ch.send(embed=embed)
                    await remind_ch.send("Best of luck! ||@everyone|| 🍀")

        except Exception as e:
            print(f"[announce/remind] Error: {e}")

        await asyncio.sleep(600)


async def task_track_past():
    """
    Every 5 min: detect newly finished contests and append them to the past embed.
    Keeps one live message per ~40 contests (Discord 4096 char limit safety).
    """
    global PAST_MSG_ID, PAST_COUNT, PAST_POSTED_IDS

    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    PAGE_SIZE = 40  # contests per embed

    while not bot.is_closed():
        try:
            all_contests = await fetch_cf()
            finished = [c for c in all_contests if c["phase"] == "FINISHED" and should_include(c)]
            # Sort oldest first so numbering is chronological
            finished.sort(key=lambda x: x["startTimeSeconds"])

            new_ones = [c for c in finished if c["id"] not in PAST_POSTED_IDS]

            for c in new_ones:
                PAST_POSTED_IDS.add(c["id"])
                PAST_COUNT += 1

                # Determine which page this belongs to
                page_start = ((PAST_COUNT - 1) // PAGE_SIZE) * PAGE_SIZE + 1
                page_end_idx = PAST_COUNT  # how many entries on this page so far

                # Collect names for current page
                page_contests = [
                    fc["name"]
                    for fc in finished
                    if fc["id"] in PAST_POSTED_IDS
                ][page_start - 1: page_end_idx]

                embed = build_past_embed(page_contests, start_num=page_start)

                is_first_in_page = (PAST_COUNT == page_start)

                if is_first_in_page:
                    # New page → new message
                    msg = await channel.send(embed=embed)
                    PAST_MSG_ID = msg.id
                else:
                    # Edit existing page message
                    if PAST_MSG_ID:
                        try:
                            msg = await channel.fetch_message(PAST_MSG_ID)
                            await msg.edit(embed=embed)
                        except (discord.NotFound, discord.HTTPException):
                            msg = await channel.send(embed=embed)
                            PAST_MSG_ID = msg.id

        except Exception as e:
            print(f"[past] Error: {e}")

        await asyncio.sleep(300)


# ===== COMMANDS =====

@bot.command(name="upcoming")
async def cmd_upcoming(ctx):
    """Show current upcoming contests."""
    try:
        all_contests = await fetch_cf()
        upcoming = [c for c in all_contests if c["phase"] == "BEFORE" and should_include(c)]
        upcoming.sort(key=lambda x: x["startTimeSeconds"])
        embed = build_upcoming_embed(upcoming)
        await ctx.reply(embed=embed, mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Error fetching contests: `{e}`", mention_author=False)


@bot.command(name="past")
async def cmd_past(ctx, page: int = 1):
    """Show past contests (paged). Usage: ;past [page]"""
    try:
        all_contests = await fetch_cf()
        finished = [c for c in all_contests if c["phase"] == "FINISHED" and should_include(c)]
        finished.sort(key=lambda x: x["startTimeSeconds"])

        PAGE_SIZE = 15
        start_idx = (page - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        page_data = finished[start_idx:end_idx]

        if not page_data:
            await ctx.reply(f"❌ No contests on page {page}.", mention_author=False)
            return

        embed = build_past_embed([c["name"] for c in page_data], start_num=start_idx + 1)
        total_pages = (len(finished) + PAGE_SIZE - 1) // PAGE_SIZE
        embed.set_footer(text=f"Page {page}/{total_pages}  •  cf.bot")
        await ctx.reply(embed=embed, mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Error: `{e}`", mention_author=False)


@bot.command(name="refresh")
@commands.has_permissions(manage_messages=True)
async def cmd_refresh(ctx):
    """Force-refresh the upcoming embed right now."""
    global UPCOMING_MSG_ID
    try:
        all_contests = await fetch_cf()
        upcoming = [c for c in all_contests if c["phase"] == "BEFORE" and should_include(c)]
        upcoming.sort(key=lambda x: x["startTimeSeconds"])
        embed = build_upcoming_embed(upcoming)

        channel = bot.get_channel(CHANNEL_ID)
        if UPCOMING_MSG_ID:
            try:
                msg = await channel.fetch_message(UPCOMING_MSG_ID)
                await msg.edit(embed=embed)
                await ctx.reply("✅ Upcoming list refreshed!", mention_author=False)
                return
            except (discord.NotFound, discord.HTTPException):
                pass
        msg = await channel.send(embed=embed)
        UPCOMING_MSG_ID = msg.id
        await ctx.reply("✅ New upcoming list posted!", mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Error: `{e}`", mention_author=False)


@bot.command(name="setcfchannel")
@commands.has_permissions(administrator=True)
async def cmd_set_channel(ctx, channel: discord.TextChannel):
    """(Admin) Point the bot to a different announce channel at runtime."""
    global CHANNEL_ID, UPCOMING_MSG_ID, PAST_MSG_ID
    CHANNEL_ID = channel.id
    UPCOMING_MSG_ID = None
    PAST_MSG_ID = None
    await ctx.reply(
        f"✅ Announce channel set to {channel.mention}. "
        "Existing message pointers cleared.",
        mention_author=False,
    )


@bot.command(name="setreminderch")
@commands.has_permissions(administrator=True)
async def cmd_set_reminder_ch(ctx, channel: discord.TextChannel):
    """(Admin) Point the bot to a different reminder channel at runtime."""
    global REMINDER_CHANNEL_ID
    REMINDER_CHANNEL_ID = channel.id
    await ctx.reply(
        f"✅ Reminder channel set to {channel.mention}.",
        mention_author=False,
    )


@bot.command(name="cfhelp")
async def cmd_help(ctx):
    """Show all available bot commands."""
    embed = discord.Embed(
        title="📖  CF Bot — Commands",
        description="Prefix: `;`",
        color=0x5865F2,
    )
    embed.add_field(
        name="`;upcoming`",
        value="Show upcoming rated contests right now.",
        inline=False,
    )
    embed.add_field(
        name="`;past [page]`",
        value="List past contests. Default page 1. E.g. `;past 3`",
        inline=False,
    )
    embed.add_field(
        name="`;refresh`  *(Manage Messages)*",
        value="Force-refresh the live upcoming embed in the announce channel.",
        inline=False,
    )
    embed.add_field(
        name="`;setcfchannel #channel`  *(Admin)*",
        value="Change the announce channel without restarting the bot.",
        inline=False,
    )
    embed.add_field(
        name="`;setreminderch #channel`  *(Admin)*",
        value="Change the reminder channel without restarting the bot.",
        inline=False,
    )
    embed.set_footer(text="cf.bot  •  Codeforces Contest Tracker")
    await ctx.reply(embed=embed, mention_author=False)


# ===== EVENTS =====

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    print(f"   Announce channel : {CHANNEL_ID}")
    print(f"   Reminder channel : {REMINDER_CHANNEL_ID}")
    bot.loop.create_task(task_update_upcoming())
    bot.loop.create_task(task_announce_and_remind())
    bot.loop.create_task(task_track_past())


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply(
            "❌ You don't have permission to use this command.",
            mention_author=False,
        )
    elif isinstance(error, commands.BadArgument):
        await ctx.reply(f"❌ Bad argument: `{error}`", mention_author=False)
    else:
        print(f"Command error: {error}")


# ===== RUN =====
if not TOKEN:
    raise RuntimeError("TOKEN env variable is not set!")

bot.run(TOKEN)
