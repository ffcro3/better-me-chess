"""
Ask your trained model what it would play in a given position.
Usage:
    python predict.py <username> "<fen>"
"""
import sys
import chess
import torch
from pathlib import Path
from train_style import PolicyNet, encode_board, decode_move_index

MODEL_PATH = Path(__file__).parent / "models"


def main():
    username, fen = sys.argv[1], sys.argv[2]
    model = PolicyNet()
    model.load_state_dict(torch.load(MODEL_PATH / f"{username}_policy.pt", map_location="cpu"))
    model.eval()

    board = chess.Board(fen)
    x = encode_board(fen).unsqueeze(0)
    with torch.no_grad():
        logits = model(x).squeeze(0)

    legal_idx = [(m.from_square * 64 + m.to_square, m) for m in board.legal_moves]
    scored = sorted(legal_idx, key=lambda p: -logits[p[0]].item())

    print("Top predicted moves (legal only):")
    for idx, move in scored[:5]:
        print(f"  {board.san(move):8s}  score={logits[idx].item():.3f}")


if __name__ == "__main__":
    main()
