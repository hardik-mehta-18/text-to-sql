"""
User entity cache — persistent JSON store (production-ready).
Fixed column mapping for Surname (Service_User) vs LastName (UserDetails).
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_TTL_MINUTES = 30
CACHE_DIR = Path("user_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PersonRecord:
    id: str
    first_name: str
    last_name: str
    preferred_name: str
    table: str


@dataclass
class UserCache:
    records: List[PersonRecord] = field(default_factory=list)
    loaded_at: datetime = field(default_factory=datetime.utcnow)

    def is_expired(self) -> bool:
        return datetime.utcnow() - self.loaded_at > timedelta(minutes=CACHE_TTL_MINUTES)


# ===================== JSON Persistence =====================
def _get_cache_path(session_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return CACHE_DIR / f"{safe_id}.json"


def _load_from_json(session_id: str) -> Optional[UserCache]:
    path = _get_cache_path(session_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = [PersonRecord(**r) for r in data.get("records", [])]
        cache = UserCache(records=records, loaded_at=datetime.fromisoformat(data["loaded_at"]))
        if not cache.is_expired():
            logger.info(f"[user_cache] ✅ JSON cache hit for session {session_id} ({len(records)} records)")
            return cache
        return None
    except Exception as e:
        logger.warning(f"[user_cache] JSON load failed: {e}")
        return None


def _save_to_json(session_id: str, cache: UserCache) -> None:
    path = _get_cache_path(session_id)
    try:
        data = {
            "records": [vars(rec) for rec in cache.records],
            "loaded_at": cache.loaded_at.isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"[user_cache] ✅ Saved JSON cache for {session_id} ({len(cache.records)} records)")
    except Exception as e:
        logger.error(f"[user_cache] JSON save failed: {e}")


# ===================== Name Normalization =====================
def _norm(s: str) -> str:
    return (s or "").lower().strip().replace("'", "")


# ===================== Public API =====================
def find_person(name: str, session_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (resolved_table, last_name_column)
    Both can be None → let LLM decide everything
    """
    cache = _load_from_json(session_id)
    if not cache:
        return None, None

    parts = name.strip().split()
    is_full_name = len(parts) >= 2
    first = _norm(parts[0])
    last = _norm(parts[-1]) if is_full_name else ""

    matched = []
    for rec in cache.records:
        r_first = _norm(rec.first_name)
        r_last = _norm(rec.last_name)
        r_pref = _norm(rec.preferred_name)

        if is_full_name:
            if first and (first in r_first or first in r_pref) and last and last in r_last:
                matched.append(rec)
        else:
            word = first
            if word and (word in r_first or word in r_last or word in r_pref):
                matched.append(rec)

    if not matched:
        return None, None

    tables = {r.table for r in matched}
    if len(tables) == 1:
        table_name = tables.pop()
        last_name_col = "Surname" if table_name == "BNR_Service_User" else "LastName"
        logger.info(f"[user_cache] ✅ Cache hit: '{name}' → {table_name} (LastNameCol={last_name_col})")
        return table_name, last_name_col

    logger.info(f"[user_cache] Ambiguous match for '{name}' → LLM decides")
    return None, None


def invalidate(session_id: str) -> None:
    _get_cache_path(session_id).unlink(missing_ok=True)


# ===================== FIXED CACHE LOADER SQL =====================
_SERVICE_USER_SQL = """
SELECT DISTINCT
    Id,
    FirstName,
    COALESCE(Surname, '') AS LastName,          -- Important: alias as LastName for consistency
    COALESCE(PreferredName, '') AS PreferredName
FROM BNR_Service_User
WHERE IsDeleted = 0
  AND SiteId IN ({site_filter})
"""

_STAFF_SQL = """
SELECT DISTINCT
    BNR_UserDetails.Id,
    BNR_UserDetails.FirstName,
    COALESCE(BNR_UserDetails.LastName, '') AS LastName,
    '' AS PreferredName
FROM BNR_UserDetails
INNER JOIN BNR_User_Sites 
    ON BNR_UserDetails.Id = BNR_User_Sites.UserDetailId
WHERE BNR_UserDetails.IsDeleted = 0
  AND BNR_User_Sites.SiteId IN ({site_filter})
"""


async def ensure_cache(
    session_id: str,
    executor,
    engine,
    site_id,
    force_reload: bool = False,
) -> UserCache:
    cache = _load_from_json(session_id)
    if cache and not force_reload:
        return cache

    logger.info(f"[user_cache] Building fresh cache for session {session_id}")

    # Normalize site_id
    if isinstance(site_id, (list, tuple)):
        site_list = [str(s).strip() for s in site_id if s]
    elif isinstance(site_id, str):
        site_list = [s.strip() for s in site_id.split(",") if s.strip()]
    else:
        site_list = [str(site_id).strip()] if site_id is not None else []

    if not site_list:
        logger.warning(f"[user_cache] No site_id provided for session {session_id}")
        empty_cache = UserCache(records=[])
        _save_to_json(session_id, empty_cache)
        return empty_cache

    site_filter = ", ".join(f"'{s}'" for s in site_list)

    records: List[PersonRecord] = []

    for sql_tmpl, table_name in [
        (_SERVICE_USER_SQL, "BNR_Service_User"),
        (_STAFF_SQL, "BNR_UserDetails"),
    ]:
        sql = sql_tmpl.format(site_filter=site_filter)
        try:
            result = await executor.execute(engine, sql)
            if result and result.rows:
                loaded = 0
                for row in result.rows:
                    records.append(PersonRecord(
                        id=str(row.get("Id") or ""),
                        first_name=str(row.get("FirstName") or ""),
                        last_name=str(row.get("LastName") or ""),      # Now correctly populated for both tables
                        preferred_name=str(row.get("PreferredName") or ""),
                        table=table_name,
                    ))
                    loaded += 1
                logger.info(f"[user_cache] Loaded {loaded} records from {table_name}")
        except Exception as exc:
            logger.error(f"[user_cache] Failed to load {table_name}: {exc}", exc_info=True)

    cache = UserCache(records=records)
    _save_to_json(session_id, cache)

    logger.info(f"[user_cache] Cache ready — {len(records)} total persons (Service User + Staff)")
    return cache