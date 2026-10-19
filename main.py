"""
Watches DMarket for Unusual Greevil couriers whose Steam "Effect" is any
"... Aura" and sends each new match to Telegram.

DMarket's listing API doesn't expose the effect, so for every new offer we
follow its "View at Steam" link and read the Effect line from the seller's
public Steam inventory. No DMarket account or API keys are needed.
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

SEARCH_TITLE = "Unusual Greevil"
EFFECT_SUFFIX = "aura"  # Effect must end with this word, e.g. "Ethereal Flame Aura"
DOTA2_GAME_ID = "9a92"
SEEN_FILE = Path(__file__).with_name("seen.json")

MARKET_URL = "https://api.dmarket.com/exchange/v1/market/items/v2"
OFFER_URL = "https://api.dmarket.com/exchange/v1/offers/v2/{offer_id}"
STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steam_id}/570/2"
VIEW_AT_STEAM_RE = re.compile(r"profiles/(\d+)/inventory/#570_2_(\d+)")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# Steam answers 429 to plain Python HTTP clients, so mimic a real Chrome.
session = requests.Session(impersonate="chrome")


def fetch_offers() -> list[dict]:
    """Returns every DMarket offer matching SEARCH_TITLE (all pages)."""
    offers, page_token = [], ""
    while True:
        response = session.get(MARKET_URL, params={
            "title": SEARCH_TITLE, "gameId": DOTA2_GAME_ID, "side": "market",
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


def get_steam_effect(view_at_steam: str) -> str | None:
    """
    Reads the "Effect: ..." line of the item behind a "View at Steam" link.
    Returns "" when the item has no effect, None when Steam can't be read
    (private inventory, rate limit, item gone) so the offer is retried later.
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
            for line in description.get("descriptions", []):
                # Only Steam's own "Effect:" attribute counts. Sellers' custom
                # descriptions (e.g. "''Aura de Sanct''") are shown in quotes
                # and never start with "Effect:", so they're ignored.
                value = line.get("value", "").strip()
                if value.startswith("Effect:"):
                    return value.removeprefix("Effect:").strip()
            return ""
    return None


def send_telegram_message(text: str) -> bool:
    response = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                             data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                             timeout=30)
    if not response.ok:
        logging.error(f"Telegram error: {response.text}")
    return response.ok


def build_message(offer: dict, effect: str, view_at_steam: str) -> str:
    price = offer.get("priceCents", 0) / 100
    url = f"https://dmarket.com/ingame-items/item-list/dota2-skins?userOfferId={offer['offerId']}"
    return (
        f"🐲 DMarket\n\n"
        f"🔍 <b>{html.escape(offer['title'])}</b>\n\n"
        f"✨ Effect: <b>{html.escape(effect)}</b>\n"
        f"💰 ${price:.2f}\n\n"
        f"🔗 {url}\n"
        f"🎮 {view_at_steam}"
    )


def main() -> None:
    seen: dict[str, str] = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else {}

    offers = [o for o in fetch_offers()
              if "greevil" in o.get("title", "").lower()
              and (o.get("dota2") or {}).get("quality") == "unusual"]
    logging.info(f"{len(offers)} Unusual Greevil offers on DMarket, {len(seen)} already checked")

    for offer in offers:
        offer_id = offer["offerId"]
        if offer_id in seen:
            continue
        try:
            view_at_steam = get_view_at_steam(offer_id)
            effect = get_steam_effect(view_at_steam)
        except requests.RequestsError as error:
            logging.warning(f"{offer_id}: {error}")
            continue
        if effect is None:
            continue  # couldn't read Steam this time — retry on the next run

        logging.info(f"{offer['title']} — effect: {effect or 'none'}")
        if effect.lower().split()[-1:] == [EFFECT_SUFFIX]:
            if not send_telegram_message(build_message(offer, effect, view_at_steam)):
                continue  # not delivered — try again on the next run
        seen[offer_id] = effect

    # Forget offers that are no longer listed, so the file doesn't grow forever.
    listed = {o["offerId"] for o in offers}
    seen = {k: v for k, v in seen.items() if k in listed}
    SEEN_FILE.write_text(json.dumps(seen, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
