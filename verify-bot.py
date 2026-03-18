"""
CF Verify Bot
=============

Features:
  • CF handle verification via Codeforces profile first-name token
  • Auto role assignment based on CF rating (Newbie → LGM)
  • Post-contest leaderboard with rating changes for all verified members
  • Role-based help: admins see everything, members see their own commands

ENV vars required:
  TOKEN                  — Discord bot token
  GUILD_ID               — Your server's guild ID
  LEADERBOARD_CHANNEL_ID — Channel where post-contest leaderboard is posted
  ADMIN_ROLE_NAME        — Name of admin/owner role (default: "Admin")
  MONGODB_URI            — MongoDB Atlas connection string
  MONGO_DB_NAME          — Database name (default: cfverify)
"""

import discord
from discord.ext import commands
import aiohttp
import asyncio
import os
import random
import string
from datetime import datetime, timezone

import db

# ─── ENV ────────────────────────────────────────────────────────────────────
TOKEN          = os.getenv("TOKEN")
GUILD_ID       = int(os.getenv("GUILD_ID", "0"))
LB_CHANNEL_ID  = int(os.getenv("LEADERBOARD_CHANNEL_ID", "0"))
ADMIN_ROLE     = os.getenv("ADMIN_ROLE_NAME", "Admin")

# ─── BOT ────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members          = True
bot = commands.Bot(command_prefix=";", intents=intents, help_command=None)

# ─── CF RANK CONFIG ──────────────────────────────────────────────────────────
# Each entry: (min_rating, role_name, colour_hex, emoji)
CF_RANKS = [
    (3000, "Legendary Grandmaster", 0xFF0000, "🔴"),
    (2600, "International Grandmaster", 0xFF0000, "🔴"),
    (2400, "Grandmaster",              0xFF0000, "🔴"),
    (2300, "International Master",     0xFF8C00, "🟠"),
    (2100, "Master",                   0xFF8C00, "🟠"),
    (1900, "Candidate Master",         0xAA00AA, "🟣"),
    (1600, "Expert",                   0x0070FF, "🔵"),
    (1400, "Specialist",               0x03A89E, "🩵"),
    (1200, "Pupil",                    0x008000, "🟢"),
    (0,    "Newbie",                   0x808080, "⚪"),
]


def rank_for_rating(rating: int):
    for min_r, name, colour, emoji in CF_RANKS:
        if rating >= min_r:
            return name, colour, emoji
    return "Newbie", 0x808080, "⚪"


def token_for(discord_id: int) -> str:
    """Deterministic-random 8-char verification token."""
    rng   = random.Random(discord_id ^ 0xDEADBEEF)
    chars = string.ascii_lowercase + string.digits
    tag   = "".join(rng.choices(chars, k=8))
    return f"cfbot-{tag}"


# ─── CF API HELPERS ──────────────────────────────────────────────────────────

async def cf_user_info(handle: str) -> dict | None:
    url = f"https://codeforces.com/api/user.info?handles={handle}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json()
    if data.get("status") != "OK":
        return None
    return data["result"][0]


async def cf_rating_changes(contest_id: int) -> list:
    url = f"https://codeforces.com/api/contest.ratingChanges?contestId={contest_id}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json()
    if data.get("status") != "OK":
        return []
    return data["result"]


async def cf_recent_rated_contests(count: int = 5) -> list:
    url = "https://codeforces.com/api/contest.list?gym=false"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json()
    if data.get("status") != "OK":
        return []
    finished = [
        c for c in data["result"]
        if c["phase"] == "FINISHED" and c.get("type") in ("CF", "ICPC", "IOI")
        and "unrated" not in c["name"].lower()
    ]
    return finished[:count]


# ─── ROLE HELPERS ────────────────────────────────────────────────────────────

def is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.name == ADMIN_ROLE for r in member.roles)


async def ensure_rank_role(guild: discord.Guild, role_name: str, colour: int) -> discord.Role:
    """Find or create a CF-rank role."""
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        role = await guild.create_role(
            name=role_name,
            color=discord.Color(colour),
            reason="CF Verify Bot — rank role",
        )
    return role


async def assign_rank_role(member: discord.Member, rating: int):
    """Remove old CF rank roles and assign the correct one."""
    guild      = member.guild
    rank_names = {r[1] for r in CF_RANKS}

    # Remove old CF rank roles
    to_remove = [r for r in member.roles if r.name in rank_names]
    if to_remove:
        await member.remove_roles(*to_remove, reason="CF rank update")

    role_name, colour, _ = rank_for_rating(rating)
    role = await ensure_rank_role(guild, role_name, colour)
    await member.add_roles(role, reason=f"CF rank: {role_name} ({rating})")
    return role_name


