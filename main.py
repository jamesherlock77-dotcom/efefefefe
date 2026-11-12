import asyncio
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

GAME_NAME = os.environ.get("GAME_NAME", "Animal Company")
STORE_URL = os.environ.get(
    "STORE_URL",
    "https://www.meta.com/en-gb/experiences/animal-company/7190422614401072/",
)
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "600"))  # default: 10 minutes
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# On Railway, attach a Volume so state survives redeploys. Railway sets
# RAILWAY_VOLUME_MOUNT_PATH automatically when a volume is attached.
STATE_FILE = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")) / "state.json"

# The store page embeds the app's data as JSON, e.g.
#   ..."version":"1.99.5.4078","version_code":4078,...
VERSION_RE = re.compile(r'"version":"(\d+(?:\.\d+)+)","version_code":(\d+)')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

sys.stdout.reconfigure(line_buffering=True)  # show logs immediately on Railway


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def fetch_version(session: aiohttp.ClientSession) -> Optional[str]:
    try:
        async with session.get(STORE_URL, headers=HEADERS) as resp:
            status = resp.status
            html = await resp.text()
    except Exception as e:
        print(f"[{now()}] Request failed: {type(e).__name__}: {e}")
        return None

    if status != 200:
        print(f"[{now()}] HTTP {status} from store page: {html[:300]!r}")
        return None

    matches = VERSION_RE.findall(html)
    if not matches:
        print(f"[{now()}] No version found in page (HTTP {status}, {len(html)} chars)")
        return None

    # The app's own binary shows up several times; pick the most common pair
    # in case the page also embeds other apps' data.
    counts = Counter(matches)
    (version, _code), _n = counts.most_common(1)[0]
    if len(counts) > 1:
        print(f"[{now()}] Multiple version candidates, using {version}: {dict(counts)}")
    return version


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


async def notify(session: aiohttp.ClientSession, message: str) -> None:
    print(f"[{now()}] {message}")
    if not WEBHOOK_URL:
        return
    try:
        async with session.post(WEBHOOK_URL, json={"content": message}) as resp:
            resp.raise_for_status()
    except Exception as e:
        print(f"[{now()}] Webhook failed: {type(e).__name__}: {e}")


async def check_once(session: aiohttp.ClientSession) -> None:
    version = await fetch_version(session)
    if version is None:
        return

    state = load_state()
    old = state.get("live")

    # First run: record a baseline.
    if not old:
        save_state({"live": version})
        await notify(session, f"Now tracking {GAME_NAME}. Current version: {version}")
        return

    if version != old:
        await notify(
            session,
            f"{GAME_NAME} updated: {old} -> {version}\n{STORE_URL}",
        )
        save_state({"live": version})


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        print(f"[{now()}] Tracking {GAME_NAME}, checking every {POLL_SECONDS}s")
        while True:
            await check_once(session)
            await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
