"""
Phase 4 — Play mode: local web app against your blend engine.
Phase 5 — Review: post-game blunder analysis + in-game "coach" hints.

Usage:
    python app.py [username] [--port 5000]
Then open http://127.0.0.1:5000 in your browser.
"""
import argparse
import io
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.engine
import chess.pgn
from flask import Flask, Response, g, jsonify, request, send_from_directory

from blend_engine import DIFFICULTY_BLEND, ENGINE_PATH, get_blended_move, load_style_model

STATIC_DIR = Path(__file__).parent / "static"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "history.db"

# Local, informal rating — not synced with chess.com/FIDE. Bot Elo values are
# rough estimates of how each difficulty plays (the blend never outright
# blunders since it always picks among Stockfish's own top candidates, but
# lower difficulties weight the style model more heavily over raw eval, which
# does cost real strength) — a compass, not a certified number.
STARTING_RATING = 1200
BOT_ELO = {"easy": 1000, "medium": 1400, "hard": 1800}
ELO_K = 32

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")


def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(_exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


def _ensure_column(conn, table, column, coltype):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            player_color TEXT,
            difficulty TEXT,
            result TEXT,
            num_moves INTEGER,
            pgn TEXT,
            started_at TEXT,
            ended_at TEXT
        )"""
    )
    _ensure_column(conn, "games", "termination", "TEXT")
    _ensure_column(conn, "games", "rating_before", "INTEGER")
    _ensure_column(conn, "games", "rating_after", "INTEGER")
    _ensure_column(conn, "games", "bot_elo", "INTEGER")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ratings (
            username TEXT PRIMARY KEY,
            rating REAL,
            games_played INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            updated_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def _score_for_player(result, player_color):
    """player_color is 'white'/'black'. Returns 1/0.5/0 from the player's POV."""
    if result == "1/2-1/2":
        return 0.5
    if (result == "1-0" and player_color == "white") or (result == "0-1" and player_color == "black"):
        return 1.0
    return 0.0


def _update_elo(r_player, r_bot, score, k=ELO_K):
    expected = 1 / (1 + 10 ** ((r_bot - r_player) / 400))
    return r_player + k * (score - expected)


def _infer_termination(pgn_text, result):
    pgn_obj = chess.pgn.read_game(io.StringIO(pgn_text))
    board = pgn_obj.end().board()
    if result == "1/2-1/2":
        if board.is_stalemate():
            return "stalemate"
        if board.is_insufficient_material():
            return "insufficient material"
        if board.is_seventyfive_moves():
            return "75-move rule"
        if board.is_fivefold_repetition():
            return "fivefold repetition"
        return "draw"
    return "checkmate" if board.is_checkmate() else "resignation"


def _termination_reason():
    if game.resignation is not None:
        return "resignation"
    b = game.board
    if b.is_checkmate():
        return "checkmate"
    if b.is_stalemate():
        return "stalemate"
    if b.is_insufficient_material():
        return "insufficient material"
    if b.is_seventyfive_moves():
        return "75-move rule"
    if b.is_fivefold_repetition():
        return "fivefold repetition"
    return "draw"


def _termination_text(result, termination, white_name, black_name):
    if result == "1/2-1/2":
        return f"Game drawn by {termination}"
    winner = white_name if result == "1-0" else black_name
    return f"{winner} won by {termination}"


