# "Better Me" Chess AI — Build Plan

## Goal
An engine that plays like YOU (style-biased), with easy/medium/hard = how forgiving
it is of your real weaknesses — not a generic bot. Plus a review mode that shows
where your moves diverged from the "better you" and by how much.

## Architecture (2 components, not one)
1. **Style model** — small CNN, learns your move preferences from your games (behavior cloning).
2. **Strength engine** — Stockfish, generates candidate moves + evals. Never trained, just installed.
Final move = blend of the two, weighted by difficulty setting. No self-play RL — wrong tool for laptop/Colab scale, and unnecessary since Stockfish already supplies "stronger."

## Phase 1 — Data
- [x] Fetch all Chess.com games (`fetch_games.py` — already built, reuse) — 63 games (own account)
- [x] Extract (position, your move) pairs (`build_dataset.py` — already built, reuse) — 2541 positions
- [ ] Re-run both anytime you play more (this IS the "current version of yourself" update loop)

## Phase 2 — Style model (train on Colab T4)
- [x] Port `train_style.py` CNN into a Colab notebook (`colab_train_style.ipynb`)
- [x] Bump capacity (more filters/layers) — T4 affords it, laptop didn't. `train_style.py` kept in sync so checkpoints are interchangeable.
- [x] Train, download checkpoint (`.pt` file) back to local machine — `models/<username>_policy.pt`, verified loading + sane predictions via `predict.py`
- [ ] Re-train periodically as dataset grows (same checkpoint, resumed — not from scratch)

## Phase 3 — Blend engine (local)
- [x] Install Stockfish locally (`engine/stockfish.exe`, v18, downloaded from official releases — no admin needed), wrap via `python-chess` UCI interface
- [x] For a given position: get Stockfish's top N candidate moves + centipawn evals (`blend_engine.py`, MultiPV)
- [x] Score those same N moves with your style model
- [x] Combine into one weighted choice — weight = difficulty setting
- [x] Define easy/medium/hard as blend ratios — easy=90% style, medium=60%, hard=30% style (rest = eval), with a plausibility floor (drops candidates the style model rates <5% of its top pick, even on hard). Verified on starting position: easy/medium→d4 (your style), hard→e4 (higher eval).

## Phase 4 — Play mode
- [x] Interface to play live against the blend engine at chosen difficulty — local web board (`app.py` Flask backend + `static/` chessboard.js frontend)
- [x] Terminal (fast to ship) or simple local web board (nicer, more work) — pick one to start → **web board**. Tested end-to-end via API: new game as either color, difficulty switch, bot replies, illegal moves rejected. Promotions auto-queen (simplification, not a picker yet).
- [x] chess.com-style UI pass: move list with click/keyboard navigation (⏮◀▶⏭ + arrow keys), legal-move dots + last-move highlight, New Game wizard modal (color + difficulty), Resign, game-over modal (Rematch / Review), dark themed layout with player bars.
- [x] Game history — every finished game (checkmate/draw/resign) auto-saved as PGN to `data/history.db` (SQLite); History tab lists past games with a win/loss/draw pill.

## Phase 5 — Review/learning mode
- [x] After a game (yours or against the bot), run Stockfish eval on every one of your moves — `GET /api/history/<id>/review`
- [x] Flag centipawn loss per move, highlight worst blunders — top 5 by cp loss (≥100cp threshold), shown in the Review view with a clickable board (jumps to the position before each blunder)
- [x] Show what "better you" (the blend engine) would've played instead at those spots — `best_move_san` per flagged move (from Stockfish, not the style model — see note below)
- [ ] Optional: group blunders by pattern (opening/middlegame/endgame, time pressure, piece hung, etc.) — not done, low priority
- [x] **Bonus (user's idea, not in original plan): in-game "Coach" hint button** — `POST /api/hint`, shows what the blend engine (style + eval, at current difficulty) would play in the live position, with square highlighting. Different from post-game review: this uses the *blend* (your style), review's "better move" uses pure Stockfish (objectively best), by design — review is meant to show the objectively correct move, not just "more you."

## Known simplifications (flagged, not fixed)
- Promotions always auto-queen (no underpromotion picker) — client hardcodes `promotion: "q"`.
- A game is only saved to history when it actually finishes (checkmate/draw/resign) — starting a new game over an unfinished one silently discards it.
- Single global in-memory game per server process — fine for one local player, would need per-session state for multiple concurrent games.
- Review's Stockfish passes use a 0.15s time limit per position (2 analyses per player move) — fast enough for casual games, but eval quality is bounded by that budget on longer games.

## Open decisions to revisit
- ~~Terminal vs web interface for play mode~~ → **decided: local web board** (chessboard.js + local backend)
- Exact difficulty blend ratios (defaults above, adjustable)
- How often you'll re-fetch/re-train (manual trigger vs scheduled) — manual for now

## Reuse from earlier work
Existing files (`fetch_games.py`, `build_dataset.py`, `train_style.py`, `predict.py`)
already cover Phase 1 and a first draft of Phase 2 — starting point rather than
rebuilding from zero.