# ─── EMBEDS ──────────────────────────────────────────────────────────────────

def embed_profile(member: discord.Member, info: dict) -> discord.Embed:
    rating    = info.get("rating", 0)
    max_r     = info.get("maxRating", 0)
    rank_name = info.get("rank", "Unrated").title()
    handle    = info["handle"]
    _, colour, emoji = rank_for_rating(rating)

    e = discord.Embed(
        title=f"{emoji}  {handle}",
        url=f"https://codeforces.com/profile/{handle}",
        color=colour,
    )
    if member.display_avatar:
        e.set_thumbnail(url=member.display_avatar.url)
    if info.get("avatar") and info["avatar"] != "//userpic.codeforces.org/no-avatar.jpg":
        e.set_thumbnail(url="https:" + info["avatar"])

    e.add_field(name="🏷️  Rank",        value=rank_name,       inline=True)
    e.add_field(name="📈  Rating",       value=f"**{rating}**", inline=True)
    e.add_field(name="🏆  Max Rating",   value=f"**{max_r}**",  inline=True)
    e.add_field(name="👤  Discord",      value=member.mention,  inline=True)
    e.add_field(
        name="🌍  Country",
        value=info.get("country", "—"),
        inline=True,
    )
    e.add_field(
        name="🔗  Profile",
        value=f"[codeforces.com/profile/{handle}](https://codeforces.com/profile/{handle})",
        inline=False,
    )
    e.set_footer(text="CF Verify Bot")
    return e


def embed_verify_dm(handle: str, token: str) -> discord.Embed:
    e = discord.Embed(
        title="🔐  Verify Your Codeforces Account",
        description=(
            f"To verify **{handle}** follow these steps:\n\n"
            f"**1.** Go to → https://codeforces.com/settings/general\n"
            f"**2.** Set your **First name** field to exactly:\n"
            f"```{token}```"
            f"**3.** Save, then go back to your server and type `;confirm`\n\n"
            f"You can remove it from your profile after verification ✅"
        ),
        color=0x5865F2,
    )
    e.set_footer(text="Token expires if you run ;verify again")
    return e


def embed_leaderboard(contest: dict, changes: list, member_handles: set) -> discord.Embed:
    """
    Build a rich leaderboard embed from contest.ratingChanges data.
    Only includes server members who participated.
    """
    cid      = contest["id"]
    name     = contest["name"]
    end_ts   = contest["startTimeSeconds"] + contest.get("durationSeconds", 0)

    # Filter to server members only
    results = [c for c in changes if c["handle"].lower() in member_handles]
    if not results:
        return None

    # Sort: biggest rating gain first
    results.sort(key=lambda x: x["newRating"] - x["oldRating"], reverse=True)

    e = discord.Embed(
        title=f"🏆  Contest Leaderboard",
        description=f"**{name}**\n[View on Codeforces](https://codeforces.com/contest/{cid})",
        color=0xF1C40F,
        timestamp=datetime.fromtimestamp(end_ts, tz=timezone.utc),
    )

    medals   = ["🥇", "🥈", "🥉"]
    lines    = []
    for pos, r in enumerate(results):
        handle    = r["handle"]
        old_r     = r["oldRating"]
        new_r     = r["newRating"]
        delta     = new_r - old_r
        rank_out  = r.get("rank", "?")
        delta_str = f"+{delta}" if delta >= 0 else str(delta)
        arrow     = "📈" if delta > 0 else ("📉" if delta < 0 else "➡️")
        _, _, emoji = rank_for_rating(new_r)
        badge     = medals[pos] if pos < 3 else f"`#{pos+1}`"
        lines.append(
            f"{badge}  {emoji} **{handle}**\n"
            f"    {arrow} `{old_r}` → `{new_r}`  **({delta_str})**  ·  Rank **#{rank_out}**"
        )

    # Split into pages of 10 to avoid embed limit
    page = "\n\n".join(lines[:15])
    e.add_field(name="📊  Results (server members)", value=page or "—", inline=False)

    total_up   = sum(1 for r in results if r["newRating"] > r["oldRating"])
    total_down = sum(1 for r in results if r["newRating"] < r["oldRating"])
    e.add_field(name="📈 Rating up",   value=str(total_up),   inline=True)
    e.add_field(name="📉 Rating down", value=str(total_down), inline=True)
    e.add_field(name="👥 Participated", value=str(len(results)), inline=True)

    e.set_footer(text=f"Contest #{cid}  •  CF Verify Bot")
    return e


