"""
Train a policy network to imitate your move choices (behavior cloning).
Primary training now happens in colab_train_style.ipynb (T4 GPU affords the
bumped capacity below) — this script stays in sync architecture-wise so a
checkpoint downloaded from Colab loads here for predict.py, and so this still
works standalone for quick local smoke tests on a small dataset.

Usage:
    python train_style.py <username>_positions.jsonl [--epochs 20]
"""
import sys
import json
import argparse
import chess
import torch
import torch.nn as nn
from pathlib import Path

MODEL_PATH = Path(__file__).parent / "models"
MODEL_PATH.mkdir(exist_ok=True)

# --- board encoding: 12 piece planes x 8x8 ---
PIECE_TO_PLANE = {
    (chess.PAWN, True): 0, (chess.KNIGHT, True): 1, (chess.BISHOP, True): 2,
    (chess.ROOK, True): 3, (chess.QUEEN, True): 4, (chess.KING, True): 5,
    (chess.PAWN, False): 6, (chess.KNIGHT, False): 7, (chess.BISHOP, False): 8,
    (chess.ROOK, False): 9, (chess.QUEEN, False): 10, (chess.KING, False): 11,
}


def encode_board(fen: str) -> torch.Tensor:
    board = chess.Board(fen)
    x = torch.zeros(12, 8, 8)
    for sq, piece in board.piece_map().items():
        plane = PIECE_TO_PLANE[(piece.piece_type, piece.color)]
        r, c = divmod(sq, 8)
        x[plane, r, c] = 1.0
    return x


# --- move encoding: from_square(64) x to_square(64) = 4096 classes (ignores underpromotion choice) ---
def encode_move(uci: str) -> int:
    move = chess.Move.from_uci(uci)
    return move.from_square * 64 + move.to_square


def decode_move_index(idx: int) -> chess.Move:
    return chess.Move(idx // 64, idx % 64)


class PolicyNet(nn.Module):
    """Mirrors the architecture in colab_train_style.ipynb — keep both in sync
    so checkpoints trained on either laptop or Colab load interchangeably."""
    def __init__(self):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(12, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(),
            nn.Conv2d(128, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(),
            nn.Conv2d(256, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(),
            nn.Conv2d(256, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(),
            nn.Conv2d(256, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(),
        )
        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 8 * 8, 1024), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(1024, 4096),
        )

    def forward(self, x):
        return self.head(self.conv(x))


def load_dataset(jsonl_path: str):
    xs, ys = [], []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            xs.append(encode_board(row["fen"]))
            ys.append(encode_move(row["move"]))
    return torch.stack(xs), torch.tensor(ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--lr", type=float, default=1e-3)
    args = ap.parse_args()

    username = Path(args.dataset).stem.replace("_positions", "")
    ckpt_file = MODEL_PATH / f"{username}_policy.pt"

    X, y = load_dataset(args.dataset)
    print(f"Loaded {len(X)} of your moves.")

    model = PolicyNet()
    if ckpt_file.exists():
        model.load_state_dict(torch.load(ckpt_file, map_location="cpu"))
        print(f"Resuming from existing checkpoint: {ckpt_file}")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.CrossEntropyLoss()

    model.train()
    for epoch in range(args.epochs):
        opt.zero_grad()
        logits = model(X)
        loss = loss_fn(logits, y)
        loss.backward()
        opt.step()
        acc = (logits.argmax(1) == y).float().mean().item()
        print(f"epoch {epoch+1}/{args.epochs} loss={loss.item():.4f} acc={acc:.3f}")

    torch.save(model.state_dict(), ckpt_file)
    print(f"Saved -> {ckpt_file}")


if __name__ == "__main__":
    main()
