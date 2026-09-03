"""
Fetch all games for a Chess.com user and save as one growing PGN archive.
Run this after every session (or cron it) to keep your dataset updated.

Usage:
    python fetch_games.py <chess.com_username>
"""
import sys
import requests
from pathlib import Path

ARCHIVE_DIR = Path(__file__).parent / "data"
ARCHIVE_DIR.mkdir(exist_ok=True)


def fetch_all_games(username: str) -> str:
    headers = {"User-Agent": "chess-style-trainer (personal use)"}
    archives_url = f"https://api.chess.com/pub/player/{username}/games/archives"
    archives = requests.get(archives_url, headers=headers).json()["archives"]

    all_pgn = []
    for url in archives:
        month_data = requests.get(url, headers=headers).json()
        for game in month_data.get("games", []):
            pgn = game.get("pgn")
            if pgn:
                all_pgn.append(pgn)
    return "\n\n".join(all_pgn)


def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_games.py <chess.com_username>")
        sys.exit(1)

    username = sys.argv[1]
    print(f"Fetching games for {username}...")
    pgn_text = fetch_all_games(username)

    out_file = ARCHIVE_DIR / f"{username}_games.pgn"
    out_file.write_text(pgn_text, encoding="utf-8")
    n_games = pgn_text.count("[Event ")
    print(f"Saved {n_games} games to {out_file}")


if __name__ == "__main__":
    main()