def embed_server_leaderboard(rows) -> discord.Embed:
    if not rows:
        e = discord.Embed(description="No verified members yet.", color=0x2B2D31)
        return e

    sorted_rows = sorted(rows, key=lambda r: r["rating"], reverse=True)
    lines       = []
    medals      = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(sorted_rows):
        badge    = medals[i] if i < 3 else f"`#{i+1}`"
        _, _, em = rank_for_rating(row["rating"])
        lines.append(f"{badge}  {em} **{row['cf_handle']}** — `{row['rating']}` ({row['cf_rank']})")

    e = discord.Embed(
        title="🏅  Server Rating Leaderboard",
        description="\n".join(lines[:25]),
        color=0xF1C40F,
    )
    e.set_footer(text=f"Top {min(len(sorted_rows), 25)} of {len(sorted_rows)} verified members  •  CF Verify Bot")
    e.timestamp = datetime.now(timezone.utc)
    return e


# ─── VERIFY COMMANDS ─────────────────────────────────────────────────────────

@bot.command(name="verify")
async def cmd_verify(ctx, handle: str = None):
    """Start CF handle verification. Usage: ;verify your_cf_handle"""
    await ctx.message.delete(delay=3)

    if not handle:
        await ctx.send("❌ Usage: `;verify your_cf_handle`", delete_after=10)
        return

    # Check if someone else already has this handle
    existing = await db.get_user_by_handle(handle)
    if existing and existing["discord_id"] != ctx.author.id:
        await ctx.send("❌ That handle is already linked to another member.", delete_after=10)
        return

    # Check CF handle exists
    info = await cf_user_info(handle)
    if not info:
        await ctx.send(f"❌ Handle `{handle}` not found on Codeforces.", delete_after=10)
        return

    token = token_for(ctx.author.id)
    await db.set_pending(ctx.author.id, info["handle"], token)

    try:
        await ctx.author.send(embed=embed_verify_dm(info["handle"], token))
        await ctx.send(
            f"📬  {ctx.author.mention} Check your DMs for verification instructions!",
            delete_after=15,
        )
    except discord.Forbidden:
        await ctx.send(
            "❌ I can't DM you. Please enable DMs from server members and try again.",
            delete_after=15,
        )


@bot.command(name="confirm")
async def cmd_confirm(ctx):
    """Confirm verification after adding token to CF profile."""
    await ctx.message.delete(delay=3)

    pending = await db.get_pending(ctx.author.id)
    if not pending:
        await ctx.send(
            "❌ No pending verification. Start with `;verify your_handle` first.",
            delete_after=10,
        )
        return

    cf_handle = pending["cf_handle"]
    token     = pending["token"]

    await ctx.send(f"🔍 Checking your Codeforces profile…", delete_after=5)
    info = await cf_user_info(cf_handle)

    if not info:
        await ctx.send("❌ Could not fetch your CF profile. Try again later.", delete_after=10)
        return

    first_name = (info.get("firstName") or "").strip()
    if token not in first_name:
        await ctx.send(
            f"❌ Token not found in your CF profile first name.\n"
            f"Make sure you saved `{token}` as your **First Name** at "
            f"https://codeforces.com/settings/general — then try `;confirm` again.",
            delete_after=20,
        )
        return

    # Verified!
    rating   = info.get("rating", 0)
    cf_rank  = info.get("rank", "Newbie")
    guild    = bot.get_guild(GUILD_ID)
    member   = guild.get_member(ctx.author.id)

    await db.upsert_user(ctx.author.id, GUILD_ID, cf_handle, rating, cf_rank)
    await db.delete_pending(ctx.author.id)

    role_name = await assign_rank_role(member, rating)

    # Try to update nickname
    try:
        await member.edit(nick=f"{cf_handle} [{rating}]")
    except discord.Forbidden:
        pass

    _, colour, emoji = rank_for_rating(rating)
    e = discord.Embed(
        title="✅  Verification Successful!",
        description=(
            f"**{cf_handle}** has been linked to {ctx.author.mention}\n\n"
            f"{emoji}  **Rank:** {cf_rank.title()}\n"
            f"📈  **Rating:** {rating}\n"
            f"🎭  **Role assigned:** {role_name}\n\n"
            f"You can now remove the token from your CF profile."
        ),
        color=colour,
    )
    e.set_footer(text="CF Verify Bot")
    await ctx.send(embed=e)


