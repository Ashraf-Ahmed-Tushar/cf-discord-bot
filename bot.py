"""
Codeforces Contest Tracker — Discord Bot
=========================================

Channel layout (all 4 messages posted once by the bot on first run):

  ┌─────────────────────────────────────────┐
  │  [STATIC]  📜 Past Codeforces Contests  │  ← never edited
  ├─────────────────────────────────────────┤
  │  [LIVE]    Past contest list embed      │  ← edited as contests finish
  │            (may grow into multiple msgs)│
  ├─────────────────────────────────────────┤
  │  [STATIC]  📢 Upcoming Contests         │  ← never edited, acts as divider
  ├─────────────────────────────────────────┤
  │  [LIVE]    Upcoming contest list embed  │  ← edited every 10 min
  └─────────────────────────────────────────┘

Past counting starts at 1 from the moment the bot is first deployed.
Historical contests that were already finished before bot start are ignored.
"""

import discord
from discord.ext import commands
import aiohttp
import asyncio
import os
from datetime import datetime, timezone

# ───────────────────────────── ENV ─────────────────────────────
TOKEN              = os.getenv("TOKEN")
CHANNEL_ID         = int(os.getenv("CHANNEL_ID", "0"))
REMINDER_CHANNEL_ID = int(os.getenv("REMINDER_CHANNEL_ID", "0"))

# ─────────────────────────── BOT SETUP ─────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=";", intents=intents)

# ──────────────────────────── STATE ────────────────────────────
# Message IDs for the 4 managed channel messages
MSG_PAST_HEADER   = None   # static "📜 Past …" title message
MSG_PAST_LIST     = None   # editable past contest embed (latest page)
MSG_UP_HEADER     = None   # static "📢 Upcoming …" divider message
MSG_UP_LIST       = None   # editable upcoming embed

# Contests tracked since this bot run started
PAST_ENTRIES: list[dict] = []   # {"num": int, "name": str, "div": str, "id": int}
PAST_IDS: set[int]       = set()
REMINDED_IDS: set[int]   = set()
ANNOUNCED_IDS: set[int]  = set()

# Timestamp of when bot connected — only contests finishing AFTER this count
BOT_START_UTC: datetime  = None

# How many past entries fit in one embed before we start a new message
PAST_PAGE_SIZE = 25


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

async def fetch_cf() -> list:
    url = "https://codeforces.com/api/contest.list"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            data = await resp.json()
    return data["result"]


def get_division(name: str) -> str:
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


def div_color(div: str) -> int:
    """Embed left-bar colour based on division difficulty."""
    return {
        "1+2":    0xE91E63,   # pink-red   — hardest combined
        "1":      0xE74C3C,   # red        — hardest
        "2":      0xE67E22,   # orange     — medium
        "3":      0x3498DB,   # blue       — easier
        "4":      0x57F287,   # green      — easiest
        "edu":    0x1ABC9C,   # teal       — educational
        "global": 0x9B59B6,   # purple     — global rounds
        "other":  0x99AAB5,   # grey
    }.get(div, 0x99AAB5)


def div_label(div: str) -> str:
    return {
        "1+2":    "Div. 1 + Div. 2",
        "1":      "Div. 1",
        "2":      "Div. 2",
        "3":      "Div. 3",
        "4":      "Div. 4",
        "edu":    "Educational",
        "global": "Global Round",
        "other":  "Special / Other",
    }.get(div, div)


def div_medal(div: str) -> str:
    return {
        "1+2":    "🔴",
        "1":      "🔴",
        "2":      "🟠",
        "3":      "🔵",
        "4":      "🟢",
        "edu":    "🟦",
        "global": "🟣",
        "other":  "⚪",
    }.get(div, "⚪")


def should_include(contest: dict) -> bool:
    """Keep only rated contests we care about."""
    name = contest["name"].lower()
    if "unrated" in name:
        return False
    # Drop pure Div.1 (too hard for most community members)
    if "div. 1" in name and "div. 2" not in name and "global" not in name:
        return False
    return True


# ══════════════════════════════════════════════════════════════
#  EMBED BUILDERS
# ══════════════════════════════════════════════════════════════

def build_past_header_msg() -> str:
    return (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📜  PAST CODEFORCES CONTESTS\n"
        "  Contests tracked since this bot started\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```"
    )


def build_upcoming_header_msg() -> str:
    return (
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📢  UPCOMING CODEFORCES CONTESTS\n"
        "  Auto-updates every 10 minutes\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```"
    )


