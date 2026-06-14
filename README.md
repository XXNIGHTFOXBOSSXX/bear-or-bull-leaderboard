# BEAR OR BULL Poker Leaderboard

A Streamlit dashboard for the BEAR OR BULL 2026 poker leaderboard.

## Run locally

```powershell
cd "C:\Users\night\Desktop\BEAR OR BULL 2026\2026 games\2026 leaderboard\leaderboard dashboard"
python -m streamlit run bear_or_bull_leaderboard_dashboard.py
```

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository.
2. In Streamlit Community Cloud, choose **Deploy a public app from GitHub**.
3. Select the repository.
4. Set the main file path to:

```text
bear_or_bull_leaderboard_dashboard.py
```

The deployed app reads player list files from:

```text
DATA FOR EACH GAME/TOTAL PLAYERS
```

To update the online leaderboard later, add new weekly `.txt` files to that folder and push the changes to GitHub.
