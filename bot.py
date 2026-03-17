import discord
from discord.ext import commands
import aiohttp
import asyncio
import os
from datetime import datetime, timezone
import pytz

TOKEN               = os.getenv("TOKEN")
CHANNEL_ID          = int(os.getenv("CHANNEL_ID", "0"))
REMINDER_CHANNEL_ID = int(os.getenv("REMINDER_CHANNEL_ID", "0"))
OWNER_NAME          = os.getenv("OWNER_NAME", "cf.bot")

BD_TZ = pytz.timezone("Asia/Dhaka")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=";", intents=intents)

MSG_PAST_HEADER = None
MSG_PAST_LIST   = None
MSG_UP_HEADER   = None
MSG_UP_LIST     = None

SETUP_DONE     = False
PAST_ENTRIES   = []
PAST_IDS       = set()
ANNOUNCED_IDS  = set()
REMINDED_IDS   = set()
BOT_START_UTC  = None

PAST_HEADER_MARKER    = "PAST CODEFORCES CONTESTS"
UPCOMING_HEADER_MARKER = "UPCOMING CODEFORCES CONTESTS"


async def fetch_cf():
    url = "https://codeforces.com/api/contest.list"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json()
    return data["result"]


def should_include(c):
    name = c["name"].lower()
    if "unrated" in name:
        return False
    if "div. 1" in name and "div. 2" not in name and "global" not in name:
        return False
    return True


def get_div(name):
    n = name.lower()
    if "div. 1 + div. 2" in n or "div. 1 + 2" in n:
        return "1+2"
    if "div. 1" in n:
        return "1"
    if "div. 2" in n:
        return "2"
    if "div. 3" in n:
        return "3"
    if "div. 4" in n:
        return "4"
    if "educational" in n:
        return "edu"
    if "global" in n:
        return "global"
    return "other"


DIV_COLOR = {
    "1+2": 0xE91E63, "1": 0xE74C3C, "2": 0xE67E22,
    "3": 0x3498DB, "4": 0x57F287, "edu": 0x1ABC9C,
    "global": 0x9B59B6, "other": 0x99AAB5,
}
DIV_LABEL = {
    "1+2": "Div. 1 + Div. 2", "1": "Div. 1", "2": "Div. 2",
    "3": "Div. 3", "4": "Div. 4", "edu": "Educational (Div. 2)",
    "global": "Global Round", "other": "Special / Other",
}
DIV_MEDAL = {
    "1+2": "🔴", "1": "🔴", "2": "🟠",
    "3": "🔵", "4": "🟢", "edu": "🟦",
    "global": "🟣", "other": "⚪",
}


def fmt_duration(seconds):
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def fmt_bd(ts):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(BD_TZ)
    return dt.strftime("%-d %B, %Y  [ %-I:%M %p ]")


def reg_status(ts):
    now  = datetime.now(timezone.utc)
    diff = datetime.fromtimestamp(ts, tz=timezone.utc) - now
    if diff.total_seconds() > 0:
        return f"Open  ·  closes <t:{ts}:R>"
    return "Closed"


def make_past_header():
    return (
        "```\n"
        "     📜  PAST CODEFORCES CONTESTS\n"
        "     Tracked since this bot started\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```"
    )


def make_upcoming_header():
    return (
        "```\n"
        "    📢  UPCOMING CODEFORCES CONTESTS\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```"
    )


def embed_past_list(entries):
    e = discord.Embed(color=0x2B2D31)
    if not entries:
        e.description = "*No contests have ended since the bot started.*"
    else:
        lines = []
        for x in entries:
            medal = DIV_MEDAL.get(x["div"], "⚪")
            lines.append(f"`{x['num']:>3}.`  {medal}  **{x['name']}**")
        e.description = "\n".join(lines)
    e.set_footer(text=f"📜 {len(entries)} contest(s) completed  •  {OWNER_NAME}")
    return e


def embed_upcoming_list(contests):
    if not contests:
        e = discord.Embed(
            description="*No upcoming rated contests right now. Check back soon!*",
            color=0x5865F2,
        )
        e.set_footer(text=f"📢 Upcoming  •  {OWNER_NAME}")
        return e

    total = len(contests)
    div0  = get_div(contests[0]["name"])
    e = discord.Embed(color=DIV_COLOR.get(div0, 0x5865F2))

    blocks = []
    for i, c in enumerate(contests):
        num   = total - i
        div   = get_div(c["name"])
        ts    = c["startTimeSeconds"]
        dur   = fmt_duration(c.get("durationSeconds", 0))
        medal = DIV_MEDAL.get(div, "⚪")
        label = DIV_LABEL.get(div, div)
        cid   = c["id"]

        blocks.append(
            f"{medal} **`#{num}`  {c['name']}**\n"
            f"┣ 🏷️ `{label}`\n"
            f"┣ 📅 {fmt_bd(ts)}\n"
            f"┣ ⏳ <t:{ts}:R>  ·  🕐 Duration: **{dur}**\n"
            f"┣ 📝 Registration: {reg_status(ts)}\n"
            f"┗ 🔗 [codeforces.com/contest/{cid}](https://codeforces.com/contest/{cid})"
        )

    e.description = "\n\n".join(blocks)
    e.set_footer(text=f"📢 {total} contest(s) upcoming  •  {OWNER_NAME}")
    e.timestamp = datetime.now(timezone.utc)
    return e