def build_past_list_embed(entries: list[dict]) -> discord.Embed:
    """
    entries: list of {"num": int, "name": str, "div": str}
    Each entry is one finished contest tracked since bot start.
    """
    embed = discord.Embed(color=0x2B2D31)  # dark neutral

    if not entries:
        embed.description = "*No contests have ended since the bot started.*"
        embed.set_footer(text="📜 Past Contests  •  cf.bot")
        return embed

    lines = []
    for e in entries:
        medal = div_medal(e["div"])
        lines.append(f"`{e['num']:>3}.` {medal}  **{e['name']}**")

    embed.description = "\n".join(lines)
    last = entries[-1]
    embed.set_footer(
        text=f"📜 {len(entries)} contest(s) completed  •  cf.bot"
    )
    return embed


def build_upcoming_list_embed(contests: list) -> discord.Embed:
    """
    One embed with all upcoming contests.
    Numbered in reverse: furthest = N (top), closest = 1 (bottom).
    Each contest is a rich field block.
    """
    if not contests:
        embed = discord.Embed(
            description="*No upcoming rated contests right now. Check back soon!*",
            color=0x5865F2,
        )
        embed.set_footer(text="📢 Upcoming  •  Updates every 10 min  •  cf.bot")
        return embed

    total = len(contests)
    # Use the division colour of the soonest contest for the embed stripe
    soonest_div = get_division(contests[0]["name"])
    embed = discord.Embed(color=div_color(soonest_div))

    for i, c in enumerate(contests):
        num   = total - i          # reverse numbering
        div   = get_division(c["name"])
        ts    = c["startTimeSeconds"]
        medal = div_medal(div)
        label = div_label(div)
        cid   = c["id"]

        # Field name = contest title with number + division badge
        field_name = f"{medal}  `#{num}`  {c['name']}"

        # Field value = structured info
        field_val = (
            f"🏷️  **Division:** {label}\n"
            f"📅  **Start:** <t:{ts}:F>\n"
            f"⏳  **Countdown:** <t:{ts}:R>\n"
            f"🔗  [Open on Codeforces](https://codeforces.com/contest/{cid})"
        )

        embed.add_field(name=field_name, value=field_val, inline=False)

    embed.set_footer(
        text=f"📢 {total} contest(s) upcoming  •  Updates every 10 min  •  cf.bot"
    )
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_announce_embed(contest: dict) -> discord.Embed:
    div = get_division(contest["name"])
    ts  = contest["startTimeSeconds"]
    cid = contest["id"]
    embed = discord.Embed(
        title="📢  New Contest Announced!",
        color=div_color(div),
    )
    embed.add_field(
        name="🏆  Contest",
        value=f"**{contest['name']}**",
        inline=False,
    )
    embed.add_field(
        name="🏷️  Division",
        value=div_label(div),
        inline=True,
    )
    embed.add_field(
        name="📅  Start Time",
        value=f"<t:{ts}:F>",
        inline=True,
    )
    embed.add_field(
        name="⏳  Countdown",
        value=f"<t:{ts}:R>",
        inline=False,
    )
    embed.add_field(
        name="🔗  Link",
        value=f"[codeforces.com/contest/{cid}](https://codeforces.com/contest/{cid})",
        inline=False,
    )
    embed.set_footer(text="Codeforces  •  cf.bot")
    return embed


def build_reminder_embed(contest: dict, hours: int) -> discord.Embed:
    div = get_division(contest["name"])
    ts  = contest["startTimeSeconds"]
    cid = contest["id"]
    embed = discord.Embed(
        title=f"⏰  Reminder — {hours} hours left!",
        color=0xFEE75C,
    )
    embed.add_field(
        name="🏆  Contest",
        value=f"**{contest['name']}**",
        inline=False,
    )
    embed.add_field(
        name="🏷️  Division",
        value=div_label(div),
        inline=True,
    )
    embed.add_field(
        name="📅  Start Time",
        value=f"<t:{ts}:F>",
        inline=True,
    )
    embed.add_field(
        name="⏳  Time Left",
        value=f"<t:{ts}:R>",
        inline=False,
    )
    embed.add_field(
        name="🔗  Join",
        value=f"[codeforces.com/contest/{cid}](https://codeforces.com/contest/{cid})",
        inline=False,
    )
    embed.set_footer(text="Codeforces  •  cf.bot")
    return embed


# ══════════════════════════════════════════════════════════════
#  CHANNEL SETUP  (run once on startup)
# ══════════════════════════════════════════════════════════════

PAST_HEADER_MARKER   = "PAST CODEFORCES CONTESTS"
UPCOMING_HEADER_MARKER = "UPCOMING CODEFORCES CONTESTS"
PAST_LIST_FOOTER     = "📜"      # footer starts with this
UPCOMING_LIST_FOOTER = "📢"      # footer starts with this


