const PIECE_BASE = "https://cdn.jsdelivr.net/gh/oakmac/chessboardjs@1.0.0/website/img/chesspieces/wikipedia";
function pieceTheme(piece) { return `${PIECE_BASE}/${piece}.png`; }

// ================= Sound (synthesized — no external audio asset) =================
let audioCtx = null;
function playTone(freq, duration) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "square";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start();
    osc.stop(audioCtx.currentTime + duration);
  } catch (e) { /* audio not available, ignore */ }
}
function playMoveSound(isCapture) {
  playTone(isCapture ? 160 : 260, isCapture ? 0.09 : 0.06);
}

// ---- live game state ----
let game = new Chess();
let board = null;
let myColor = "w";
let currentDifficulty = "medium";
let positions = [game.fen()];   // FEN after each ply, positions[0] = start
let sanHistory = [];            // SAN per ply
let moveSquares = [];           // {from,to} per ply
let viewIndex = 0;              // which position we're displaying
let lastFinishedGameId = null;

// ---- review view state ----
let reviewBoard = null;
let reviewPositions = [];
let reviewSanHistory = [];
let reviewMoveSquares = [];
let reviewViewIndex = 0;
let currentReviewGameId = null;

function isLive() { return viewIndex === positions.length - 1; }

// ================= Move application =================
function applyLocalMove(moveInput) {
  const mv = game.move(moveInput);
  if (!mv) return null;
  positions.push(game.fen());
  sanHistory.push(mv.san);
  moveSquares.push({ from: mv.from, to: mv.to, captured: !!mv.captured });
  return mv;
}

// ================= Board interaction =================
function onDragStart(source, piece) {
  if (!isLive()) return false;
  if (game.game_over()) return false;
  if (game.turn() !== myColor) return false;
  if ((myColor === "w" && piece.search(/^b/) !== -1) ||
      (myColor === "b" && piece.search(/^w/) !== -1)) return false;
}

function onDrop(source, target) {
  removeGreySquares();
  const mv = applyLocalMove({ from: source, to: target, promotion: "q" });
  if (mv === null) return "snapback";
  playMoveSound(!!mv.captured);
  viewIndex = positions.length - 1;
  renderMoveList();
  highlightLastMove();
  setTurnStatus("Bot thinking...");
  sendMove(mv.from + mv.to + (mv.promotion || ""));
}

function onSnapEnd() { board.position(game.fen()); }

function onMouseoverSquare(square) {
  if (!isLive() || game.game_over() || game.turn() !== myColor) return;
  const moves = game.moves({ square, verbose: true });
  if (!moves.length) return;
  removeGreySquares();
  moves.forEach((m) => {
    const isCapture = !!(m.flags && (m.flags.indexOf("c") !== -1 || m.flags.indexOf("e") !== -1));
    greySquare(m.to, isCapture);
  });
}
function onMouseoutSquare() { removeGreySquares(); }

function greySquare(square, isCapture) {
  $(`#board [data-square="${square}"]`).addClass(isCapture ? "square-legal-capture" : "square-legal-move");
}
function removeGreySquares() {
  $("#board [data-square]").removeClass("square-legal-move square-legal-capture");
}
function highlightLastMove() {
  $("#board [data-square]").removeClass("square-last-move square-hint-from square-hint-to");
  if (viewIndex > 0 && moveSquares[viewIndex - 1]) {
    const { from, to } = moveSquares[viewIndex - 1];
    $(`#board [data-square="${from}"]`).addClass("square-last-move");
    $(`#board [data-square="${to}"]`).addClass("square-last-move");
  }
}

// ================= Networking =================
async function sendMove(uci) {
  const res = await fetch("/api/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ move: uci }),
  });
  const data = await res.json();
  if (data.error) {
    document.getElementById("status-line").textContent = "Error: " + data.error;
    return;
  }
  if (data.last_bot_move) {
    const mv = applyLocalMove(data.last_bot_move);
    if (mv) playMoveSound(!!mv.captured);
  }
  board.position(game.fen());
  viewIndex = positions.length - 1;
  renderMoveList();
  highlightLastMove();
  setTurnStatus(describeTurn());
  document.getElementById("status-line").textContent = "";
  if (data.game_over) handleGameOver(data);
}

