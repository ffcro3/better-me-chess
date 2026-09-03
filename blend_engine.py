"""
Phase 3 — Blend engine: combines Stockfish's strength with your style model.
Given a position, gets Stockfish's top-N candidate moves, scores those same
moves with your trained PolicyNet, and picks one via a weighted blend.
Difficulty = how much weight goes to "what you'd actually play" vs "the
objectively best move" — always restricted to moves your style model finds
at least somewhat plausible, even on "hard".

Usage:
    python blend_engine.py <username> "<fen>" [easy|medium|hard]
"""
import os
import sys
from pathlib import Path

import chess
import chess.engine
import torch
import torch.nn.functional as F

from train_style import PolicyNet, encode_board


def _load_dotenv(path: Path):
    """Tiny KEY=VALUE .env loader — no extra dependency needed for this."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(Path(__file__).parent / ".env")

ENGINE_PATH = Path(os.environ.get("STOCKFISH_PATH", str(Path(__file__).parent / "engine" / "stockfish.exe")))
MODEL_PATH = Path(__file__).parent / "models"

# weight on the style model's preference; remainder goes to Stockfish's eval
DIFFICULTY_BLEND = {
    "easy": 0.9,
    "medium": 0.6,
    "hard": 0.3,
}

# drop candidates the style model rates below this fraction of its
# most-preferred candidate — never play a move you'd basically never play
PLAUSIBILITY_FLOOR = 0.05


def _style_probs(model: PolicyNet, board: chess.Board) -> dict:
    x = encode_board(board.fen()).unsqueeze(0)
    with torch.no_grad():
        logits = model(x).squeeze(0)
    legal = list(board.legal_moves)
    idx = torch.tensor([m.from_square * 64 + m.to_square for m in legal])
    probs = F.softmax(logits[idx], dim=0)
    return {m: probs[i].item() for i, m in enumerate(legal)}


def load_style_model(username: str) -> PolicyNet:
    model = PolicyNet()
    model.load_state_dict(torch.load(MODEL_PATH / f"{username}_policy.pt", map_location="cpu"))
    model.eval()
    return model


def get_blended_move(
    fen: str,
    username: str,
    difficulty: str = "medium",
    n_candidates: int = 8,
    think_time: float = 0.3,
    model: PolicyNet | None = None,
) -> tuple[chess.Move, dict]:
    """Returns (chosen_move, debug_info) for the given position."""
    if difficulty not in DIFFICULTY_BLEND:
        raise ValueError(f"difficulty must be one of {list(DIFFICULTY_BLEND)}")
    style_weight = DIFFICULTY_BLEND[difficulty]

    board = chess.Board(fen)
    if model is None:
        model = load_style_model(username)

    style_probs = _style_probs(model, board)

    n_candidates = max(1, min(n_candidates, board.legal_moves.count()))
    with chess.engine.SimpleEngine.popen_uci(str(ENGINE_PATH)) as engine:
        infos = engine.analyse(board, chess.engine.Limit(time=think_time), multipv=n_candidates)
    if isinstance(infos, dict):
        infos = [infos]

    candidates = []
    for info in infos:
        move = info["pv"][0]
        cp = info["score"].pov(board.turn).score(mate_score=100_000)
        candidates.append((move, cp))

    cps = [cp for _, cp in candidates]
    lo, hi = min(cps), max(cps)
    spread = hi - lo or 1

    cand_style = {m: style_probs.get(m, 0.0) for m, _ in candidates}
    max_style = max(cand_style.values()) or 1.0
    style_total = sum(cand_style.values()) or 1.0

    scored = []
    for move, cp in candidates:
        style_p = cand_style[move]
        if style_p < PLAUSIBILITY_FLOOR * max_style:
            continue
        eval_norm = (cp - lo) / spread
        style_norm = style_p / style_total
        blend = style_weight * style_norm + (1 - style_weight) * eval_norm
        scored.append((move, blend, cp, style_p))

    scored.sort(key=lambda t: -t[1])
    best_move = scored[0][0]

    debug = {
        "difficulty": difficulty,
        "style_weight": style_weight,
        "candidates": [
            {"move": board.san(m), "blend_score": round(b, 3), "cp": cp, "style_prob": round(p, 4)}
            for m, b, cp, p in scored
        ],
    }
    return best_move, debug


def main():
    if len(sys.argv) < 3:
        print('Usage: python blend_engine.py <username> "<fen>" [easy|medium|hard]')
        sys.exit(1)

    username, fen = sys.argv[1], sys.argv[2]
    difficulty = sys.argv[3] if len(sys.argv) > 3 else "medium"

    move, debug = get_blended_move(fen, username, difficulty)
    board = chess.Board(fen)
    print(f"Difficulty: {difficulty} (style weight={debug['style_weight']})")
    for c in debug["candidates"]:
        print(f"  {c['move']:8s} blend={c['blend_score']:.3f}  cp={c['cp']:+d}  style_prob={c['style_prob']:.3f}")
    print(f"\nChosen: {board.san(move)}")


if __name__ == "__main__":
    main()