async def setup_channel(channel: discord.TextChannel):
    """
    Scan channel history to find existing bot messages.
    If not found, post them in order:
      past_header → past_list → upcoming_header → upcoming_list
    """
    global MSG_PAST_HEADER, MSG_PAST_LIST, MSG_UP_HEADER, MSG_UP_LIST

    print("[setup] Scanning channel history…")

    # Collect bot messages (newest first from Discord)
    bot_msgs: list[discord.Message] = []
    async for msg in channel.history(limit=200):
        if msg.author == bot.user:
            bot_msgs.append(msg)

    # Reverse so we iterate oldest → newest
    bot_msgs.reverse()

    for msg in bot_msgs:
        content = msg.content or ""
        # Static headers (plain text codeblock)
        if PAST_HEADER_MARKER in content:
            MSG_PAST_HEADER = msg.id
            print(f"[setup] Found past header: {msg.id}")
        elif UPCOMING_HEADER_MARKER in content:
            MSG_UP_HEADER = msg.id
            print(f"[setup] Found upcoming header: {msg.id}")
        # Live embeds identified by footer prefix
        elif msg.embeds:
            em = msg.embeds[0]
            footer_text = em.footer.text if em.footer else ""
            if footer_text.startswith(PAST_LIST_FOOTER):
                MSG_PAST_LIST = msg.id
                print(f"[setup] Found past list: {msg.id}")
            elif footer_text.startswith(UPCOMING_LIST_FOOTER):
                MSG_UP_LIST = msg.id
                print(f"[setup] Found upcoming list: {msg.id}")

    # Post any missing messages in the right order
    if not MSG_PAST_HEADER:
        m = await channel.send(build_past_header_msg())
        MSG_PAST_HEADER = m.id
        print(f"[setup] Posted past header: {m.id}")
        await asyncio.sleep(0.5)

    if not MSG_PAST_LIST:
        embed = build_past_list_embed([])
        m = await channel.send(embed=embed)
        MSG_PAST_LIST = m.id
        print(f"[setup] Posted past list: {m.id}")
        await asyncio.sleep(0.5)

    if not MSG_UP_HEADER:
        m = await channel.send(build_upcoming_header_msg())
        MSG_UP_HEADER = m.id
        print(f"[setup] Posted upcoming header: {m.id}")
        await asyncio.sleep(0.5)

    if not MSG_UP_LIST:
        embed = build_upcoming_list_embed([])
        m = await channel.send(embed=embed)
        MSG_UP_LIST = m.id
        print(f"[setup] Posted upcoming list: {m.id}")

    print("[setup] Channel ready ✅")


# ══════════════════════════════════════════════════════════════
#  BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════

async def _edit_or_repost(channel: discord.TextChannel, msg_id_attr: str, embed: discord.Embed):
    """Helper: edit an existing message or repost if deleted."""
    global MSG_PAST_LIST, MSG_UP_LIST

    msg_id = globals()[msg_id_attr]
    if msg_id:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(embed=embed)
            return
        except (discord.NotFound, discord.HTTPException):
            pass
    # Re-post
    msg = await channel.send(embed=embed)
    globals()[msg_id_attr] = msg.id


async def task_upcoming():
    """Every 10 min: refresh the upcoming embed in place."""
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    await setup_channel(channel)

    while not bot.is_closed():
        try:
            all_c = await fetch_cf()
            upcoming = sorted(
                [c for c in all_c if c["phase"] == "BEFORE" and should_include(c)],
                key=lambda x: x["startTimeSeconds"],
            )
            embed = build_upcoming_list_embed(upcoming)
            await _edit_or_repost(channel, "MSG_UP_LIST", embed)
        except Exception as e:
            print(f"[upcoming] {e}")
        await asyncio.sleep(600)


