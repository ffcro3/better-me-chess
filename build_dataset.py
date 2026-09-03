"""
Convert a PGN archive into (FEN position -> your move) training pairs.
Only keeps moves YOU made (filters by your username as White or Black).

Usage:
    python build_dataset.py <username> <pgn_file>
Outputs:
    data/<username>_positions.jsonl  (one {"fen":..., "move":...} per line)
"""
import sys
import json
import chess.pgn
from pathlib import Path


def extract_my_moves(pgn_path: str, username: str):
    username = username.lower()
    samples = []
    with open(pgn_path, encoding="utf-8") as f:
        while True:
            game = chess.pgn.read_game(f)
            if game is None:
                break

            white = game.headers.get("White", "").lower()
            black = game.headers.get("Black", "").lower()
            if username not in (white, black):
                continue
            my_color_white = username == white

            board = game.board()
            for move in game.mainline_moves():
                is_my_move = (board.turn == chess.WHITE) == my_color_white
                if is_my_move:
                    samples.append({"fen": board.fen(), "move": move.uci()})
                board.push(move)
    return samples


def main():
    if len(sys.argv) < 3:
        print("Usage: python build_dataset.py <username> <pgn_file>")
        sys.exit(1)

    username, pgn_file = sys.argv[1], sys.argv[2]
    samples = extract_my_moves(pgn_file, username)

    out_file = Path(pgn_file).with_name(f"{username}_positions.jsonl")
    with open(out_file, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")

    print(f"Extracted {len(samples)} of your moves -> {out_file}")


if __name__ == "__main__":
    main()