async function startNewGame(color, difficulty) {
  myColor = color === "white" ? "w" : "b";
  currentDifficulty = difficulty;

  const res = await fetch("/api/new_game", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ difficulty, player_color: color }),
  });
  const data = await res.json();

  game = new Chess();
  positions = [game.fen()];
  sanHistory = [];
  moveSquares = [];

  if (board) board.destroy();
  board = Chessboard("board", {
    draggable: true,
    position: game.fen(),
    orientation: color,
    pieceTheme,
    onDragStart, onDrop, onSnapEnd, onMouseoverSquare, onMouseoutSquare,
  });

  if (data.last_bot_move) {
    applyLocalMove(data.last_bot_move);
    board.position(game.fen());
  }
  viewIndex = positions.length - 1;

  renderMoveList();
  highlightLastMove();
  document.getElementById("opp-name").textContent = "BetterMe Bot";
  document.getElementById("opp-sub").textContent = difficulty[0].toUpperCase() + difficulty.slice(1);
  document.getElementById("coach-box").textContent =
    "Ask for a hint if you're stuck — it'll show what your \"better you\" blend would play here.";
  setTurnStatus(describeTurn());
  document.getElementById("status-line").textContent = "";
  document.getElementById("resign-btn").disabled = false;
  document.getElementById("game-over-btn").style.display = "none";
  pendingGameOverData = null;
}

// ================= Move list / navigation =================
function renderMoveList() {
  const el = document.getElementById("move-list");
  el.innerHTML = "";
  for (let i = 0; i < sanHistory.length; i += 2) {
    const numDiv = document.createElement("div");
    numDiv.className = "mv-num";
    numDiv.textContent = (i / 2 + 1) + ".";
    el.appendChild(numDiv);
    el.appendChild(makeMoveSpan(sanHistory[i], i + 1));
    el.appendChild(sanHistory[i + 1] !== undefined ? makeMoveSpan(sanHistory[i + 1], i + 2) : document.createElement("div"));
  }
  el.scrollTop = el.scrollHeight;
}
function makeMoveSpan(san, posIdx) {
  const div = document.createElement("div");
  div.className = "mv" + (posIdx === viewIndex ? " current" : "");
  div.textContent = san;
  div.addEventListener("click", () => showPosition(posIdx));
  return div;
}
function showPosition(idx) {
  idx = Math.max(0, Math.min(idx, positions.length - 1));
  const changed = idx !== viewIndex;
  viewIndex = idx;
  board.position(positions[idx]);
  renderMoveList();
  highlightLastMove();
  document.getElementById("review-banner").classList.toggle("show", !isLive());
  if (changed && idx > 0 && moveSquares[idx - 1]) playMoveSound(!!moveSquares[idx - 1].captured);
}

document.getElementById("nav-start").addEventListener("click", () => showPosition(0));
document.getElementById("nav-back").addEventListener("click", () => showPosition(viewIndex - 1));
document.getElementById("nav-fwd").addEventListener("click", () => showPosition(viewIndex + 1));
document.getElementById("nav-end").addEventListener("click", () => showPosition(positions.length - 1));
document.getElementById("back-to-live").addEventListener("click", () => showPosition(positions.length - 1));

document.addEventListener("keydown", (e) => {
  if (document.getElementById("view-play").classList.contains("active")) {
    if (e.key === "ArrowLeft") showPosition(viewIndex - 1);
    if (e.key === "ArrowRight") showPosition(viewIndex + 1);
  } else if (document.getElementById("view-review").classList.contains("active")) {
    if (e.key === "ArrowLeft") { stopReviewAutoplay(); reviewShowPosition(reviewViewIndex - 1); }
    if (e.key === "ArrowRight") { stopReviewAutoplay(); reviewShowPosition(reviewViewIndex + 1); }
  }
});