async def task_past():
    """
    Every 5 min: detect contests that finished AFTER bot start.
    Append them to PAST_ENTRIES and edit the past list embed.
    """
    global PAST_ENTRIES, PAST_IDS, MSG_PAST_LIST

    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    # Wait for setup_channel to finish (task_upcoming runs setup)
    await asyncio.sleep(8)

    while not bot.is_closed():
        try:
            all_c = await fetch_cf()

            # Finished contests that ended AFTER bot start and pass filter
            newly_done = []
            for c in all_c:
                if c["phase"] != "FINISHED":
                    continue
                if not should_include(c):
                    continue
                if c["id"] in PAST_IDS:
                    continue
                # "startTimeSeconds" is when it started; durationSeconds is length
                # We approximate finish time as start + duration
                finish_ts = c["startTimeSeconds"] + c.get("durationSeconds", 0)
                finish_dt = datetime.fromtimestamp(finish_ts, tz=timezone.utc)
                if finish_dt <= BOT_START_UTC:
                    continue
                newly_done.append((finish_ts, c))

            # Sort by finish time so numbering is chronological
            newly_done.sort(key=lambda x: x[0])

            changed = False
            for _, c in newly_done:
                PAST_IDS.add(c["id"])
                num = len(PAST_ENTRIES) + 1
                div = get_division(c["name"])
                PAST_ENTRIES.append({"num": num, "name": c["name"], "div": div, "id": c["id"]})
                changed = True
                print(f"[past] New entry #{num}: {c['name']}")

            if changed:
                # If current page is full, send a new message
                page_entries = PAST_ENTRIES[-PAST_PAGE_SIZE:]  # show latest page
                embed = build_past_list_embed(PAST_ENTRIES)

                # Discord embed description limit is 4096 — if exceeded, send new msg
                test_embed = build_past_list_embed(PAST_ENTRIES)
                if len(test_embed.description or "") > 3900:
                    # Trim: show only last PAST_PAGE_SIZE entries in this message,
                    # post a new message for the overflow
                    overflow = PAST_ENTRIES[-PAST_PAGE_SIZE:]
                    embed = build_past_list_embed(overflow)
                    msg = await channel.send(embed=embed)
                    MSG_PAST_LIST = msg.id
                else:
                    await _edit_or_repost(channel, "MSG_PAST_LIST", embed)

        except Exception as e:
            print(f"[past] {e}")

        await asyncio.sleep(300)


async def task_announce_remind():
    """
    Every 10 min:
      • Announce newly found upcoming contests (embed + @everyone ping)
      • Send 8-hour reminder when contest is ~8h away
    """
    global ANNOUNCED_IDS, REMINDED_IDS

    await bot.wait_until_ready()
    announce_ch = bot.get_channel(CHANNEL_ID)
    remind_ch   = bot.get_channel(REMINDER_CHANNEL_ID)

    # Pre-seed announced IDs from recent channel history so we don't re-ping on restart
    try:
        async for msg in announce_ch.history(limit=100):
            if msg.author == bot.user and msg.embeds:
                for em in msg.embeds:
                    if em.title and "New Contest Announced" in em.title:
                        for field in em.fields:
                            if "codeforces.com/contest/" in (field.value or ""):
                                try:
                                    cid = int(field.value.split("contest/")[1].split(")")[0])
                                    ANNOUNCED_IDS.add(cid)
                                except (ValueError, IndexError):
                                    pass
    except Exception:
        pass

    while not bot.is_closed():
        try:
            all_c   = await fetch_cf()
            now_utc = datetime.now(timezone.utc)

            upcoming = sorted(
                [c for c in all_c if c["phase"] == "BEFORE" and should_include(c)],
                key=lambda x: x["startTimeSeconds"],
            )

            for c in upcoming:
                cid  = c["id"]
                ts   = c["startTimeSeconds"]
                diff = datetime.fromtimestamp(ts, tz=timezone.utc) - now_utc
                diff_secs = diff.total_seconds()

                # ── Announce ──
                if cid not in ANNOUNCED_IDS:
                    ANNOUNCED_IDS.add(cid)
                    embed = build_announce_embed(c)
                    await announce_ch.send(embed=embed)
                    await announce_ch.send("Best of luck! ||@everyone|| 🍀")

                # ── 8-hour reminder (28 000 – 29 000 s window) ──
                if cid not in REMINDED_IDS and 28_000 < diff_secs < 29_000:
                    REMINDED_IDS.add(cid)
                    embed = build_reminder_embed(c, hours=8)
                    await remind_ch.send(embed=embed)
                    await remind_ch.send("Best of luck! ||@everyone|| 🍀")

        except Exception as e:
            print(f"[announce/remind] {e}")

        await asyncio.sleep(600)


