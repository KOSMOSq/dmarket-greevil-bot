# DMarket item watcher

Every few minutes (GitHub Actions, triggered by an external cron) checks DMarket and sends a Telegram message for each new offer of:

- **Unusual Greevil** whose Steam **Effect** is any "… Aura" (other effects like "Vas Air" are skipped);
- **Bow of the Lone Traveler** (any quality) with any **Ethereal Gem** socketed.

DMarket doesn't show effects or gems, so for each new offer the bot follows its "View at Steam" link and reads the item description from the seller's public Steam inventory. Sellers' custom descriptions can't fake a match: Steam shows them in quotes and escapes HTML. No DMarket account or API keys needed.

- Searches live in `WATCHES` in `main.py`; each has a DMarket search title, a title/quality filter and a matcher over the Steam description.
- `seen.json` stores offers already checked (committed back by the workflow), so each offer is reported once. Offers whose Steam inventory couldn't be read are retried next run.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather), send it `/start`, get your chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. Repo **Settings → Secrets and variables → Actions**: add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. Run once manually from the **Actions** tab, or locally: `pip install -r requirements.txt`, put both vars in `.env`, `python main.py`.