// ================= Review move list / navigation =================
function renderReviewMoveList() {
  const el = document.getElementById("review-move-list");
  el.innerHTML = "";
  for (let i = 0; i < reviewSanHistory.length; i += 2) {
    const numDiv = document.createElement("div");
    numDiv.className = "mv-num";
    numDiv.textContent = (i / 2 + 1) + ".";
    el.appendChild(numDiv);
    el.appendChild(makeReviewMoveSpan(reviewSanHistory[i], i + 1));
    el.appendChild(reviewSanHistory[i + 1] !== undefined ? makeReviewMoveSpan(reviewSanHistory[i + 1], i + 2) : document.createElement("div"));
  }
}
function makeReviewMoveSpan(san, posIdx) {
  const div = document.createElement("div");
  div.className = "mv" + (posIdx === reviewViewIndex ? " current" : "");
  div.textContent = san;
  div.addEventListener("click", () => { stopReviewAutoplay(); reviewShowPosition(posIdx); });
  return div;
}
function highlightReviewLastMove() {
  $("#review-board [data-square]").removeClass("square-last-move");
  if (reviewViewIndex > 0 && reviewMoveSquares[reviewViewIndex - 1]) {
    const { from, to } = reviewMoveSquares[reviewViewIndex - 1];
    $(`#review-board [data-square="${from}"]`).addClass("square-last-move");
    $(`#review-board [data-square="${to}"]`).addClass("square-last-move");
  }
}
function reviewShowPosition(idx) {
  idx = Math.max(0, Math.min(idx, reviewPositions.length - 1));
  const changed = idx !== reviewViewIndex;
  reviewViewIndex = idx;
  reviewBoard.position(reviewPositions[idx]);
  renderReviewMoveList();
  highlightReviewLastMove();
  if (changed && idx > 0 && reviewMoveSquares[idx - 1]) playMoveSound(!!reviewMoveSquares[idx - 1].captured);
}

// ---- autoplay: steps through the whole game, one move every 3s ----
let reviewPlayTimer = null;
function stopReviewAutoplay() {
  if (reviewPlayTimer) { clearInterval(reviewPlayTimer); reviewPlayTimer = null; }
  document.getElementById("rv-play-btn").textContent = "▶ Play";
}
function toggleReviewAutoplay() {
  if (reviewPlayTimer) { stopReviewAutoplay(); return; }
  if (reviewViewIndex >= reviewPositions.length - 1) reviewShowPosition(0);
  document.getElementById("rv-play-btn").textContent = "⏸ Pause";
  reviewPlayTimer = setInterval(() => {
    if (reviewViewIndex >= reviewPositions.length - 1) { stopReviewAutoplay(); return; }
    reviewShowPosition(reviewViewIndex + 1);
  }, 3000);
}
document.getElementById("rv-play-btn").addEventListener("click", toggleReviewAutoplay);
document.getElementById("rv-nav-start").addEventListener("click", () => { stopReviewAutoplay(); reviewShowPosition(0); });
document.getElementById("rv-nav-back").addEventListener("click", () => { stopReviewAutoplay(); reviewShowPosition(reviewViewIndex - 1); });
document.getElementById("rv-nav-fwd").addEventListener("click", () => { stopReviewAutoplay(); reviewShowPosition(reviewViewIndex + 1); });
document.getElementById("rv-nav-end").addEventListener("click", () => { stopReviewAutoplay(); reviewShowPosition(reviewPositions.length - 1); });

// ================= Rating =================
async function loadRating() {
  try {
    const data = await fetch("/api/rating").then((r) => r.json());
    document.getElementById("rating-badge").textContent =
      `Rating: ${data.rating} · ${data.wins}W ${data.losses}L ${data.draws}D`;
    return data;
  } catch (e) { return null; }
}