# ══════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command(name="upcoming")
async def cmd_upcoming(ctx):
    """Show current upcoming contests."""
    try:
        all_c = await fetch_cf()
        upcoming = sorted(
            [c for c in all_c if c["phase"] == "BEFORE" and should_include(c)],
            key=lambda x: x["startTimeSeconds"],
        )
        await ctx.reply(embed=build_upcoming_list_embed(upcoming), mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Error: `{e}`", mention_author=False)


@bot.command(name="past")
async def cmd_past(ctx, page: int = 1):
    """Show past contests tracked by the bot. Usage: ;past [page]"""
    page_size = 15
    start = (page - 1) * page_size
    end   = start + page_size
    chunk = PAST_ENTRIES[start:end]

    if not chunk:
        await ctx.reply(
            f"❌ No entries on page **{page}**. Total tracked: **{len(PAST_ENTRIES)}**.",
            mention_author=False,
        )
        return

    embed = build_past_list_embed(chunk)
    total_pages = max(1, (len(PAST_ENTRIES) + page_size - 1) // page_size)
    embed.set_footer(text=f"📜 Page {page}/{total_pages}  •  cf.bot")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="refresh")
@commands.has_permissions(manage_messages=True)
async def cmd_refresh(ctx):
    """Force-refresh the upcoming list embed right now."""
    try:
        all_c = await fetch_cf()
        upcoming = sorted(
            [c for c in all_c if c["phase"] == "BEFORE" and should_include(c)],
            key=lambda x: x["startTimeSeconds"],
        )
        channel = bot.get_channel(CHANNEL_ID)
        await _edit_or_repost(channel, "MSG_UP_LIST", build_upcoming_list_embed(upcoming))
        await ctx.reply("✅ Upcoming list refreshed!", mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Error: `{e}`", mention_author=False)


@bot.command(name="setcfchannel")
@commands.has_permissions(administrator=True)
async def cmd_setchannel(ctx, channel: discord.TextChannel):
    """(Admin) Change the announce channel at runtime."""
    global CHANNEL_ID, MSG_PAST_HEADER, MSG_PAST_LIST, MSG_UP_HEADER, MSG_UP_LIST
    CHANNEL_ID = channel.id
    MSG_PAST_HEADER = MSG_PAST_LIST = MSG_UP_HEADER = MSG_UP_LIST = None
    await ctx.reply(
        f"✅ Announce channel → {channel.mention}\n"
        "Bot will post fresh messages there on next cycle.",
        mention_author=False,
    )


@bot.command(name="setreminderch")
@commands.has_permissions(administrator=True)
async def cmd_setremind(ctx, channel: discord.TextChannel):
    """(Admin) Change the reminder channel at runtime."""
    global REMINDER_CHANNEL_ID
    REMINDER_CHANNEL_ID = channel.id
    await ctx.reply(f"✅ Reminder channel → {channel.mention}", mention_author=False)


@bot.command(name="cfhelp")
async def cmd_help(ctx):
    """List all commands."""
    embed = discord.Embed(
        title="📖  CF Bot — Help",
        description="**Prefix:** `;`\nAll contest data is from the [Codeforces API](https://codeforces.com/api/contest.list).",
        color=0x5865F2,
    )
    embed.add_field(
        name="`;upcoming`",
        value="Show all upcoming rated contests right now.",
        inline=False,
    )
    embed.add_field(
        name="`;past [page]`",
        value="List past contests tracked by the bot (15 per page).\nExample: `;past 2`",
        inline=False,
    )
    embed.add_field(
        name="`;refresh`  ⚠️ *Manage Messages*",
        value="Force-refresh the live upcoming embed immediately.",
        inline=False,
    )
    embed.add_field(
        name="`;setcfchannel #channel`  🔒 *Admin*",
        value="Switch the announce channel without restarting.",
        inline=False,
    )
    embed.add_field(
        name="`;setreminderch #channel`  🔒 *Admin*",
        value="Switch the reminder channel without restarting.",
        inline=False,
    )
    embed.set_footer(text="cf.bot  •  Codeforces Contest Tracker")
    await ctx.reply(embed=embed, mention_author=False)


# ══════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    global BOT_START_UTC
    BOT_START_UTC = datetime.now(timezone.utc)
    print(f"✅  Logged in as {bot.user} ({bot.user.id})")
    print(f"    Announce channel  : {CHANNEL_ID}")
    print(f"    Reminder channel  : {REMINDER_CHANNEL_ID}")
    print(f"    Bot start UTC     : {BOT_START_UTC.isoformat()}")

    bot.loop.create_task(task_upcoming())        # also runs setup_channel
    bot.loop.create_task(task_past())
    bot.loop.create_task(task_announce_remind())


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ You don't have permission for that command.", mention_author=False)
    elif isinstance(error, commands.BadArgument):
        await ctx.reply(f"❌ Bad argument: `{error}`", mention_author=False)
    elif isinstance(error, commands.CommandNotFound):
        pass  # silently ignore unknown commands
    else:
        print(f"[error] {error}")


# ══════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════

if not TOKEN:
    raise RuntimeError("TOKEN environment variable is not set!")

bot.run(TOKEN)
