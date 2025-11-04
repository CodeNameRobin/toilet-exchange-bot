# Stock_Exchange_App

# The Toilet Exchange

The **Toilet Exchange** is a chaotic, tongue-in-cheek Discord stock-trading simulator where players buy, sell, and crash fake companies in a volatile market run by chaos and bad decisions.

Prices move automatically. Fortunes rise and fall. You might become the richest trader in the server… or lose everything in a glorious toilet-flush of economic disaster.

---

## 🧾 Features

-  **Dynamic Market** – Stock prices shift automatically based on random market behavior.  
-  **Risk System** – Each stock has a volatility rating: `Low`, `Moderate`, or `High`.  
-  **Trading System** – Buy and sell stocks using in-game cash. Build your portfolio or go broke trying.  
-  **Leaderboards** – See who’s ruling the market (and who’s in debt).  
-  **Player Trading** – Trade directly with other users.  
-  **Admin Tools** – Server admins can add/remove stocks, trigger market crashes, and tweak settings.  


---

## 💬 Commands

### Player Commands
| Command | Description |
|----------|-------------|
| `!register` | Create your account and get your starter cash |
| `!portfolio` | View your current holdings and total worth |
| `!buy <ticker> <qty>` | Buy shares of a stock |
| `!sell <ticker> <qty>` | Sell your shares |
| `!price <ticker>` | Check the current price of a stock |
| `!stocks` | List all active companies |
| `!trend <ticker>` | View recent performance as a line chart |
| `!leaderboard` | See who’s winning (and losing) |
| `!feedback` | Send feedback to the developer |

### Admin Commands
| Command | Description |
|----------|-------------|
| `!add_stock <ticker> <name> <price> <risk>` | Add a new stock |
| `!remove_stock <ticker>` | Remove a stock and cash out holders |
| `!set_price <ticker> <price>` | Manually set a stock’s price |
| `!set_risk <ticker> <low/moderate/high>` | Change a stock’s volatility |
| `!reset_stocks` | Wipe all stocks and reset the market |
| `!gift <user> <amount or ticker qty>` | Gift players money or stocks |
| `!market_crash` | Trigger a catastrophic market event |
| `!set_setting <key> <value>` | Change server settings |
| `!list_settings` | View current server settings |

---

## ⚙️ Setup

### Prerequisites
- Python 3.10 or higher  
- A Discord bot token  
- SQLite (included by default)  
- (Recommended) A virtual environment

### Installation
```bash
git clone https://github.com/CodeNameRobin/toilet-exchange-bot.git
cd toilet-exchange-bot
pip install -r requirements.txt
```

### Configuration
Create a .env file in the project root:
```bash
env

DISCORD_TOKEN=your-bot-token-here
FEEDBACK_GUILD_ID=your-feedback-guild-id
FEEDBACK_CHANNEL_ID=your-feedback-channel-id
```
(This file is ignored by Git — do not share it publicly.)

Run the bot
```bash
python bot.py
```

## 🧠 Development Notes

- Stock prices update automatically via a background task.

- Price movement scales with volatility — low-risk stocks move slightly, high-risk ones swing wildly.

- Prices can never fall below $0.01, and low-value stocks have a slightly higher chance of recovery.

## 🪙 Planned Features

- Add/Change Admins: Allow servers to add custom bot admins without requiring Discord server admin permissions.

- Low-Value Stock Recovery System: Give penny stocks a fairer chance to recover from long-term stagnation.

- Market News that shows an overview of market activity for the day/week/month.

- Server Customization: Allow each server to fine-tune update rates, risk multipliers, and more.

## 🧑‍💻 Contributing

- Pull requests and suggestions are always welcome!

- If you find bugs or balance issues, open an issue on GitHub or use the !feedback command in Discord.


## 🌐 Links

Website: https://toilet-exchange.neocities.org

.