// ================= Status text =================
function describeTurn() {
  if (game.in_checkmate()) return `Checkmate — ${game.turn() === "w" ? "Black" : "White"} wins.`;
  if (game.in_draw()) return "Draw.";
  if (game.in_check()) return `${game.turn() === "w" ? "White" : "Black"} to move — check!`;
  return `${game.turn() === "w" ? "White" : "Black"} to move.`;
}
function setTurnStatus(text) { document.getElementById("status-line-inline").textContent = text; }

// ================= New Game modal =================
function openNewGameModal() { document.getElementById("new-game-modal").classList.add("show"); }
function closeNewGameModal() { document.getElementById("new-game-modal").classList.remove("show"); }

document.getElementById("new-game-btn").addEventListener("click", openNewGameModal);
document.getElementById("new-game-cancel").addEventListener("click", closeNewGameModal);

document.querySelectorAll("#color-choices .choice-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#color-choices .choice-btn").forEach((b) => b.classList.remove("selected"));
    btn.classList.add("selected");
  });
});
document.querySelectorAll("#difficulty-choices .choice-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#difficulty-choices .choice-btn").forEach((b) => b.classList.remove("selected"));
    btn.classList.add("selected");
  });
});
document.getElementById("new-game-start").addEventListener("click", async () => {
  const color = document.querySelector("#color-choices .selected").dataset.value;
  const difficulty = document.querySelector("#difficulty-choices .selected").dataset.value;
  closeNewGameModal();
  await startNewGame(color, difficulty);
});

// ================= Resign =================
document.getElementById("resign-btn").addEventListener("click", async () => {
  if (!confirm("Resign this game?")) return;
  const res = await fetch("/api/resign", { method: "POST" });
  const data = await res.json();
  if (data.game_over) handleGameOver(data);
});

// ================= Coach hint =================
document.getElementById("hint-btn").addEventListener("click", async () => {
  if (game.game_over()) return;
  if (!isLive()) { alert("Jump back to the live position first."); return; }
  const res = await fetch("/api/hint", { method: "POST" });
  const data = await res.json();
  if (data.error) { document.getElementById("coach-box").textContent = data.error; return; }
  document.getElementById("coach-box").innerHTML = `Coach suggests: <b>${data.san}</b>`;

  const tmp = new Chess(positions[positions.length - 1]);
  const mv = tmp.move(data.san);
  if (mv) {
    $("#board [data-square]").removeClass("square-hint-from square-hint-to");
    $(`#board [data-square="${mv.from}"]`).addClass("square-hint-from");
    $(`#board [data-square="${mv.to}"]`).addClass("square-hint-to");
  }
});

// ================= Game over =================
function describeResult(result, playerColor) {
  if (result === "1/2-1/2") return { headline: "Draw", sub: "The game ended in a draw." };
  const playerWon = (result === "1-0" && playerColor === "white") || (result === "0-1" && playerColor === "black");
  return playerWon
    ? { headline: "You won! 🎉", sub: "Nice game." }
    : { headline: "You lost", sub: "Review the game to see where it slipped." };
}
let pendingGameOverData = null;

function ratingDeltaText(data) {
  if (data.rating_before == null || data.rating_after == null) return "";
  const delta = data.rating_after - data.rating_before;
  return ` · Rating ${data.rating_after} (${delta >= 0 ? "+" : ""}${delta})`;
}
function showGameOverModal() {
  if (!pendingGameOverData) return;
  const { headline, sub } = describeResult(pendingGameOverData.result, pendingGameOverData.player_color);
  document.getElementById("go-headline").textContent = headline;
  document.getElementById("go-sub").textContent = sub + ratingDeltaText(pendingGameOverData).replace(" · ", " ");
  document.getElementById("gameover-modal").classList.add("show");
  document.getElementById("go-review").style.display = lastFinishedGameId ? "inline-block" : "none";
}
async function handleGameOver(data) {
  document.getElementById("resign-btn").disabled = true;
  pendingGameOverData = data;

  const { headline } = describeResult(data.result, data.player_color);
  setTurnStatus(headline + ratingDeltaText(data));
  document.getElementById("game-over-btn").style.display = "block";

  const hist = await fetch("/api/history").then((r) => r.json());
  lastFinishedGameId = hist.length ? hist[0].id : null;
  loadRating();
  showGameOverModal();
}
function hideGameOverModal() { document.getElementById("gameover-modal").classList.remove("show"); }
document.getElementById("game-over-btn").addEventListener("click", showGameOverModal);
document.getElementById("go-close").addEventListener("click", hideGameOverModal);
document.getElementById("go-rematch").addEventListener("click", () => { hideGameOverModal(); openNewGameModal(); });
document.getElementById("go-review").addEventListener("click", () => {
  hideGameOverModal();
  if (lastFinishedGameId) { switchTab("review"); openReview(lastFinishedGameId); }
});

