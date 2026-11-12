import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

ACCESS_TOKEN = "OC|752908224809889|"
APP_ID = 7190422614401072
DOCID = 6771539532935162

URL = "https://graph.oculus.com/graphql"
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "600"))  # default: 10 minutes
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# On Railway, attach a Volume so state survives redeploys. Railway sets
# RAILWAY_VOLUME_MOUNT_PATH automatically when a volume is attached.
STATE_FILE = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")) / "state.json"

sys.stdout.reconfigure(line_buffering=True)  # show logs immediately on Railway


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def payload() -> dict:
    return {
        "access_token": ACCESS_TOKEN,
        "variables": json.dumps({"applicationID": str(APP_ID)}),
        "doc_id": str(DOCID),
    }


async def fetch_metadata(session: aiohttp.ClientSession) -> Optional[dict]:
    try:
        async with session.post(URL, data=payload()) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:500]
                print(f"[{now()}] HTTP {resp.status} from Meta: {body}")
                return None
            data = await resp.json(content_type=None)
            return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[{now()}] Request failed: {type(e).__name__}: {e}")
        return None


def live_version(meta: dict) -> Optional[str]:
    nodes = (meta.get("data") or {}).get("node", {}).get("liveChannel", {}).get("nodes", [])
    if not nodes:
        return None
    return (nodes[0].get("latest_supported_binary") or {}).get("version")


def dev_version(meta: dict) -> Optional[str]:
    nodes = (meta.get("data") or {}).get("node", {}).get("primary_binaries", {}).get("nodes", [])
    if not nodes:
        return None
    return nodes[0].get("version")


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
    meta = await fetch_metadata(session)
    if meta is None:
        return

    current = {"live": live_version(meta), "dev": dev_version(meta)}
    state = load_state()

    # First run: record a baseline.
    if not state:
        save_state(current)
        await notify(
            session,
            f"Now tracking app {APP_ID}. Current versions: live={current['live']} dev={current['dev']}",
        )
        return

    for channel, new in current.items():
        old = state.get(channel)
        if new and new != old:
            await notify(session, f"App {APP_ID} {channel} version changed: {old} -> {new}")
            state[channel] = new

    save_state(state)


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        print(f"[{now()}] Tracking app {APP_ID}, checking every {POLL_SECONDS}s")
        while True:
            await check_once(session)
            await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
