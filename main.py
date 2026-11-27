"""
Watches DMarket for Dota 2 items with a specific Steam attribute and sends
each new match to Telegram:
  - Unusual Greevil couriers whose "Effect" is any "... Aura";
  - Bow of the Lone Traveler (any quality) with any Ethereal Gem socketed.

DMarket's listing API doesn't expose effects or gems, so for every new offer
we follow its "View at Steam" link and read the item's description from the
seller's public Steam inventory. No DMarket account or API keys are needed.
"""
import html
import json
import logging
import os
import re
import time
from pathlib import Path

from curl_cffi import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DOTA2_GAME_ID = "9a92"
SEEN_FILE = Path(__file__).with_name("seen.json")

MARKET_URL = "https://api.dmarket.com/exchange/v1/market/items/v2"
OFFER_URL = "https://api.dmarket.com/exchange/v1/offers/v2/{offer_id}"
STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/570/2"
VIEW_AT_STEAM_RE = re.compile(r"profiles/(\d+)/inventory/#570_2_(\d+)")
# A socketed gem in Steam's description HTML: <span>Gem name</span><br><span>Gem type</span>
ETHEREAL_GEM_RE = re.compile(r">([^<>]+)</span><br><span[^>]*>Ethereal Gem</span>")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# Steam answers 429 to plain Python HTTP clients, so mimic a real Chrome.
session = requests.Session(impersonate="chrome")


def fetch_offers(search_title: str) -> list[dict]:
    """Returns every DMarket offer matching search_title (all pages)."""
    offers, page_token = [], ""
    while True:
        response = session.get(MARKET_URL, params={
            "title": search_title, "gameId": DOTA2_GAME_ID, "side": "market",
            "orderBy": "updated", "orderDir": "desc", "treeFilters": "",
            "pageSize": 100, "currency": "USD", "platform": "browser",
            "pageToken": page_token,
        }, timeout=30)
        response.raise_for_status()
        data = response.json()
        page = data.get("offers") or []
        offers.extend(page)
        page_token = data.get("pageToken")
        if not page or not page_token or len(page) < 100:
            return offers


def get_view_at_steam(offer_id: str) -> str | None:
    response = session.get(OFFER_URL.format(offer_id=offer_id),
                           params={"gameId": DOTA2_GAME_ID}, timeout=30)
    response.raise_for_status()
    items = response.json().get("offers") or []
    return items[0].get("viewAtSteam") if items else None


_inventories: dict[str, dict] = {}


def get_steam_description(view_at_steam: str) -> list[str] | None:
    """
    Returns the description lines of the item behind a "View at Steam" link,
    or None when Steam can't be read (private inventory, rate limit, item gone)
    so the offer is retried on the next run.
    """
    match = VIEW_AT_STEAM_RE.search(view_at_steam or "")
    if not match:
        return None
    steam_id, asset_id = match.groups()

    if steam_id not in _inventories:
        time.sleep(5)  # be gentle with Steam's rate limit
        response = session.get(STEAM_INVENTORY_URL.format(steam_id=steam_id),
                               params={"l": "english", "count": 2000}, timeout=30)
        if response.status_code != 200:
            # 429 = Steam rate limit, 403 = private inventory; retried next run
            logging.warning(f"Steam inventory {steam_id}: HTTP {response.status_code}")
            return None
        _inventories[steam_id] = response.json() or {}
    inventory = _inventories[steam_id]

    asset = next((a for a in inventory.get("assets", []) if a["assetid"] == asset_id), None)
    if asset is None:
        return None
    for description in inventory.get("descriptions", []):
        if (description["classid"], description["instanceid"]) == (asset["classid"], asset["instanceid"]):
            return [line.get("value", "").strip() for line in description.get("descriptions", [])]
    return None


# Each matcher reads an item's Steam description lines and returns what to
# report ("" when the item doesn't qualify). Sellers' custom descriptions
# can't fake either: Steam shows them in quotes (e.g. "''Aura de Sanct''")
# and escapes any HTML in them.

def greevil_aura_effect(lines: list[str]) -> str:
    """The "Effect: ..." attribute, only when it is some "... Aura"."""
    for value in lines:
        if value.startswith("Effect:"):
            effect = value.removeprefix("Effect:").strip()
            return effect if effect.lower().split()[-1:] == ["aura"] else ""
    return ""


def ethereal_gems(lines: list[str]) -> str:
    """Names of every Ethereal Gem socketed in the item."""
    return ", ".join(html.unescape(name).strip()
                     for value in lines for name in ETHEREAL_GEM_RE.findall(value))


WATCHES = [
    {
        "name": "Unusual Greevil", "search": "Unusual Greevil",
        "emoji": "🐲", "label": "Effect", "match": greevil_aura_effect,
        "accept": lambda o: "greevil" in o["title"].lower()
                            and (o.get("dota2") or {}).get("quality") == "unusual",
    },
    {
        "name": "Bow of the Lone Traveler", "search": "Bow of the Lone Traveler",
        "emoji": "🏹", "label": "Ethereal Gem", "match": ethereal_gems,
        "accept": lambda o: "bow of the lone traveler" in o["title"].lower(),
    },
]


def send_telegram_message(text: str) -> bool:
    response = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                             data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                             timeout=30)
    if not response.ok:
        logging.error(f"Telegram error: {response.text}")
    return response.ok


def build_message(watch: dict, offer: dict, found: str, view_at_steam: str) -> str:
    price = offer.get("priceCents", 0) / 100
    url = f"https://dmarket.com/ingame-items/item-list/dota2-skins?userOfferId={offer['offerId']}"
    return (
        f"{watch['emoji']} DMarket\n\n"
        f"🔍 <b>{html.escape(offer['title'])}</b>\n\n"
        f"✨ {watch['label']}: <b>{html.escape(found)}</b>\n"
        f"💰 ${price:.2f}\n\n"
        f"🔗 {url}\n"
        f"🎮 {view_at_steam}"
    )


def main() -> None:
    seen: dict[str, str] = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else {}
    listed: set[str] = set()
    all_fetched = True

    for watch in WATCHES:
        try:
            offers = [o for o in fetch_offers(watch["search"]) if watch["accept"](o)]
        except requests.RequestsError as error:
            logging.warning(f"{watch['name']}: DMarket search failed: {error}")
            all_fetched = False
            continue
        listed |= {o["offerId"] for o in offers}
        logging.info(f"{watch['name']}: {len(offers)} offers on DMarket, "
                     f"{sum(o['offerId'] in seen for o in offers)} already checked")

        for offer in offers:
            offer_id = offer["offerId"]
            if offer_id in seen:
                continue
            try:
                view_at_steam = get_view_at_steam(offer_id)
                lines = get_steam_description(view_at_steam)
            except requests.RequestsError as error:
                logging.warning(f"{offer_id}: {error}")
                continue
            if lines is None:
                continue  # couldn't read Steam this time — retry on the next run

            found = watch["match"](lines)
            logging.info(f"{offer['title']} ${offer.get('priceCents', 0) / 100:.2f} — "
                         f"{watch['label']}: {found or 'no match'}")
            if found and not send_telegram_message(build_message(watch, offer, found, view_at_steam)):
                continue  # not delivered — try again on the next run
            seen[offer_id] = found

    # Forget offers that are no longer listed, so the file doesn't grow forever
    # (skipped when a search failed, or its offers would be re-sent next run).
    if all_fetched:
        seen = {k: v for k, v in seen.items() if k in listed}
    SEEN_FILE.write_text(json.dumps(seen, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