// ================= Tabs =================
function switchTab(name) {
  if (name !== "review") stopReviewAutoplay();
  document.getElementById("tab-play").classList.toggle("active", name === "play");
  document.getElementById("tab-history").classList.toggle("active", name === "history" || name === "review");
  document.getElementById("view-play").classList.toggle("active", name === "play");
  document.getElementById("view-history").classList.toggle("active", name === "history");
  document.getElementById("view-review").classList.toggle("active", name === "review");
  if (name === "history") loadHistory();
}
document.getElementById("tab-play").addEventListener("click", () => switchTab("play"));
document.getElementById("tab-history").addEventListener("click", () => switchTab("history"));
document.getElementById("review-back-btn").addEventListener("click", () => switchTab("history"));
document.getElementById("rv-export-btn").addEventListener("click", () => {
  if (currentReviewGameId) window.location.href = `/api/history/${currentReviewGameId}/pgn`;
});
document.getElementById("rv-copy-btn").addEventListener("click", async () => {
  if (!currentReviewGameId) return;
  const detail = await fetch(`/api/history/${currentReviewGameId}`).then((r) => r.json());
  const btn = document.getElementById("rv-copy-btn");
  const original = btn.textContent;
  try {
    await navigator.clipboard.writeText(detail.pgn);
    btn.textContent = "✓ Copied!";
  } catch (e) {
    window.prompt("Copy PGN:", detail.pgn);
    btn.textContent = original;
    return;
  }
  setTimeout(() => { btn.textContent = original; }, 1500);
});

// ================= History =================
function historyPill(result, playerColor) {
  if (result === "1/2-1/2") return { headline: "Draw", cls: "result-draw" };
  const won = (result === "1-0" && playerColor === "white") || (result === "0-1" && playerColor === "black");
  return won ? { headline: "Win", cls: "result-win" } : { headline: "Loss", cls: "result-loss" };
}
async function loadRatingSummary() {
  const data = await loadRating();
  if (!data) return;
  document.getElementById("rating-stats").innerHTML = `
    <div class="stat-box"><div class="stat-value">${data.rating}</div><div class="stat-label">Rating</div></div>
    <div class="stat-box"><div class="stat-value">${data.wins}-${data.losses}-${data.draws}</div><div class="stat-label">W-L-D</div></div>
    <div class="stat-box"><div class="stat-value">${data.games_played}</div><div class="stat-label">Games</div></div>
  `;
  const diffEl = document.getElementById("rating-per-diff");
  if (!data.per_difficulty.length) {
    diffEl.textContent = "No games yet.";
  } else {
    diffEl.innerHTML = data.per_difficulty.map((d) =>
      `${d.difficulty[0].toUpperCase() + d.difficulty.slice(1)} bot (~${d.bot_elo} elo): ${d.wins}W ${d.losses}L ${d.draws}D`
    ).join("<br>");
  }
}
async function loadHistory() {
  loadRatingSummary();
  const el = document.getElementById("history-list");
  el.innerHTML = '<div class="empty-note">Loading...</div>';
  const rows = await fetch("/api/history").then((r) => r.json());
  if (!rows.length) {
    el.innerHTML = '<div class="empty-note">No finished games yet. Play one!</div>';
    return;
  }
  el.innerHTML = "";
  rows.forEach((row) => {
    const div = document.createElement("div");
    div.className = "history-row";
    const { headline, cls } = historyPill(row.result, row.player_color);
    const date = new Date(row.ended_at).toLocaleString();
    div.innerHTML = `
      <div class="history-meta">
        <div class="h-title">You (${row.player_color}) vs Bot · ${row.difficulty}</div>
        <div class="h-sub">${date} · ${row.num_moves} moves</div>
      </div>
      <div style="display:flex; align-items:center; gap:8px">
        <button class="ghost" data-export="${row.id}" title="Export PGN">⬇</button>
        <div class="result-pill ${cls}">${headline}</div>
      </div>
    `;
    div.querySelector("[data-export]").addEventListener("click", (e) => {
      e.stopPropagation();
      window.location.href = `/api/history/${row.id}/pgn`;
    });
    div.addEventListener("click", () => { switchTab("review"); openReview(row.id); });
    el.appendChild(div);
  });
}