@bot.command(name="profile")
async def cmd_profile(ctx, member: discord.Member = None):
    """Show CF profile. Usage: ;profile [@user]"""
    target = member or ctx.author
    row    = await db.get_user(target.id)
    if not row:
        await ctx.reply(
            f"❌ {target.mention} is not verified yet. Use `;verify <handle>` to get started.",
            mention_author=False,
        )
        return
    info = await cf_user_info(row["cf_handle"])
    if not info:
        await ctx.reply("❌ Could not fetch CF data. Try again later.", mention_author=False)
        return
    await ctx.reply(embed=embed_profile(target, info), mention_author=False)


@bot.command(name="serverrank")
async def cmd_serverrank(ctx):
    """Show server rating leaderboard (verified members only)."""
    rows = await db.all_users(GUILD_ID)
    await ctx.reply(embed=embed_server_leaderboard(rows), mention_author=False)


# ─── ADMIN COMMANDS ──────────────────────────────────────────────────────────

@bot.command(name="updateall")
async def cmd_updateall(ctx):
    """[Admin] Refresh CF ratings & roles for all verified members."""
    if not is_admin(ctx.author):
        await ctx.reply("❌ Admin only.", mention_author=False)
        return

    msg   = await ctx.reply("⏳ Updating all members…", mention_author=False)
    guild = bot.get_guild(GUILD_ID)
    rows  = await db.all_users(GUILD_ID)
    done  = 0

    for row in rows:
        info = await cf_user_info(row["cf_handle"])
        if not info:
            continue
        rating  = info.get("rating", 0)
        cf_rank = info.get("rank", "Newbie")
        member  = guild.get_member(row["discord_id"])
        if not member:
            continue
        await db.update_rating(row["discord_id"], rating, cf_rank)
        await assign_rank_role(member, rating)
        try:
            await member.edit(nick=f"{row['cf_handle']} [{rating}]")
        except discord.Forbidden:
            pass
        done += 1
        await asyncio.sleep(0.5)   # be gentle with CF API

    await msg.edit(content=f"✅ Updated **{done}** member(s).")


@bot.command(name="forceupdate")
async def cmd_forceupdate(ctx, member: discord.Member = None):
    """[Admin] Force-refresh a specific member's CF rating & role."""
    if not is_admin(ctx.author):
        await ctx.reply("❌ Admin only.", mention_author=False)
        return
    if not member:
        await ctx.reply("❌ Mention a member: `;forceupdate @user`", mention_author=False)
        return

    row = await db.get_user(member.id)
    if not row:
        await ctx.reply(f"❌ {member.mention} is not verified.", mention_author=False)
        return

    info = await cf_user_info(row["cf_handle"])
    if not info:
        await ctx.reply("❌ CF API error. Try again later.", mention_author=False)
        return

    rating  = info.get("rating", 0)
    cf_rank = info.get("rank", "Newbie")
    guild   = bot.get_guild(GUILD_ID)

    await db.update_rating(member.id, rating, cf_rank)
    role_name = await assign_rank_role(member, rating)
    try:
        await member.edit(nick=f"{row['cf_handle']} [{rating}]")
    except discord.Forbidden:
        pass

    await ctx.reply(
        f"✅ Updated {member.mention}: **{row['cf_handle']}** → `{rating}` ({cf_rank.title()}) · Role: **{role_name}**",
        mention_author=False,
    )


@bot.command(name="unverify")
async def cmd_unverify(ctx, member: discord.Member = None):
    """[Admin] Remove a member's CF verification."""
    if not is_admin(ctx.author):
        await ctx.reply("❌ Admin only.", mention_author=False)
        return
    if not member:
        await ctx.reply("❌ Mention a member: `;unverify @user`", mention_author=False)
        return

    row = await db.get_user(member.id)
    if not row:
        await ctx.reply(f"❌ {member.mention} is not verified.", mention_author=False)
        return

    await db.delete_user(member.id)
    # Remove rank roles
    rank_names = {r[1] for r in CF_RANKS}
    to_remove  = [r for r in member.roles if r.name in rank_names]
    if to_remove:
        await member.remove_roles(*to_remove, reason="Unverified by admin")

    await ctx.reply(
        f"✅ {member.mention} (`{row['cf_handle']}`) has been unverified.",
        mention_author=False,
    )


@bot.command(name="leaderboard")
async def cmd_leaderboard(ctx, contest_id: int = None):
    """[Admin] Post leaderboard for a specific contest ID."""
    if not is_admin(ctx.author):
        await ctx.reply("❌ Admin only.", mention_author=False)
        return
    if not contest_id:
        await ctx.reply("❌ Usage: `;leaderboard <contest_id>`", mention_author=False)
        return

    await ctx.reply(f"⏳ Fetching results for contest `{contest_id}`…", mention_author=False)
    await post_leaderboard(contest_id, force=True)