def _get_or_init_rating(conn, username):
    row = conn.execute("SELECT * FROM ratings WHERE username = ?", (username,)).fetchone()
    if row is None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO ratings (username, rating, games_played, wins, losses, draws, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (username, STARTING_RATING, 0, 0, 0, 0, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ratings WHERE username = ?", (username,)).fetchone()
    return dict(row)


def _backfill_missing_data(username):
    """One-time migration: old games predate the rating/termination columns.
    Replay them in order and fill in Elo progression + inferred termination."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if conn.execute("SELECT 1 FROM ratings WHERE username = ?", (username,)).fetchone() is not None:
        conn.close()
        return

    rating = STARTING_RATING
    wins = losses = draws = 0
    rows = conn.execute(
        "SELECT id, player_color, difficulty, result, pgn FROM games WHERE username = ? ORDER BY id ASC",
        (username,),
    ).fetchall()
    for row in rows:
        bot_elo = BOT_ELO.get(row["difficulty"], STARTING_RATING)
        score = _score_for_player(row["result"], row["player_color"])
        rating_before = rating
        rating = _update_elo(rating, bot_elo, score)
        if score == 1.0:
            wins += 1
        elif score == 0.0:
            losses += 1
        else:
            draws += 1
        termination = _infer_termination(row["pgn"], row["result"])
        conn.execute(
            "UPDATE games SET rating_before=?, rating_after=?, bot_elo=?, termination=? WHERE id=?",
            (round(rating_before), round(rating), bot_elo, termination, row["id"]),
        )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO ratings (username, rating, games_played, wins, losses, draws, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (username, rating, len(rows), wins, losses, draws, now),
    )
    conn.commit()
    conn.close()


class GameState:
    def __init__(self, username: str):
        self.username = username
        self.board = chess.Board()
        self.difficulty = "medium"
        self.model = load_style_model(username)
        self.player_color = chess.WHITE
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.resignation = None  # "1-0" / "0-1" set on resign
        self.saved = False
        self.rating_before = None  # set once the game is saved
        self.rating_after = None


game: GameState | None = None


def _play_bot_move() -> str | None:
    if game.board.is_game_over():
        return None
    move, _debug = get_blended_move(
        game.board.fen(), game.username, game.difficulty, model=game.model
    )
    san = game.board.san(move)
    game.board.push(move)
    return san


def _is_over() -> bool:
    return game.resignation is not None or game.board.is_game_over()


def _result() -> str | None:
    if game.resignation is not None:
        return game.resignation
    if game.board.is_game_over():
        return game.board.result()
    return None


def _save_game_if_over():
    if not _is_over() or game.saved:
        return
    game.saved = True  # set before persisting so a re-entrant call can't double-insert
    result = _result()
    termination = _termination_reason()
    player_color_str = "white" if game.player_color == chess.WHITE else "black"
    white_name = "You" if game.player_color == chess.WHITE else f"BetterMe-Bot ({game.difficulty})"
    black_name = f"BetterMe-Bot ({game.difficulty})" if game.player_color == chess.WHITE else "You"
    now = datetime.now(timezone.utc)

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rating_row = _get_or_init_rating(db, game.username)
    rating_before = rating_row["rating"]
    bot_elo = BOT_ELO.get(game.difficulty, STARTING_RATING)
    score = _score_for_player(result, player_color_str)
    rating_after = _update_elo(rating_before, bot_elo, score)

    white_elo = round(rating_before) if game.player_color == chess.WHITE else bot_elo
    black_elo = bot_elo if game.player_color == chess.WHITE else round(rating_before)

    pgn_game = chess.pgn.Game.from_board(game.board)
    pgn_game.headers["Event"] = f"Better Me Chess ({game.difficulty})"
    pgn_game.headers["Site"] = "Local"
    pgn_game.headers["Date"] = now.strftime("%Y.%m.%d")
    pgn_game.headers["Round"] = "-"
    pgn_game.headers["White"] = white_name
    pgn_game.headers["Black"] = black_name
    pgn_game.headers["Result"] = result
    pgn_game.headers["WhiteElo"] = str(white_elo)
    pgn_game.headers["BlackElo"] = str(black_elo)
    pgn_game.headers["TimeControl"] = "-"
    pgn_game.headers["Termination"] = _termination_text(result, termination, white_name, black_name)
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    pgn_text = pgn_game.accept(exporter)

    db.execute(
        "INSERT INTO games (username, player_color, difficulty, result, num_moves, pgn, started_at, ended_at, "
        "termination, rating_before, rating_after, bot_elo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            game.username,
            player_color_str,
            game.difficulty,
            result,
            game.board.fullmove_number,
            pgn_text,
            game.started_at,
            now.isoformat(),
            termination,
            round(rating_before),
            round(rating_after),
            bot_elo,
        ),
    )
    wins = 1 if score == 1.0 else 0
    losses = 1 if score == 0.0 else 0
    draws = 1 if score == 0.5 else 0
    db.execute(
        "UPDATE ratings SET rating=?, games_played=games_played+1, wins=wins+?, losses=losses+?, draws=draws+?, "
        "updated_at=? WHERE username=?",
        (rating_after, wins, losses, draws, now.isoformat(), game.username),
    )
    db.commit()
    db.close()
    game.rating_before = round(rating_before)
    game.rating_after = round(rating_after)


def status_payload(last_player_move=None, last_bot_move=None):
    return {
        "fen": game.board.fen(),
        "turn": "white" if game.board.turn == chess.WHITE else "black",
        "game_over": _is_over(),
        "result": _result(),
        "difficulty": game.difficulty,
        "player_color": "white" if game.player_color == chess.WHITE else "black",
        "last_player_move": last_player_move,
        "last_bot_move": last_bot_move,
        "rating_before": game.rating_before,
        "rating_after": game.rating_after,
    }


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/state")
def api_state():
    return jsonify(status_payload())


@app.route("/api/new_game", methods=["POST"])
def api_new_game():
    global game
    _save_game_if_over()  # safety net: persist a just-finished game before resetting

    data = request.get_json(force=True) or {}
    difficulty = data.get("difficulty", game.difficulty)
    player_color = data.get("player_color", "white")
    if difficulty not in DIFFICULTY_BLEND:
        return jsonify({"error": f"difficulty must be one of {list(DIFFICULTY_BLEND)}"}), 400

    username = game.username
    model = game.model
    game = GameState(username)
    game.model = model  # reuse already-loaded weights, no need to reload from disk
    game.difficulty = difficulty
    game.player_color = chess.WHITE if player_color == "white" else chess.BLACK

    last_bot_move = None
    if game.player_color == chess.BLACK:
        last_bot_move = _play_bot_move()

    return jsonify(status_payload(last_bot_move=last_bot_move))


@app.route("/api/move", methods=["POST"])
def api_move():
    data = request.get_json(force=True) or {}
    uci = data.get("move")
    if not uci:
        return jsonify({"error": "missing 'move' (uci string, e.g. 'e2e4')"}), 400
    if _is_over():
        return jsonify({"error": "game is already over"}), 400

    board = game.board
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return jsonify({"error": f"invalid move format: {uci}"}), 400

    if move not in board.legal_moves:
        return jsonify({"error": f"illegal move: {uci}"}), 400

    player_san = board.san(move)
    board.push(move)

    bot_san = _play_bot_move()
    _save_game_if_over()

    return jsonify(status_payload(last_player_move=player_san, last_bot_move=bot_san))


@app.route("/api/resign", methods=["POST"])
def api_resign():
    if _is_over():
        return jsonify(status_payload())
    game.resignation = "0-1" if game.player_color == chess.WHITE else "1-0"
    _save_game_if_over()
    return jsonify(status_payload())


@app.route("/api/hint", methods=["POST"])
def api_hint():
    """'Coach' hint: what would the blend engine (at current difficulty) play here?"""
    if _is_over():
        return jsonify({"error": "game is already over"}), 400
    move, debug = get_blended_move(
        game.board.fen(), game.username, game.difficulty, model=game.model
    )
    return jsonify({
        "san": game.board.san(move),
        "candidates": debug["candidates"][:3],
    })


@app.route("/api/history")
def api_history():
    db = get_db()
    rows = db.execute(
        "SELECT id, player_color, difficulty, result, num_moves, started_at, ended_at "
        "FROM games WHERE username = ? ORDER BY id DESC",
        (game.username,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/history/<int:game_id>")
def api_history_detail(game_id):
    db = get_db()
    row = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@app.route("/api/rating")
def api_rating():
    db = get_db()
    rating_row = _get_or_init_rating(db, game.username)
    per_diff_rows = db.execute(
        """SELECT difficulty,
                  COUNT(*) as games,
                  SUM(CASE WHEN (result='1-0' AND player_color='white') OR (result='0-1' AND player_color='black')
                           THEN 1 ELSE 0 END) as wins,
                  SUM(CASE WHEN result='1/2-1/2' THEN 1 ELSE 0 END) as draws
           FROM games WHERE username = ? GROUP BY difficulty""",
        (game.username,),
    ).fetchall()
    per_difficulty = []
    for r in per_diff_rows:
        wins = r["wins"] or 0
        draws = r["draws"] or 0
        per_difficulty.append({
            "difficulty": r["difficulty"],
            "bot_elo": BOT_ELO.get(r["difficulty"], STARTING_RATING),
            "games": r["games"],
            "wins": wins,
            "losses": r["games"] - wins - draws,
            "draws": draws,
        })
    return jsonify({
        "rating": round(rating_row["rating"]),
        "games_played": rating_row["games_played"],
        "wins": rating_row["wins"],
        "losses": rating_row["losses"],
        "draws": rating_row["draws"],
        "per_difficulty": per_difficulty,
        "bot_elo": BOT_ELO,
    })


@app.route("/api/history/<int:game_id>/pgn")
def api_history_pgn(game_id):
    db = get_db()
    row = db.execute("SELECT pgn, started_at FROM games WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    date_part = row["started_at"][:10] if row["started_at"] else "game"
    resp = Response(row["pgn"], mimetype="application/x-chess-pgn")
    resp.headers["Content-Disposition"] = f'attachment; filename="{date_part}_game{game_id}.pgn"'
    return resp


@app.route("/api/history/<int:game_id>/review")
def api_history_review(game_id):
    """Phase 5: run Stockfish over every move of a finished game, flag centipawn
    loss, and surface what the blend engine would've played instead at the
    worst spots."""
    db = get_db()
    row = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404

    # cap eval magnitude before diffing: once Stockfish spots a forced mate it
    # reports scores near mate_score (e.g. ~99997), and a single such jump can
    # swing "centipawn loss" by tens of thousands even when the position was
    # already thoroughly won/lost — clamp so mate detection doesn't drown out
    # real blunders in the average.
    CP_CAP = 1000

    pgn_game = chess.pgn.read_game(io.StringIO(row["pgn"]))
    player_is_white = row["player_color"] == "white"

    board = pgn_game.board()
    analysis = []
    with chess.engine.SimpleEngine.popen_uci(str(ENGINE_PATH)) as engine:
        for ply, move in enumerate(pgn_game.mainline_moves()):
            is_player_move = (board.turn == chess.WHITE) == player_is_white
            san = board.san(move)
            move_no = board.fullmove_number

            entry = {"ply": ply, "move_no": move_no, "san": san, "is_player": is_player_move}

            if is_player_move:
                info_before = engine.analyse(board, chess.engine.Limit(depth=15))
                best_move = info_before["pv"][0]
                cp_before = info_before["score"].pov(board.turn).score(mate_score=100_000)
                cp_before = max(-CP_CAP, min(CP_CAP, cp_before))

                board.push(move)
                info_after = engine.analyse(board, chess.engine.Limit(depth=15))
                cp_after_opp_pov = info_after["score"].pov(not board.turn).score(mate_score=100_000)
                cp_after_opp_pov = max(-CP_CAP, min(CP_CAP, cp_after_opp_pov))
                board.pop()

                cp_loss = max(0, cp_before - cp_after_opp_pov)
                entry["cp_loss"] = cp_loss
                entry["best_move_san"] = board.san(best_move) if best_move != move else san
                entry["was_best"] = best_move == move
            board.push(move)

            analysis.append(entry)

    player_moves = [a for a in analysis if a["is_player"]]
    blunders = sorted(
        [a for a in player_moves if a.get("cp_loss", 0) >= 100],
        key=lambda a: -a["cp_loss"],
    )[:5]
    avg_cp_loss = (
        sum(a.get("cp_loss", 0) for a in player_moves) / len(player_moves) if player_moves else 0
    )

    return jsonify({
        "moves": analysis,
        "blunders": blunders,
        "avg_cp_loss": round(avg_cp_loss, 1),
        "result": row["result"],
    })


def main():
    global game
    ap = argparse.ArgumentParser()
    ap.add_argument("username", nargs="?", default=os.environ.get("CHESS_USERNAME", "demo"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    args = ap.parse_args()

    init_db()
    _backfill_missing_data(args.username)
    game = GameState(args.username)
    print(f"Loaded style model for '{args.username}'.")
    print(f"Open http://127.0.0.1:{args.port} to play.")
    app.run(port=args.port, debug=False)


if __name__ == "__main__":
    main()
