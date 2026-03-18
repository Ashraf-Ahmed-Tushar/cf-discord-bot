"""
MongoDB helper for CF Verify Bot  (using motor — async driver)

Collections:
  users      — verified members
  pending    — mid-verification sessions
  lb_posted  — contest IDs already leaderboard-posted

ENV:
  MONGODB_URI   — full connection string from MongoDB Atlas
                  e.g. mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true
  MONGO_DB_NAME — database name (default: cfverify)
"""

import os
from datetime import datetime, timezone
import motor.motor_asyncio

MONGODB_URI   = os.getenv("MONGODB_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "cfverify")

# Module-level client — created once, reused everywhere
_client: motor.motor_asyncio.AsyncIOMotorClient | None = None
_db     = None


def _get_db():
    global _client, _db
    if _db is None:
        if not MONGODB_URI:
            raise RuntimeError("MONGODB_URI environment variable is not set!")
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
        _db     = _client[MONGO_DB_NAME]
    return _db


async def init_db():
    """Create indexes so lookups are fast."""
    db = _get_db()

    # users: discord_id is the primary key, cf_handle must be unique
    await db.users.create_index("discord_id",  unique=True)
    await db.users.create_index("cf_handle",   unique=True)
    await db.users.create_index("guild_id")

    # pending: one pending entry per discord user
    await db.pending.create_index("discord_id", unique=True)

    # lb_posted: one entry per contest
    await db.lb_posted.create_index("contest_id", unique=True)

    print("[db] MongoDB connected and indexes ensured ✅")


# ── Users ────────────────────────────────────────────────────────────────────

async def get_user(discord_id: int) -> dict | None:
    db  = _get_db()
    doc = await db.users.find_one({"discord_id": discord_id}, {"_id": 0})
    return doc


async def get_user_by_handle(cf_handle: str) -> dict | None:
    db  = _get_db()
    doc = await db.users.find_one(
        {"cf_handle": {"$regex": f"^{cf_handle}$", "$options": "i"}},
        {"_id": 0},
    )
    return doc


async def upsert_user(discord_id: int, guild_id: int, cf_handle: str,
                      rating: int, cf_rank: str):
    db = _get_db()
    await db.users.update_one(
        {"discord_id": discord_id},
        {"$set": {
            "discord_id":  discord_id,
            "guild_id":    guild_id,
            "cf_handle":   cf_handle,
            "rating":      rating,
            "cf_rank":     cf_rank,
        },
         "$setOnInsert": {
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


async def update_rating(discord_id: int, rating: int, cf_rank: str):
    db = _get_db()
    await db.users.update_one(
        {"discord_id": discord_id},
        {"$set": {"rating": rating, "cf_rank": cf_rank}},
    )


async def delete_user(discord_id: int):
    db = _get_db()
    await db.users.delete_one({"discord_id": discord_id})


async def all_users(guild_id: int) -> list[dict]:
    db      = _get_db()
    cursor  = db.users.find({"guild_id": guild_id}, {"_id": 0})
    return await cursor.to_list(length=None)


# ── Pending ──────────────────────────────────────────────────────────────────

async def set_pending(discord_id: int, cf_handle: str, token: str):
    db = _get_db()
    await db.pending.update_one(
        {"discord_id": discord_id},
        {"$set": {
            "discord_id": discord_id,
            "cf_handle":  cf_handle,
            "token":      token,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


async def get_pending(discord_id: int) -> dict | None:
    db  = _get_db()
    doc = await db.pending.find_one({"discord_id": discord_id}, {"_id": 0})
    return doc


async def delete_pending(discord_id: int):
    db = _get_db()
    await db.pending.delete_one({"discord_id": discord_id})


# ── Leaderboard dedup ────────────────────────────────────────────────────────

async def lb_already_posted(contest_id: int) -> bool:
    db  = _get_db()
    doc = await db.lb_posted.find_one({"contest_id": contest_id})
    return doc is not None


async def mark_lb_posted(contest_id: int):
    db = _get_db()
    await db.lb_posted.update_one(
        {"contest_id": contest_id},
        {"$setOnInsert": {
            "contest_id": contest_id,
            "posted_at":  datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