async def post_leaderboard(contest_id: int, force: bool = False):
    if not force and await db.lb_already_posted(contest_id):
        return

    lb_channel = bot.get_channel(LB_CHANNEL_ID)
    if not lb_channel:
        return

    rows           = await db.all_users(GUILD_ID)
    member_handles = {r["cf_handle"].lower() for r in rows}

    if not member_handles:
        return

    changes = await cf_rating_changes(contest_id)
    if not changes:
        return

    # Build a fake contest dict from what we know
    contest_info = {"id": contest_id, "name": changes[0].get("contestName", f"Contest {contest_id}"),
                    "startTimeSeconds": 0, "durationSeconds": 0}

    emb = embed_leaderboard(contest_info, changes, member_handles)
    if not emb:
        return

    await db.mark_lb_posted(contest_id)
    await lb_channel.send(embed=emb)


# ─── BACKGROUND TASK  ─────────────────────────────────────────────────────────

async def task_auto_leaderboard():
    """Every 15 min: check for newly finished rated contests and post leaderboard."""
    await bot.wait_until_ready()
    await asyncio.sleep(30)   # give bot time to settle

    while not bot.is_closed():
        try:
            contests = await cf_recent_rated_contests(count=8)
            now_ts   = datetime.now(timezone.utc).timestamp()
            for c in contests:
                end_ts = c["startTimeSeconds"] + c.get("durationSeconds", 0)
                # Only process if it finished in the last 3 hours
                if 0 < (now_ts - end_ts) < 10_800:
                    await post_leaderboard(c["id"])
        except Exception as ex:
            print(f"[lb-task] {ex}")
        await asyncio.sleep(900)   # 15 minutes


# ─── HELP COMMAND ────────────────────────────────────────────────────────────

PUBLIC_HELP = discord.Embed(
    title="📖  CF Verify Bot  —  Commands",
    description="**Prefix:** `;`",
    color=0x5865F2,
)
PUBLIC_HELP.add_field(
    name="`;verify <cf_handle>`",
    value="Link your Codeforces account. You'll get a DM with steps.",
    inline=False,
)
PUBLIC_HELP.add_field(
    name="`;confirm`",
    value="Confirm after adding the verification token to your CF profile.",
    inline=False,
)
PUBLIC_HELP.add_field(
    name="`;profile [@user]`",
    value="View your (or another member's) CF profile card.",
    inline=False,
)
PUBLIC_HELP.add_field(
    name="`;serverrank`",
    value="See the server's top rated members leaderboard.",
    inline=False,
)
PUBLIC_HELP.set_footer(text="CF Verify Bot")


def make_admin_help() -> discord.Embed:
    e = PUBLIC_HELP.copy()
    e.title = "📖  CF Verify Bot  —  All Commands  🔒"
    e.add_field(
        name="─── Admin Only ───",
        value="\u200b",
        inline=False,
    )
    e.add_field(
        name="`;updateall`",
        value="Refresh CF ratings & roles for every verified member.",
        inline=False,
    )
    e.add_field(
        name="`;forceupdate @user`",
        value="Force-refresh a specific member's rating & role.",
        inline=False,
    )
    e.add_field(
        name="`;unverify @user`",
        value="Remove a member's CF verification entirely.",
        inline=False,
    )
    e.add_field(
        name="`;leaderboard <contest_id>`",
        value="Manually post the leaderboard for a given contest ID.",
        inline=False,
    )
    return e


@bot.command(name="help")
async def cmd_help(ctx):
    if is_admin(ctx.author):
        await ctx.reply(embed=make_admin_help(), mention_author=False)
    else:
        await ctx.reply(embed=PUBLIC_HELP, mention_author=False)


# ─── EVENTS ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await db.init_db()
    print(f"✅  {bot.user} ready")
    print(f"    Guild          : {GUILD_ID}")
    print(f"    LB Channel     : {LB_CHANNEL_ID}")
    bot.loop.create_task(task_auto_leaderboard())


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Missing argument. Use `;help` for usage.", mention_author=False)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.reply("❌ Member not found.", mention_author=False)
    elif isinstance(error, commands.BadArgument):
        await ctx.reply(f"❌ Bad argument: `{error}`", mention_author=False)
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"[error] {error}")


# ─── RUN ─────────────────────────────────────────────────────────────────────

if not TOKEN:
    raise RuntimeError("TOKEN env variable is not set!")

bot.run(TOKEN)