def embed_announce(c, num_in_list):
    div   = get_div(c["name"])
    ts    = c["startTimeSeconds"]
    dur   = fmt_duration(c.get("durationSeconds", 0))
    cid   = c["id"]
    label = DIV_LABEL.get(div, div)
    medal = DIV_MEDAL.get(div, "⚪")

    e = discord.Embed(
        title="📢  New Contest Announced!",
        description=f"### {medal}  Contest `#{num_in_list}`\n**{c['name']}**",
        color=DIV_COLOR.get(div, 0x5865F2),
    )
    e.add_field(name="🏷️  Division",     value=f"`{label}`",       inline=True)
    e.add_field(name="⏳  Countdown",    value=f"<t:{ts}:R>",      inline=True)
    e.add_field(name="\u200b",           value="\u200b",            inline=True)
    e.add_field(name="📅  Start Time",   value=f"`{fmt_bd(ts)}`",  inline=False)
    e.add_field(name="🕐  Duration",     value=f"`{dur}`",         inline=True)
    e.add_field(name="📝  Registration", value=reg_status(ts),     inline=True)
    e.add_field(name="\u200b",           value="\u200b",            inline=True)
    e.add_field(
        name="🔗  Contest Link",
        value=f"[codeforces.com/contest/{cid}](https://codeforces.com/contest/{cid})",
        inline=False,
    )
    e.set_footer(text=f"Codeforces  •  {OWNER_NAME}")
    return e


def embed_reminder(c, hours):
    div   = get_div(c["name"])
    ts    = c["startTimeSeconds"]
    dur   = fmt_duration(c.get("durationSeconds", 0))
    cid   = c["id"]
    label = DIV_LABEL.get(div, div)

    e = discord.Embed(
        title=f"⏰  Contest Reminder — {hours} Hours Left!",
        description=f"**{c['name']}**",
        color=0xFEE75C,
    )
    e.add_field(name="🏷️  Division",   value=f"`{label}`",      inline=True)
    e.add_field(name="⏳  Time Left",  value=f"<t:{ts}:R>",     inline=True)
    e.add_field(name="\u200b",         value="\u200b",           inline=True)
    e.add_field(name="📅  Start Time", value=f"`{fmt_bd(ts)}`", inline=False)
    e.add_field(name="🕐  Duration",   value=f"`{dur}`",        inline=True)
    e.add_field(
        name="🔗  Join Contest",
        value=f"[codeforces.com/contest/{cid}](https://codeforces.com/contest/{cid})",
        inline=False,
    )
    e.set_footer(text=f"Codeforces  •  {OWNER_NAME}")
    return e


async def setup_channel(channel):
    global MSG_PAST_HEADER, MSG_PAST_LIST, MSG_UP_HEADER, MSG_UP_LIST, SETUP_DONE

    print("[setup] Scanning channel history…")
    bot_msgs = []
    async for msg in channel.history(limit=300):
        if msg.author == bot.user:
            bot_msgs.append(msg)
    bot_msgs.reverse()

    for msg in bot_msgs:
        content = msg.content or ""
        if PAST_HEADER_MARKER in content and not MSG_PAST_HEADER:
            MSG_PAST_HEADER = msg.id
        elif UPCOMING_HEADER_MARKER in content and not MSG_UP_HEADER:
            MSG_UP_HEADER = msg.id
        elif msg.embeds and MSG_PAST_HEADER and not MSG_PAST_LIST and not MSG_UP_HEADER:
            ft = msg.embeds[0].footer.text if msg.embeds[0].footer else ""
            if "📜" in ft or "completed" in ft:
                MSG_PAST_LIST = msg.id
        elif msg.embeds and MSG_UP_HEADER and not MSG_UP_LIST:
            ft = msg.embeds[0].footer.text if msg.embeds[0].footer else ""
            if "📢" in ft or "upcoming" in ft.lower():
                MSG_UP_LIST = msg.id

    if not MSG_PAST_HEADER:
        m = await channel.send(make_past_header())
        MSG_PAST_HEADER = m.id
        await asyncio.sleep(0.6)

    if not MSG_PAST_LIST:
        m = await channel.send(embed=embed_past_list([]))
        MSG_PAST_LIST = m.id
        await asyncio.sleep(0.6)

    if not MSG_UP_HEADER:
        m = await channel.send(make_upcoming_header())
        MSG_UP_HEADER = m.id
        await asyncio.sleep(0.6)

    if not MSG_UP_LIST:
        m = await channel.send(embed=embed_upcoming_list([]))
        MSG_UP_LIST = m.id

    SETUP_DONE = True
    print("[setup] Done ✅")