// ================= Review =================
async function openReview(gameId) {
  stopReviewAutoplay();
  currentReviewGameId = gameId;
  document.getElementById("rv-avgcp").textContent = "…";
  document.getElementById("rv-blunders").textContent = "…";
  document.getElementById("rv-result").textContent = "…";
  document.getElementById("rv-detail").textContent = "Running Stockfish over the game...";
  document.getElementById("rv-blunder-list").innerHTML = "";

  const [detail, review] = await Promise.all([
    fetch(`/api/history/${gameId}`).then((r) => r.json()),
    fetch(`/api/history/${gameId}/review`).then((r) => r.json()),
  ]);

  document.getElementById("rv-avgcp").textContent = review.avg_cp_loss;
  document.getElementById("rv-blunders").textContent = review.blunders.length;
  document.getElementById("rv-result").textContent = review.result;
  const ratingLine = detail.rating_before != null
    ? ` Rating ${detail.rating_before} → ${detail.rating_after}.`
    : "";
  document.getElementById("rv-detail").textContent =
    `${detail.player_color === "white" ? "You played White" : "You played Black"} vs the ${detail.difficulty} bot ` +
    `(~${detail.bot_elo} elo). Ended by ${detail.termination || "?"}.${ratingLine}`;

  const listEl = document.getElementById("rv-blunder-list");
  if (!review.blunders.length) {
    listEl.innerHTML = '<div class="empty-note">No real blunders — solid game.</div>';
  } else {
    review.blunders.forEach((b) => {
      const row = document.createElement("div");
      row.className = "blunder-row";
      row.innerHTML = `
        <span>${b.move_no}. ${b.san} <span style="color:var(--muted)">→ better: ${b.best_move_san}</span></span>
        <span class="badge badge-blunder">-${b.cp_loss}cp</span>
      `;
      row.addEventListener("click", () => { stopReviewAutoplay(); reviewShowPosition(b.ply); });
      listEl.appendChild(row);
    });
  }

  const tmp = new Chess();
  tmp.load_pgn(detail.pgn);
  const verboseMoves = tmp.history({ verbose: true });
  const replay = new Chess();
  reviewPositions = [replay.fen()];
  reviewSanHistory = [];
  reviewMoveSquares = [];
  verboseMoves.forEach((m) => {
    replay.move(m.san);
    reviewPositions.push(replay.fen());
    reviewSanHistory.push(m.san);
    reviewMoveSquares.push({ from: m.from, to: m.to, captured: !!m.captured });
  });

  if (reviewBoard) reviewBoard.destroy();
  reviewBoard = Chessboard("review-board", {
    draggable: false,
    position: reviewPositions[0],
    orientation: detail.player_color,
    pieceTheme,
  });
  reviewShowPosition(0);
}

// ================= Boot =================
board = Chessboard("board", { position: "start", pieceTheme });
openNewGameModal();
loadRating();
