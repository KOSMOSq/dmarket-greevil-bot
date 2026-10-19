# DMarket Greevil watcher

Every 10 minutes (GitHub Actions) checks DMarket for **Unusual Greevil** couriers and sends a Telegram message for each one whose Steam **Effect** is any "… Aura" (other effects like "Vas Air" are skipped).

DMarket doesn't show the effect, so for each new offer the bot follows its "View at Steam" link and reads the `Effect:` line from the seller's public Steam inventory. No DMarket account or API keys needed.

- Search / keyword: `SEARCH_TITLE` and `EFFECT_SUFFIX` in `main.py`.
- `seen.json` stores offers already checked (committed back by the workflow), so each offer is reported once. Offers whose Steam inventory couldn't be read are retried next run.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather), send it `/start`, get your chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. Repo **Settings → Secrets and variables → Actions**: add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. Run once manually from the **Actions** tab, or locally: `pip install -r requirements.txt`, put both vars in `.env`, `python main.py`.