async def edit_msg(channel, key, embed):
    global MSG_PAST_LIST, MSG_UP_LIST
    mid = globals().get(key)
    if mid:
        try:
            msg = await channel.fetch_message(mid)
            await msg.edit(embed=embed)
            return
        except (discord.NotFound, discord.HTTPException):
            pass
    msg = await channel.send(embed=embed)
    globals()[key] = msg.id


async def task_upcoming():
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    await setup_channel(channel)

    while not bot.is_closed():
        try:
            all_c    = await fetch_cf()
            upcoming = sorted(
                [c for c in all_c if c["phase"] == "BEFORE" and should_include(c)],
                key=lambda x: x["startTimeSeconds"],
            )
            await edit_msg(channel, "MSG_UP_LIST", embed_upcoming_list(upcoming))
        except Exception as ex:
            print(f"[upcoming] {ex}")
        await asyncio.sleep(600)


async def task_announce():
    global ANNOUNCED_IDS
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not SETUP_DONE:
        await asyncio.sleep(2)

    # Mark ALL currently-known upcoming contests as already announced on startup.
    # This prevents re-announcing old contests every time the bot restarts.
    try:
        all_c = await fetch_cf()
        for c in all_c:
            if c["phase"] == "BEFORE" and should_include(c):
                ANNOUNCED_IDS.add(c["id"])
        print(f"[announce] Pre-seeded {len(ANNOUNCED_IDS)} IDs")
    except Exception as ex:
        print(f"[announce] Pre-seed error: {ex}")

    while not bot.is_closed():
        await asyncio.sleep(600)
        try:
            all_c    = await fetch_cf()
            upcoming = sorted(
                [c for c in all_c if c["phase"] == "BEFORE" and should_include(c)],
                key=lambda x: x["startTimeSeconds"],
            )
            total = len(upcoming)
            for i, c in enumerate(upcoming):
                if c["id"] not in ANNOUNCED_IDS:
                    ANNOUNCED_IDS.add(c["id"])
                    num = total - i
                    await channel.send(embed=embed_announce(c, num))
        except Exception as ex:
            print(f"[announce] {ex}")


async def task_remind():
    global REMINDED_IDS
    await bot.wait_until_ready()
    remind_ch = bot.get_channel(REMINDER_CHANNEL_ID)

    while not bot.is_closed():
        try:
            all_c   = await fetch_cf()
            now_utc = datetime.now(timezone.utc)
            for c in all_c:
                if c["phase"] != "BEFORE" or not should_include(c):
                    continue
                ts   = c["startTimeSeconds"]
                diff = (datetime.fromtimestamp(ts, tz=timezone.utc) - now_utc).total_seconds()
                if c["id"] not in REMINDED_IDS and 28_000 < diff < 29_000:
                    REMINDED_IDS.add(c["id"])
                    await remind_ch.send(embed=embed_reminder(c, hours=8))
        except Exception as ex:
            print(f"[remind] {ex}")
        await asyncio.sleep(600)


async def task_past():
    global PAST_ENTRIES, PAST_IDS, MSG_PAST_LIST
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not SETUP_DONE:
        await asyncio.sleep(2)

    while not bot.is_closed():
        try:
            all_c  = await fetch_cf()
            newly  = []
            for c in all_c:
                if c["phase"] != "FINISHED" or not should_include(c):
                    continue
                if c["id"] in PAST_IDS:
                    continue
                finish_ts = c["startTimeSeconds"] + c.get("durationSeconds", 0)
                finish_dt = datetime.fromtimestamp(finish_ts, tz=timezone.utc)
                if finish_dt <= BOT_START_UTC:
                    continue
                newly.append((finish_ts, c))

            newly.sort(key=lambda x: x[0])
            changed = False
            for _, c in newly:
                PAST_IDS.add(c["id"])
                PAST_ENTRIES.append({
                    "num":  len(PAST_ENTRIES) + 1,
                    "name": c["name"],
                    "div":  get_div(c["name"]),
                    "id":   c["id"],
                })
                changed = True
                print(f"[past] #{len(PAST_ENTRIES)}: {c['name']}")

            if changed:
                em = embed_past_list(PAST_ENTRIES)
                if len(em.description or "") > 3900:
                    chunk = PAST_ENTRIES[-25:]
                    em    = embed_past_list(chunk)
                    msg   = await channel.send(embed=em)
                    MSG_PAST_LIST = msg.id
                else:
                    await edit_msg(channel, "MSG_PAST_LIST", em)

        except Exception as ex:
            print(f"[past] {ex}")
        await asyncio.sleep(300)


@bot.command(name="upcoming")
async def cmd_upcoming(ctx):
    try:
        all_c    = await fetch_cf()
        upcoming = sorted(
            [c for c in all_c if c["phase"] == "BEFORE" and should_include(c)],
            key=lambda x: x["startTimeSeconds"],
        )
        await ctx.reply(embed=embed_upcoming_list(upcoming), mention_author=False)
    except Exception as ex:
        await ctx.reply(f"❌ Error: `{ex}`", mention_author=False)


@bot.command(name="past")
async def cmd_past(ctx, page: int = 1):
    size  = 15
    start = (page - 1) * size
    chunk = PAST_ENTRIES[start:start + size]
    if not chunk:
        await ctx.reply(
            f"❌ No entries on page **{page}**. Total: **{len(PAST_ENTRIES)}**.",
            mention_author=False,
        )
        return
    total_pages = max(1, (len(PAST_ENTRIES) + size - 1) // size)
    em = embed_past_list(chunk)
    em.set_footer(text=f"📜 Page {page}/{total_pages}  •  {OWNER_NAME}")
    await ctx.reply(embed=em, mention_author=False)


@bot.command(name="refresh")
@commands.has_permissions(manage_messages=True)
async def cmd_refresh(ctx):
    try:
        all_c    = await fetch_cf()
        upcoming = sorted(
            [c for c in all_c if c["phase"] == "BEFORE" and should_include(c)],
            key=lambda x: x["startTimeSeconds"],
        )
        channel = bot.get_channel(CHANNEL_ID)
        await edit_msg(channel, "MSG_UP_LIST", embed_upcoming_list(upcoming))
        await ctx.reply("✅ Upcoming list refreshed!", mention_author=False)
    except Exception as ex:
        await ctx.reply(f"❌ Error: `{ex}`", mention_author=False)


@bot.command(name="setcfchannel")
@commands.has_permissions(administrator=True)
async def cmd_setchannel(ctx, channel: discord.TextChannel):
    global CHANNEL_ID, MSG_PAST_HEADER, MSG_PAST_LIST, MSG_UP_HEADER, MSG_UP_LIST, SETUP_DONE
    CHANNEL_ID = channel.id
    MSG_PAST_HEADER = MSG_PAST_LIST = MSG_UP_HEADER = MSG_UP_LIST = None
    SETUP_DONE = False
    await ctx.reply(f"✅ Announce channel → {channel.mention}", mention_author=False)


@bot.command(name="setreminderch")
@commands.has_permissions(administrator=True)
async def cmd_setremind(ctx, channel: discord.TextChannel):
    global REMINDER_CHANNEL_ID
    REMINDER_CHANNEL_ID = channel.id
    await ctx.reply(f"✅ Reminder channel → {channel.mention}", mention_author=False)


@bot.command(name="cfhelp")
async def cmd_help(ctx):
    e = discord.Embed(
        title="📖  CF Bot — Commands",
        description=f"**Prefix:** `;`  ·  Data from [Codeforces API](https://codeforces.com/api/contest.list)",
        color=0x5865F2,
    )
    e.add_field(name="`;upcoming`",                     value="Show upcoming contests now.",                    inline=False)
    e.add_field(name="`;past [page]`",                  value="List tracked past contests (15/page).",          inline=False)
    e.add_field(name="`;refresh`  ⚠️ Manage Messages", value="Force-refresh the live upcoming embed.",          inline=False)
    e.add_field(name="`;setcfchannel #ch`  🔒 Admin",  value="Switch announce channel without restarting.",    inline=False)
    e.add_field(name="`;setreminderch #ch`  🔒 Admin", value="Switch reminder channel without restarting.",    inline=False)
    e.set_footer(text=f"cf.bot  •  {OWNER_NAME}")
    await ctx.reply(embed=e, mention_author=False)


@bot.event
async def on_ready():
    global BOT_START_UTC
    BOT_START_UTC = datetime.now(timezone.utc)
    print(f"✅  {bot.user} ready")
    print(f"    Announce  : {CHANNEL_ID}")
    print(f"    Reminder  : {REMINDER_CHANNEL_ID}")

    bot.loop.create_task(task_upcoming())
    bot.loop.create_task(task_past())
    bot.loop.create_task(task_announce())
    bot.loop.create_task(task_remind())


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Permission denied.", mention_author=False)
    elif isinstance(error, commands.BadArgument):
        await ctx.reply(f"❌ Bad argument: `{error}`", mention_author=False)
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"[error] {error}")


if not TOKEN:
    raise RuntimeError("TOKEN env variable is not set!")

bot.run(TOKEN)
