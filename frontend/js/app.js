/* 星陨竞技场前端逻辑
   结构：全局状态 → WebSocket 通信 → 各屏渲染 → 战斗回放。
   后端给出的战报是事件列表，前端只负责按顺序播放并把血量变化画出来。 */
(function () {
  "use strict";

  const MAX_NAME = 12;
  const PLAY_SPEEDS = [1, 2, 4];
  const BASE_TICK_MS = 520;
  // 一场回放的总时长预算：事件多的时候自动加快，避免回放吃掉下一局的备战时间
  const REPLAY_BUDGET_MS = 11000;
  const MIN_TICK_MS = 60;
  const HISTORY_KEY = "nc_history";
  const HISTORY_LIMIT = 5;
  const RECONNECT_LIMIT = 5;
  const SESSION_KEY = "nc_session";
  const MODE_KEY = "nc_mode";

  const state = {
    ws: null,
    connected: false,
    reconnectTimer: null,
    name: "",
    roomCode: null,
    seat: null,
    roundIndex: 0,
    score: [0, 0],
    pool: [],
    selection: [],
    bonuses: [],
    strategy: { kind: "lowest_hp", tag: null },
    strategies: [],
    bonusOptions: [],
    teamSize: 3,
    poolSize: 6,
    bonusPerRound: 4,
    maxBonusPerFighter: 2,
    submitted: false,
    opponentReady: false,
    deadlineAt: 0,
    timerHandle: null,
    battle: null,
    lastBattle: null,
    urgentWarned: false,
    reconnectAttempts: 0,
    pendingResult: null,
    pendingGameOver: null,
    nextRound: null,
    nextRoundDeadline: 0,
    resultTimer: null,
    rules: null,
    rulesLoading: false,
    previousScreen: "screen-lobby",
    overlayActive: false,
    opponentDisconnected: false,
    modeKey: "standard",
    modes: [],
    randomTags: false,
  };

  const el = (id) => document.getElementById(id);
  const teamLabel = (team) => (team === state.seat ? "你" : "对手");

  /* ------------------------------------------------------------ 通用工具 */
  function show(screenId) {
    document.querySelectorAll(".screen").forEach((node) => {
      node.classList.toggle("is-active", node.id === screenId);
    });
  }

  function setConn(kind, text) {
    const node = el("conn-status");
    node.textContent = text;
    node.className = "pill " + (kind === "ok" ? "pill-ok" : kind === "warn" ? "pill-warn" : "pill-bad");
  }

  let toastTimer = null;
  function toast(message, isError) {
    const node = el("toast");
    node.textContent = message;
    node.className = "toast is-visible" + (isError ? " is-error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.className = "toast" + (isError ? " is-error" : "");
    }, 3200);
  }

  function showOverlay(html) {
    el("overlay-card").innerHTML = html;
    el("overlay").classList.remove("hidden");
    state.overlayActive = true;
  }

  function hideOverlay() {
    el("overlay").classList.add("hidden");
    state.overlayActive = false;
  }

  function send(payload) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      toast("尚未连接到服务器，请稍后重试", true);
      return false;
    }
    state.ws.send(JSON.stringify(payload));
    return true;
  }

  function currentNickname() {
    const value = el("input-name").value.trim();
    return value.slice(0, MAX_NAME);
  }

  function rememberInputs() {
    try {
      localStorage.setItem("nc_name", currentNickname());
      localStorage.setItem("nc_code", el("input-code").value.trim().toUpperCase());
    } catch (err) {
      /* 隐私模式下 localStorage 不可用，忽略即可 */
    }
  }

  /* ------------------------------------------------------------ 会话（断线重连用）
     注意：必须用 sessionStorage —— localStorage 在同源的所有标签页之间共享，
     同一台电脑开着两个标签页对战时，后加入的标签页会覆盖前一个的 token，
     刷新后会把自己重连成对手的座位。sessionStorage 是每个标签页独立的。 */
  function sessionStore() {
    try {
      return window.sessionStorage;
    } catch (err) {
      return null;
    }
  }

  function saveSession(roomCode, token) {
    const store = sessionStore();
    if (!store) return;
    try {
      store.setItem(SESSION_KEY, JSON.stringify({ roomCode: roomCode, token: token }));
    } catch (err) {
      /* 隐私模式下忽略 */
    }
  }

  function readSession() {
    const store = sessionStore();
    if (!store) return null;
    try {
      const raw = JSON.parse(store.getItem(SESSION_KEY) || "null");
      return raw && raw.roomCode && raw.token ? raw : null;
    } catch (err) {
      return null;
    }
  }

  function clearSession() {
    const store = sessionStore();
    try {
      if (store) store.removeItem(SESSION_KEY);
    } catch (err) {
      /* 忽略 */
    }
    hideMatching();
  }

  function hideMatching() {
    el("matching").classList.add("hidden");
  }

  /* ------------------------------------------------------------ 游戏模式 */
  function renderModes() {
    const box = el("mode-options");
    if (!state.modes.length) return;
    box.innerHTML = "";
    state.modes.forEach((mode) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mode-option" + (mode.key === state.modeKey ? " is-active" : "");
      const title = document.createElement("strong");
      title.textContent = mode.name;
      const summary = document.createElement("span");
      summary.textContent = mode.summary;
      button.append(title, summary);
      button.addEventListener("click", () => {
        state.modeKey = mode.key;
        state.randomTags = Boolean(mode.random_tags);
        try {
          localStorage.setItem(MODE_KEY, mode.key);
        } catch (err) {
          /* 忽略 */
        }
        renderModes();
      });
      box.appendChild(button);
    });
  }

  function restoreMode() {
    try {
      const saved = localStorage.getItem(MODE_KEY);
      if (saved) state.modeKey = saved;
    } catch (err) {
      /* 忽略 */
    }
  }

  function restoreInputs() {
    try {
      const name = localStorage.getItem("nc_name");
      const code = localStorage.getItem("nc_code");
      if (name) el("input-name").value = name;
      if (code) el("input-code").value = code;
    } catch (err) {
      /* 同上 */
    }
  }

  function resetToLobby(message) {
    stopTimer();
    stopPlayback();
    clearSession();
    state.roomCode = null;
    state.seat = null;
    state.pool = [];
    state.selection = [];
    state.bonuses = [];
    state.submitted = false;
    state.opponentReady = false;
    state.battle = null;
    state.pendingResult = null;
    state.pendingGameOver = null;
    state.nextRound = null;
    state.nextRoundDeadline = 0;
    clearTimeout(state.resultTimer);
    state.resultTimer = null;
    hideOverlay();
    show("screen-lobby");
    el("timer").classList.remove("is-urgent");
    if (message) toast(message);
  }

  /* ------------------------------------------------------------ WebSocket */
  function connect() {
    if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const scheme = location.protocol === "https:" ? "wss://" : "ws://";
    setConn("warn", "连接中…");
    const ws = new WebSocket(scheme + location.host + "/ws");
    state.ws = ws;

    ws.onopen = () => {
      state.connected = true;
      state.reconnectAttempts = 0;
      setConn("ok", "已连接");
      const session = readSession();
      if (session) {
        // 带着上次的房间凭据回来：优先恢复原来的对局
        setConn("warn", "正在恢复对局…");
        send({ type: "reconnect", room_code: session.roomCode, token: session.token });
        return;
      }
      if (state.roomCode) {
        resetToLobby("已重新连接；由于断线，上一局已经结束，请重新创建或加入房间");
      }
    };

    ws.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      handleMessage(message);
    };

    ws.onclose = () => {
      state.connected = false;
      setConn("bad", "已断开");
      if (!state.overlayActive) {
        if (state.reconnectAttempts < RECONNECT_LIMIT) {
          state.reconnectAttempts += 1;
          const delay = Math.min(8000, 500 * 2 ** state.reconnectAttempts);
          setConn("warn", `重连中…（${state.reconnectAttempts}/${RECONNECT_LIMIT}）`);
          clearTimeout(state.reconnectTimer);
          state.reconnectTimer = setTimeout(connect, delay);
          return;
        }
        showOverlay(
          `<h2>与服务器断开连接</h2>
           <p>多次自动重连都没有成功。检查网络后刷新页面即可重新开始。</p>
           <div class="overlay-actions"><button class="primary" onclick="location.reload()">刷新重连</button></div>`
        );
      }
    };

    ws.onerror = () => setConn("bad", "连接异常");
  }

  function handleMessage(message) {
    switch (message.type) {
      case "hello":
        el("brand-version").textContent = message.server + " v" + message.version;
        break;
      case "room_joined":
        onRoomJoined(message);
        break;
      case "state_sync":
        onStateSync(message);
        break;
      case "matchmaking_waiting":
        el("matching").classList.remove("hidden");
        el("lobby-hint").textContent = "";
        break;
      case "matchmaking_cancelled":
        hideMatching();
        toast("已取消匹配");
        break;
      case "opponent_disconnected":
        state.opponentDisconnected = true;
        showOpponentBanner(
          `${message.name} 掉线了，正在等待重连（最多 ${message.grace_seconds} 秒）`
        );
        toast(`${message.name} 掉线了，对局进度会保留`, true);
        break;
      case "opponent_reconnected":
        state.opponentDisconnected = false;
        hideOpponentBanner();
        toast(`${message.name} 已重新连接`);
        break;
      case "opponent_joined":
        toast(message.name + " 加入了房间");
        break;
      case "round_start":
        onRoundStart(message);
        break;
      case "plan_accepted":
        onPlanAccepted(message, false);
        break;
      case "plan_auto_submitted":
        onPlanAccepted(message, true);
        break;
      case "opponent_ready":
        state.opponentReady = true;
        renderPrepareStatus();
        toast("对手已提交方案");
        break;
      case "battle_report":
        onBattleReport(message);
        break;
      case "round_result":
        onRoundResult(message);
        break;
      case "game_over":
        onGameOver(message);
        break;
      case "rematch_started":
        // 双方都同意再来一局：清掉上一场的残留状态，等 round_start 建新局
        state.pendingResult = null;
        state.pendingGameOver = null;
        state.nextRound = null;
        state.nextRoundDeadline = 0;
        state.battle = null;
        hideOverlay();
        break;
      case "opponent_rematch":
        toast("对手想再来一局");
        break;
      case "room_closed":
        onRoomClosed(message);
        break;
      case "left_room":
        resetToLobby("已离开房间");
        break;
      case "error":
        toast(message.message, true);
        if (message.fatal) resetToLobby();
        break;
      default:
        break;
    }
  }

  /* ------------------------------------------------------------ 大厅 */
  function onRoomJoined(message) {
    state.roomCode = message.room_code;
    state.seat = message.seat;
    state.score = message.score || [0, 0];
    state.opponentDisconnected = false;
    hideMatching();
    hideOpponentBanner();
    if (message.token) saveSession(message.room_code, message.token);
    if (message.mode) applyModeFromMessage(message);
    el("room-code").textContent = message.room_code;
    renderWaitingPlayers(message.players || []);
    show("screen-waiting");
  }

  function showOpponentBanner(text) {
    const banner = el("opponent-banner");
    banner.textContent = text;
    banner.classList.remove("hidden");
  }

  function hideOpponentBanner() {
    el("opponent-banner").classList.add("hidden");
  }

  function onStateSync(message) {
    state.roomCode = message.room_code;
    state.seat = message.seat;
    state.score = message.score || [0, 0];
    saveSession(message.room_code, message.token);
    setConn("ok", "已连接");
    stopTimer();
    stopPlayback();
    state.battle = null;
    state.pendingResult = null;
    state.pendingGameOver = null;
    state.nextRound = null;
    state.nextRoundDeadline = 0;
    state.opponentReady = Boolean(message.opponent_ready);
    state.submitted = Boolean(message.submitted);
    applyModeFromMessage(message);

    if (message.phase === "preparing" && message.pool) {
      hideOverlay();
      state.roundIndex = message.round_index;
      state.pool = message.pool;
      state.strategies = message.strategies || [];
      state.bonusOptions = message.bonus_options || [];
      state.teamSize = message.team_size || 3;
      state.bonusPerRound = message.bonus_per_round || 4;
      state.maxBonusPerFighter = message.max_bonus_per_fighter || 2;
      if (message.plan) {
        state.selection = message.plan.selection.slice();
        state.bonuses = message.plan.bonuses.slice();
        state.strategy = { kind: message.plan.strategy.kind, tag: message.plan.strategy.tag };
        el("btn-submit").textContent = "更新方案";
      } else {
        state.selection = [];
        state.bonuses = [];
        state.strategy = { kind: "lowest_hp", tag: null };
        el("btn-submit").textContent = "提交方案";
      }
      renderPrepare();
      show("screen-prepare");
      startTimer(message.remaining_seconds || 60);
      toast("已回到原来的房间，继续你的部署");
      return;
    }

    if (message.phase === "waiting") {
      hideOverlay();
      el("room-code").textContent = message.room_code;
      renderWaitingPlayers(message.players || []);
      show("screen-waiting");
      toast("已回到原来的房间，等待对手加入");
      return;
    }

    // 对局已经结束或已关闭：回大厅重新开始
    clearSession();
    resetToLobby("上一场对局已经结束，请重新创建或加入房间");
  }

  function renderWaitingPlayers(players) {
    const list = el("waiting-players");
    list.innerHTML = "";
    const total = 2;
    for (let seat = 0; seat < total; seat += 1) {
      const player = players.find((p) => p.seat === seat);
      const li = document.createElement("li");
      const nameNode = document.createElement("b");
      if (player) {
        nameNode.textContent = player.name + (seat === state.seat ? "（你）" : "");
        li.appendChild(nameNode);
        const status = document.createElement("span");
        status.textContent = "已就位";
        li.appendChild(status);
      } else {
        nameNode.textContent = "等待对手加入…";
        nameNode.style.color = "var(--text-dim)";
        li.appendChild(nameNode);
        const status = document.createElement("span");
        status.textContent = "空位";
        li.appendChild(status);
      }
      list.appendChild(li);
    }
  }

  function copyRoomCode() {
    const code = state.roomCode || "";
    if (!code) return;
    copyText(code, "房间号已复制：" + code);
  }

  function copyInviteLink() {
    const code = state.roomCode || "";
    if (!code) return;
    const link = `${location.origin}${location.pathname}?room=${code}`;
    copyText(link, "邀请链接已复制：" + link);
  }

  function copyText(text, okMessage) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => toast(okMessage),
        () => toast("复制失败，请手动复制：" + text, true)
      );
    } else {
      toast("请手动复制：" + text);
    }
  }

  function escapeHtml(text) {
    return String(text).replace(
      /[&<>"']/g,
      (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])
    );
  }

  function showBattleReview() {
    const review = state.lastBattle;
    if (!review) {
      toast("还没有可以回看的战报");
      return;
    }
    const rows = review.lines
      .map((line) => `<p class="${line.kind === "round_start" ? "is-round" : ""}">${escapeHtml(line.text)}</p>`)
      .join("");
    showOverlay(
      `<h2>第 ${review.roundIndex} 局战报</h2>
       <div class="battle-review">${rows}</div>
       <div class="overlay-actions"><button class="primary" id="btn-review-close" type="button">关闭</button></div>`
    );
    el("btn-review-close").addEventListener("click", hideOverlay);
  }

  /* ------------------------------------------------------------ 最近战绩 */
  function readHistory() {
    try {
      const list = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
      return Array.isArray(list) ? list : [];
    } catch (err) {
      return [];
    }
  }

  function recordMatch(win, myScore, opponentScore) {
    const list = readHistory();
    list.unshift({ at: Date.now(), win: win, my: myScore, opp: opponentScore });
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_LIMIT)));
    } catch (err) {
      /* 隐私模式下写不了就跳过 */
    }
    renderHistory();
  }

  function renderHistory() {
    const box = el("lobby-history");
    const list = el("recent-history");
    const items = readHistory();
    box.classList.toggle("hidden", items.length === 0);
    list.innerHTML = "";
    items.forEach((item) => {
      const li = document.createElement("li");
      const when = new Date(item.at).toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
      const left = document.createElement("span");
      left.textContent = `${when} · ${item.win ? "胜" : "负"}`;
      const right = document.createElement("span");
      right.textContent = `${item.my} : ${item.opp}`;
      li.append(left, right);
      list.appendChild(li);
    });
  }

  /* ------------------------------------------------------------ 备战 */
  function onRoundStart(message) {
    stopTimer();
    state.nextRound = message;
    state.nextRoundDeadline = Date.now() + (message.deadline_seconds || 60) * 1000;
    // 上一局的战斗回放还没放完，就先把这一局看完再进入下一局
    if (state.battle && state.battle.playing) return;
    if (state.pendingResult) {
      if (!state.overlayActive) {
        showResultOverlay(state.pendingResult);
        return;
      }
      // 覆盖层已经开着（例如上一场的结算卡片还没关），直接进入新一局，
      // 保证任何 round_start 都不会被残留状态卡住
    }
    applyNextRound();
  }

  function applyNextRound() {
    const message = state.nextRound;
    if (!message) return;
    state.nextRound = null;
    state.pendingResult = null;
    state.battle = null;
    clearTimeout(state.resultTimer);
    state.resultTimer = null;
    hideOverlay();

    applyModeFromMessage(message);
    state.roundIndex = message.round_index;
    state.score = message.score || [0, 0];
    state.pool = message.pool || [];
    state.strategies = message.strategies || [];
    state.bonusOptions = message.bonus_options || [];
    state.teamSize = message.team_size || 3;
    state.poolSize = message.pool_size || 6;
    state.bonusPerRound = message.bonus_per_round || 4;
    state.maxBonusPerFighter = message.max_bonus_per_fighter || 2;
    state.selection = [];
    state.bonuses = [];
    state.strategy = { kind: "lowest_hp", tag: null };
    state.submitted = false;
    state.opponentReady = false;

    renderPrepare();
    show("screen-prepare");
    el("btn-submit").textContent = "提交方案";
    el("submit-hint").textContent = "";
    startTimer(message.deadline_seconds || 60);
  }

  function applyModeFromMessage(message) {
    if (!message.mode) return;
    state.modeKey = message.mode;
    state.randomTags = Boolean(message.random_tags);
    const badge = el("mode-badge");
    badge.textContent = message.mode_name || message.mode;
    badge.classList.remove("hidden");
    document.body.dataset.mode = message.mode;
  }

  function renderPrepare() {
    el("round-index").textContent = String(state.roundIndex);
    el("score-display").textContent = state.score.join(" : ");
    el("btn-review").classList.toggle("hidden", !state.lastBattle);
    const badge = el("mode-badge");
    if (state.modeKey) {
      const mode = state.modes.find((item) => item.key === state.modeKey);
      badge.textContent = mode ? mode.name : state.modeKey;
      badge.classList.remove("hidden");
    }
    renderPool();
    renderSlots();
    renderStrategy();
    renderBanner();
    renderPrepareStatus();
    updateSubmitState();
  }

  function renderBanner() {
    const banner = el("last-round-banner");
    const result = state.pendingResult;
    if (!result) {
      banner.classList.add("hidden");
      return;
    }
    const myScore = state.score[state.seat] || 0;
    const opponentScore = state.score[1 - state.seat] || 0;
    const outcome = result.winner_seat === null ? "平局" : result.winner_seat === state.seat ? "你赢下本局" : "本局失利";
    banner.className = "banner " + (result.winner_seat === state.seat ? "win" : result.winner_seat === null ? "" : "lose");
    banner.textContent = `上一局：${outcome}（比分 ${myScore} : ${opponentScore}）— ${result.reason}`;
  }

  function renderPool() {
    const container = el("pool-cards");
    container.innerHTML = "";
    state.pool.forEach((character) => {
      const order = state.selection.indexOf(character.id);
      const card = document.createElement("button");
      card.type = "button";
      card.className = "card" + (order >= 0 ? " is-selected" : "");
      card.addEventListener("click", () => toggleCharacter(character.id));

      const top = document.createElement("div");
      top.className = "card-top";
      const name = document.createElement("span");
      name.className = "card-name";
      name.textContent = character.name;
      top.appendChild(name);
      if (order >= 0) {
        const badge = document.createElement("span");
        badge.className = "order-badge";
        badge.textContent = String(order + 1);
        top.appendChild(badge);
      } else {
        const tag = document.createElement("span");
        tag.className = "tag-badge" + (character.tag === "none" ? " tag-none" : "");
        tag.textContent = character.tag_name;
        top.appendChild(tag);
      }
      card.appendChild(top);

      const stats = document.createElement("div");
      stats.className = "card-stats";
      stats.innerHTML = `<span>ATK ${character.atk}</span><span>HP ${character.hp}</span><span>先手 ${character.initiative}</span>`;
      card.appendChild(stats);

      const lore = document.createElement("div");
      lore.className = "card-lore";
      lore.textContent = character.lore;
      card.appendChild(lore);

      if (character.place_first) {
        const notice = document.createElement("div");
        notice.className = "card-lore";
        notice.style.color = "var(--warn)";
        notice.textContent = "只能放在第 1 位";
        card.appendChild(notice);
      }

      const tags = (character.tags || []).filter((tag) => tag && tag !== "none");
      if (tags.length) {
        const chipRow = document.createElement("div");
        chipRow.className = "tag-row";
        tags.forEach((tag, index) => {
          const chip = document.createElement("span");
          chip.className = "tag-badge";
          chip.textContent = (character.tag_names || [])[index] || tag;
          chipRow.appendChild(chip);
        });
        const tagButton = document.createElement("button");
        tagButton.type = "button";
        tagButton.className = "tag-badge";
        tagButton.textContent = "标签说明";
        tagButton.addEventListener("click", (event) => {
          event.stopPropagation();
          showTagNote(character);
        });
        chipRow.appendChild(tagButton);
        card.appendChild(chipRow);
      }

      container.appendChild(card);
    });
  }

  function showTagNote(character) {
    const note = el("tag-note");
    note.classList.remove("hidden");
    const parts = (character.tags || []).map(
      (tag, index) => `${(character.tag_names || [])[index] || tag}：${(character.tag_summaries || [])[index] || ""}`
    );
    note.textContent = `${character.name} · ${character.tag_name}｜${parts.join("　")}`;
  }

  function toggleCharacter(charId) {
    const index = state.selection.indexOf(charId);
    if (index >= 0) {
      state.selection.splice(index, 1);
      renderPool();
      renderSlots();
      updateSubmitState();
      return;
    }
    if (state.selection.length >= state.teamSize) {
      toast("最多只能选 " + state.teamSize + " 名角色，先取消一个再选", true);
      return;
    }
    const character = state.pool.find((c) => c.id === charId);
    if (character && character.place_first && state.selection.length > 0) {
      toast(character.name + " 只能放在第一个出击位，请先清空阵容", true);
      return;
    }
    state.selection.push(charId);
    renderPool();
    renderSlots();
    updateSubmitState();
  }

  function bonusCountForSlot(slot) {
    return state.bonuses.filter((bonus) => bonus.slot === slot).length;
  }

  function slotStats(character, slot) {
    const atkBonus = state.bonuses.filter((b) => b.slot === slot && b.kind === "atk").length * 2;
    const hpBonus = state.bonuses.filter((b) => b.slot === slot && b.kind === "hp").length * 4;
    return {
      atk: character.atk + atkBonus,
      hp: character.hp + hpBonus,
      atkBonus,
      hpBonus,
    };
  }

  function renderSlots() {
    const container = el("slots");
    container.innerHTML = "";
    for (let index = 0; index < state.teamSize; index += 1) {
      const charId = state.selection[index];
      const character = charId ? state.pool.find((c) => c.id === charId) : null;
      const li = document.createElement("li");
      li.className = "slot" + (character ? " is-filled" : "");

      const head = document.createElement("div");
      head.className = "slot-head";
      const label = document.createElement("span");
      label.className = "slot-index";
      label.textContent = "第 " + (index + 1) + " 位";
      head.appendChild(label);
      if (character) {
        const stats = slotStats(character, index + 1);
        const info = document.createElement("span");
        info.className = "card-stats";
        info.innerHTML = `<strong>${character.name}</strong> · ATK ${stats.atk} · HP ${stats.hp}`;
        head.appendChild(info);
      }
      li.appendChild(head);

      if (!character) {
        const empty = document.createElement("div");
        empty.className = "slot-empty";
        empty.textContent = "点击左侧角色加入这一位";
        li.appendChild(empty);
        container.appendChild(li);
        continue;
      }

      const chips = document.createElement("div");
      chips.className = "slot-actions";
      state.bonuses.forEach((bonus, bonusIndex) => {
        if (bonus.slot !== index + 1) return;
        const chip = document.createElement("span");
        chip.className = "bonus-chip";
        chip.textContent = (bonus.kind === "atk" ? "攻击 +2" : "生命 +4") + " ×";
        chip.title = "点击移除这次增益";
        chip.addEventListener("click", () => {
          state.bonuses.splice(bonusIndex, 1);
          renderSlots();
          updateSubmitState();
        });
        chips.appendChild(chip);
      });
      li.appendChild(chips);

      const actions = document.createElement("div");
      actions.className = "slot-actions";
      actions.appendChild(
        button("+ 攻击", "ghost small", () => addBonus(index + 1, "atk"), state.bonusPerRound <= state.bonuses.length || bonusCountForSlot(index + 1) >= state.maxBonusPerFighter)
      );
      actions.appendChild(
        button("+ 生命", "ghost small", () => addBonus(index + 1, "hp"), state.bonusPerRound <= state.bonuses.length || bonusCountForSlot(index + 1) >= state.maxBonusPerFighter)
      );
      actions.appendChild(button("上移", "ghost small", () => moveSlot(index, -1), index === 0));
      actions.appendChild(button("下移", "ghost small", () => moveSlot(index, 1), index === state.selection.length - 1));
      actions.appendChild(button("移除", "ghost small", () => toggleCharacter(character.id)));
      li.appendChild(actions);

      container.appendChild(li);
    }
    el("bonus-left").textContent = String(Math.max(0, state.bonusPerRound - state.bonuses.length));
  }

  function button(label, className, onClick, disabled) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    node.disabled = Boolean(disabled);
    node.addEventListener("click", onClick);
    return node;
  }

  function addBonus(slot, kind) {
    if (state.bonuses.length >= state.bonusPerRound) {
      toast("增益次数已经用完（共 " + state.bonusPerRound + " 次）", true);
      return;
    }
    if (bonusCountForSlot(slot) >= state.maxBonusPerFighter) {
      toast("每个出击位最多获得 " + state.maxBonusPerFighter + " 次增益", true);
      return;
    }
    state.bonuses.push({ slot: slot, kind: kind });
    renderSlots();
    updateSubmitState();
  }

  function moveSlot(index, direction) {
    const target = index + direction;
    if (target < 0 || target >= state.selection.length) return;
    const moved = state.selection[index];
    const replaced = state.selection[target];
    const movedChar = state.pool.find((c) => c.id === moved);
    const targetChar = state.pool.find((c) => c.id === replaced);
    if (target === 0 && (movedChar.place_first || targetChar.place_first) && !movedChar.place_first) {
      toast(movedChar.name + " 不能放在第一位，" + targetChar.name + " 只能放在第一位", true);
      return;
    }
    if (index === 0 && targetChar.place_first && !movedChar.place_first) {
      toast(targetChar.name + " 只能放在第一位", true);
      return;
    }
    state.selection[index] = replaced;
    state.selection[target] = moved;
    renderPool();
    renderSlots();
  }

  function clearSelection() {
    state.selection = [];
    state.bonuses = [];
    renderPool();
    renderSlots();
    updateSubmitState();
  }

  function renderStrategy() {
    const container = el("strategy-options");
    container.innerHTML = "";
    state.strategies.forEach((strategy) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "strategy";
      input.value = strategy.kind;
      input.checked = state.strategy.kind === strategy.kind;
      input.addEventListener("change", () => {
        state.strategy.kind = strategy.kind;
        if (strategy.kind !== "tag_priority") state.strategy.tag = null;
        renderStrategy();
        updateSubmitState();
      });
      label.appendChild(input);
      const text = document.createElement("span");
      text.textContent = strategy.label;
      label.appendChild(text);
      container.appendChild(label);
    });

    const picker = el("tag-picker");
    const isTagStrategy = state.strategy.kind === "tag_priority";
    picker.classList.toggle("hidden", !isTagStrategy);
    if (isTagStrategy) {
      const select = el("select-tag");
      const tags = [];
      state.pool.forEach((character) => {
        if (character.tag !== "none" && !tags.some((t) => t.key === character.tag)) {
          tags.push({ key: character.tag, name: character.tag_name });
        }
      });
      const previous = state.strategy.tag;
      select.innerHTML = "";
      if (!tags.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "你的角色池里没有带标签的角色";
        select.appendChild(option);
        state.strategy.tag = null;
      } else {
        if (!tags.some((t) => t.key === previous)) state.strategy.tag = tags[0].key;
        tags.forEach((tag) => {
          const option = document.createElement("option");
          option.value = tag.key;
          option.textContent = tag.name;
          option.selected = tag.key === state.strategy.tag;
          select.appendChild(option);
        });
      }
    }
  }

  function planIssues() {
    const issues = [];
    if (state.selection.length !== state.teamSize) {
      issues.push("还需选择 " + (state.teamSize - state.selection.length) + " 名角色");
    }
    if (state.bonuses.length !== state.bonusPerRound) {
      issues.push("还需分配 " + (state.bonusPerRound - state.bonuses.length) + " 次增益");
    }
    if (state.strategy.kind === "tag_priority" && !state.strategy.tag) {
      issues.push("请选择要优先攻击的标签");
    }
    return issues;
  }

  function updateSubmitState() {
    const issues = planIssues();
    el("btn-submit").disabled = issues.length > 0;
    el("submit-hint").textContent = issues.length ? issues.join("；") : "方案已满足全部规则，可以提交";
  }

  function renderPrepareStatus() {
    const node = el("prepare-status");
    if (state.submitted && state.opponentReady) node.textContent = "双方已提交，正在结算…";
    else if (state.submitted) node.textContent = "你已提交，等待对手…";
    else if (state.opponentReady) node.textContent = "对手已提交，等待你";
    else node.textContent = "准备中";
  }

  function submitPlan() {
    const payload = {
      type: "submit_plan",
      selection: state.selection.slice(),
      bonuses: state.bonuses.map((bonus) => ({ slot: bonus.slot, kind: bonus.kind })),
      strategy: { kind: state.strategy.kind, tag: state.strategy.tag },
    };
    if (send(payload)) {
      el("btn-submit").disabled = true;
      el("submit-hint").textContent = "正在提交…";
    }
  }

  function onPlanAccepted(message, auto) {
    state.submitted = true;
    if (auto && message.plan) {
      state.selection = message.plan.selection.slice();
      state.bonuses = message.plan.bonuses.slice();
      state.strategy = { kind: message.plan.strategy.kind, tag: message.plan.strategy.tag };
      renderPrepare();
      toast(message.reason || "已自动提交方案");
    } else {
      toast("方案已提交，仍可在双方提交前修改");
    }
    renderPrepareStatus();
    updateSubmitState();
    el("btn-submit").textContent = "更新方案";
  }

  /* ------------------------------------------------------------ 计时 */
  function startTimer(seconds) {
    stopTimer();
    state.urgentWarned = false;
    el("timer").classList.remove("is-urgent");
    state.deadlineAt = Date.now() + seconds * 1000;
    const total = seconds * 1000;
    const tick = () => {
      const remain = Math.max(0, state.deadlineAt - Date.now());
      el("timer-text").textContent = Math.ceil(remain / 1000) + "s";
      el("timer-fill").style.width = (total ? (remain / total) * 100 : 0) + "%";
      const urgent = remain > 0 && remain <= 10000;
      el("timer").classList.toggle("is-urgent", urgent);
      if (urgent && !state.urgentWarned) {
        state.urgentWarned = true;
        toast("准备时间只剩 10 秒，超时系统会随机提交方案");
      }
      if (remain <= 0) {
        stopTimer();
        el("prepare-status").textContent = "时间到，系统正在自动提交…";
      }
    };
    tick();
    state.timerHandle = setInterval(tick, 250);
  }

  function stopTimer() {
    if (state.timerHandle) {
      clearInterval(state.timerHandle);
      state.timerHandle = null;
    }
  }

  /* ------------------------------------------------------------ 战斗回放 */
  function onBattleReport(message) {
    stopTimer();
    hideOverlay();
    state.score = message.score || state.score;
    const lineups = message.lineups || [];
    const events = (message.result && message.result.events) || [];
    state.lastBattle = {
      roundIndex: message.round_index,
      lines: events.map((event) => ({ round: event.round, kind: event.kind, text: event.text })),
    };
    state.battle = {
      roundIndex: message.round_index,
      events: events,
      index: 0,
      playing: true,
      speed: 1,
      // 回放按「真实时间」推进：即使标签页在后台被浏览器限流（定时器降到 1 秒），
      // 每次回调也会一次性补上应该播到的事件，保证整场回放仍在预算时间内结束。
      startedAt: Date.now(),
      tickMs: Math.max(
        MIN_TICK_MS,
        Math.min(BASE_TICK_MS, Math.round(REPLAY_BUDGET_MS / Math.max(events.length, 1)))
      ),
      timer: null,
      fighters: lineups.map((team) =>
        team.map((fighter) => ({
          uid: fighter.uid,
          name: fighter.name,
          position: fighter.position,
          tags: fighter.tags || [],
          lostTags: fighter.lost_tags || [],
          atk: fighter.atk,
          hp: fighter.hp,
          maxHp: fighter.max_hp,
          alive: true,
          flash: "",
        }))
      ),
      result: message.result,
    };
    el("battle-round").textContent = String(message.round_index);
    el("battle-score").textContent = state.score.join(" : ");
    el("battle-log").innerHTML = "";
    el("log-progress").textContent = "0 / " + state.battle.events.length;
    el("btn-pause").textContent = "暂停";
    el("btn-speed").textContent = "1×";
    el("team-a-title").textContent = "A 队（" + teamLabel(0) + "）";
    el("team-b-title").textContent = "B 队（" + teamLabel(1) + "）";
    if (message.auto_submitted && message.auto_submitted.length) {
      const who = message.auto_submitted.map((seat) => teamLabel(seat)).join("、");
      toast(who + " 超时，已由系统随机提交方案");
    }
    renderBoard();
    show("screen-battle");
    if (state.battle.events.length === 0) {
      finishPlayback();
      return;
    }
    state.battle.timer = setTimeout(scheduleNext, state.battle.tickMs);
  }

  function scheduleNext() {
    const battle = state.battle;
    if (!battle || !battle.playing) return;
    if (battle.index >= battle.events.length) {
      finishPlayback();
      return;
    }
    const step = Math.max(1, (battle.tickMs || BASE_TICK_MS) / battle.speed);
    const target = Math.min(battle.events.length, Math.floor((Date.now() - battle.startedAt) / step) + 1);
    let painted = 0;
    // 上限只是防御性的：一次性补齐即可追上真实时间轴（后台标签页被限流时也能立刻追平）
    while (battle.index < target && painted < 500) {
      applyEvent(battle.events[battle.index]);
      battle.index += 1;
      painted += 1;
    }
    renderBoard();
    el("log-progress").textContent = progressText(battle);
    if (battle.index >= battle.events.length) {
      finishPlayback();
      return;
    }
    battle.timer = setTimeout(scheduleNext, Math.max(30, step));
  }

  function progressText(battle) {
    const base = battle.index + " / " + battle.events.length;
    if (!state.nextRoundDeadline) return base;
    const remain = Math.max(0, Math.round((state.nextRoundDeadline - Date.now()) / 1000));
    return base + " · 下一局剩余 " + remain + "s";
  }

  function applyEvent(event, render) {
    const battle = state.battle;
    if (!battle) return;
    const find = (uid) => {
      for (const team of battle.fighters) {
        const hit = team.find((f) => f.uid === uid);
        if (hit) return hit;
      }
      return null;
    };
    const data = event.data || {};
    switch (event.kind) {
      case "damage":
      case "poison_tick": {
        const target = find(data.target);
        if (target) {
          target.hp = data.hp_after;
          if (target.hp <= 0) target.alive = false;
          target.flash = "is-hurt";
        }
        break;
      }
      case "heal":
      case "revive": {
        const target = find(data.target);
        if (target) {
          target.hp = data.hp_after;
          target.alive = true;
          target.flash = "is-heal";
        }
        break;
      }
      case "death": {
        const target = find(data.target);
        if (target) {
          target.alive = false;
          target.hp = 0;
        }
        break;
      }
      case "curse": {
        const target = find(data.target);
        if (target) {
          target.lostTags = data.lost_tags || [];
          target.tags = [];
        }
        break;
      }
      default:
        break;
    }
    if (render !== false) appendLog(event);
  }

  function appendLog(event) {
    const log = el("battle-log");
    const li = document.createElement("li");
    li.className = "kind-" + event.kind;
    li.textContent = event.text;
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  function renderBoard() {
    const battle = state.battle;
    if (!battle) return;
    [0, 1].forEach((team) => {
      const container = el(team === 0 ? "team-a" : "team-b");
      container.innerHTML = "";
      (battle.fighters[team] || []).forEach((fighter) => {
        const node = document.createElement("div");
        const low = fighter.hp <= fighter.maxHp * 0.3;
        node.className = "fighter " + (fighter.alive ? "" : "is-dead ") + (low ? "low " : "") + (fighter.flash || "");
        const row = document.createElement("div");
        row.className = "fighter-row";
        const name = document.createElement("span");
        name.className = "fighter-name";
        name.textContent = fighter.position + " " + fighter.name;
        row.appendChild(name);
        const hpText = document.createElement("span");
        hpText.textContent = fighter.hp + "/" + fighter.maxHp;
        row.appendChild(hpText);
        node.appendChild(row);
        const tags = fighter.tags.concat(fighter.lostTags.map((tag) => tag + "(被剥夺)"));
        if (tags.length) {
          const tagRow = document.createElement("div");
          tagRow.className = "fighter-tags";
          tagRow.textContent = tags.join(" / ");
          node.appendChild(tagRow);
        }
        const bar = document.createElement("div");
        bar.className = "hp";
        const fill = document.createElement("i");
        fill.style.width = Math.max(0, (fighter.hp / fighter.maxHp) * 100) + "%";
        bar.appendChild(fill);
        node.appendChild(bar);
        container.appendChild(node);
        fighter.flash = "";
      });
    });
  }

  function finishPlayback() {
    const battle = state.battle;
    if (!battle) return;
    battle.playing = false;
    if (battle.timer) {
      clearTimeout(battle.timer);
      battle.timer = null;
    }
    el("btn-pause").textContent = "已结束";
    el("log-progress").textContent = "播放完成";
    if (state.pendingGameOver) {
      showGameOverOverlay(state.pendingGameOver);
    } else if (state.pendingResult) {
      showResultOverlay(state.pendingResult);
    }
  }

  function stopPlayback() {
    if (state.battle && state.battle.timer) {
      clearTimeout(state.battle.timer);
      state.battle.timer = null;
    }
    if (state.battle) state.battle.playing = false;
  }

  function togglePause() {
    const battle = state.battle;
    if (!battle) return;
    if (battle.playing) {
      battle.playing = false;
      clearTimeout(battle.timer);
      battle.timer = null;
      el("btn-pause").textContent = "继续";
    } else {
      if (battle.index >= battle.events.length) return;
      battle.playing = true;
      el("btn-pause").textContent = "暂停";
      // 继续播放时重新对齐时间轴，避免暂停期间的时间被算进去
      battle.startedAt = Date.now() - battle.index * ((battle.tickMs || BASE_TICK_MS) / battle.speed);
      battle.timer = setTimeout(scheduleNext, 120);
    }
  }

  function cycleSpeed() {
    const battle = state.battle;
    if (!battle) return;
    const next = (PLAY_SPEEDS.indexOf(battle.speed) + 1) % PLAY_SPEEDS.length;
    battle.speed = PLAY_SPEEDS[next];
    battle.startedAt = Date.now() - battle.index * ((battle.tickMs || BASE_TICK_MS) / battle.speed);
    el("btn-speed").textContent = battle.speed + "×";
  }

  function skipToEnd() {
    const battle = state.battle;
    if (!battle) return;
    if (battle.timer) {
      clearTimeout(battle.timer);
      battle.timer = null;
    }
    while (battle.index < battle.events.length) {
      applyEvent(battle.events[battle.index]);
      battle.index += 1;
    }
    renderBoard();
    finishPlayback();
  }

  /* ------------------------------------------------------------ 结算 */
  function onRoundResult(message) {
    state.pendingResult = message;
    state.score = message.score || state.score;
    el("battle-score").textContent = state.score.join(" : ");
    if (message.replay) {
      toast("本局双方同归于尽，重新开一局");
    }
    const battleFinished = !state.battle || !state.battle.playing;
    if (battleFinished && !message.match_over) {
      showResultOverlay(message);
    }
  }

  function showResultOverlay(result) {
    const myScore = state.score[state.seat] || 0;
    const opponentScore = state.score[1 - state.seat] || 0;
    const title = result.winner_seat === null ? "本局平局" : result.winner_seat === state.seat ? "你赢下本局" : "本局失利";
    const remain = state.nextRoundDeadline
      ? Math.max(0, Math.round((state.nextRoundDeadline - Date.now()) / 1000))
      : 0;
    const hint = state.nextRound
      ? `下一局已经开始计时，准备时间还剩约 ${remain} 秒。`
      : "下一局马上开始，新的角色池会重新发到手上。";
    showOverlay(
      `<h2>${title}</h2>
       <div class="score-big">${myScore} : ${opponentScore}</div>
       <p>${result.reason || ""}${result.rounds ? "，共 " + result.rounds + " 回合" : ""}</p>
       <p class="hint">${hint}</p>`
    );
    clearTimeout(state.resultTimer);
    state.resultTimer = setTimeout(() => {
      if (state.nextRound) applyNextRound();
    }, 1800);
  }

  function onGameOver(message) {
    // 最后一局也要让玩家看完战斗回放，回放结束后再弹最终结算
    if (state.battle && state.battle.playing) {
      state.pendingGameOver = message;
      return;
    }
    showGameOverOverlay(message);
  }

  function showGameOverOverlay(message) {
    state.pendingGameOver = null;
    // 整场已经结束：清掉「本局结果」相关状态，避免下一场开始时被旧的 pendingResult 拦住
    state.pendingResult = null;
    state.nextRound = null;
    state.nextRoundDeadline = 0;
    stopPlayback();
    state.score = message.score || state.score;
    const win = message.winner_seat === state.seat;
    const myScore = state.score[state.seat] || 0;
    const opponentScore = state.score[1 - state.seat] || 0;
    recordMatch(win, myScore, opponentScore);
    const history = (message.history || [])
      .map((item) => {
        const label = item.winner_seat === null ? "平局" : item.winner_seat === state.seat ? "胜" : "负";
        return `<li><span>第 ${item.round_index} 局 · ${label}</span><span>${item.rounds} 回合</span></li>`;
      })
      .join("");
    showOverlay(
      `<h2>${win ? "🏆 你赢下了整场对局" : "对局结束"}</h2>
       <div class="score-big">${myScore} : ${opponentScore}</div>
       <p>${win ? "星核为你亮起，归寂潮退去。" : "对手的部署更胜一筹，再来一局试试别的思路。"}</p>
       <ul class="history">${history}</ul>
       <div class="overlay-actions">
         <button class="primary" id="btn-rematch">再来一局</button>
         <button class="ghost" id="btn-back-lobby">返回大厅</button>
       </div>`
    );
    el("btn-rematch").addEventListener("click", () => {
      if (send({ type: "rematch" })) {
        el("btn-rematch").disabled = true;
        el("btn-rematch").textContent = "已请求，等待对手…";
      }
    });
    el("btn-back-lobby").addEventListener("click", () => {
      send({ type: "leave_room" });
      resetToLobby("已离开房间");
    });
  }

  function onRoomClosed(message) {
    stopTimer();
    stopPlayback();
    showOverlay(
      `<h2>房间已关闭</h2>
       <p>${message.reason || "对手已离开房间。"}</p>
       <div class="overlay-actions"><button class="primary" id="btn-closed-back">返回大厅</button></div>`
    );
    el("btn-closed-back").addEventListener("click", () => resetToLobby());
  }

  /* ------------------------------------------------------------ 规则页 */
  async function loadRules() {
    if (state.rules || state.rulesLoading) return;
    state.rulesLoading = true;
    try {
      const response = await fetch("/api/rules");
      state.rules = await response.json();
      state.modes = state.rules.modes || [];
      if (state.modes.length && !state.modes.some((mode) => mode.key === state.modeKey)) {
        state.modeKey = state.modes[0].key;
      }
      renderModes();
      renderRules();
    } catch (err) {
      el("rules-content").innerHTML = "<p class='hint'>规则加载失败，请刷新页面重试。</p>";
    } finally {
      state.rulesLoading = false;
    }
  }

  function renderRules() {
    const rules = state.rules;
    if (!rules) return;
    const c = rules.constants;
    const characterRows = rules.characters
      .map(
        (ch) =>
          `<tr><td>${ch.id}</td><td><strong>${ch.name}</strong></td><td>${ch.role}</td><td>${ch.atk}</td><td>${ch.hp}</td><td>${ch.initiative}</td><td>${ch.tag_name}</td><td>${ch.domain}</td></tr>`
      )
      .join("");
    const tagRows = rules.tags
      .filter((tag) => tag.key !== "none")
      .map((tag) => `<tr><td><strong>${tag.name}</strong></td><td>${tag.detail}</td></tr>`)
      .join("");
    const strategies = rules.strategies.map((item) => `<li>${item.label}</li>`).join("");
    const modeRows = (rules.modes || [])
      .map(
        (mode) =>
          `<tr><td><strong>${mode.name}</strong></td><td>${mode.summary}</td><td>${mode.detail}</td></tr>`
      )
      .join("");

    el("rules-content").innerHTML = `
      <h3>游戏模式</h3>
      <table><thead><tr><th>模式</th><th>一句话</th><th>说明</th></tr></thead><tbody>${modeRows}</tbody></table>

      <h3>一局怎么打</h3>
      <ul>
        <li>每局双方各自获得 ${c.pool_size} 名随机角色构成的角色池，从中选出 ${c.team_size} 名出战。</li>
        <li>选择顺序就是出击顺序，战斗时按 A1 → B1 → A2 → B2 → A3 → B3 依次行动。</li>
        <li>每人有 ${c.bonus_per_round} 次增益机会：攻击 +${c.bonus_atk} 或生命 +${c.bonus_hp}，单个出击位最多 ${c.max_bonus_per_fighter} 次。</li>
        <li>双方提交后自动演算，先赢下 ${c.rounds_to_win} 局的一方获得整场胜利。</li>
        <li>准备阶段限时 ${c.prepare_timeout} 秒，超时由系统随机提交。</li>
      </ul>

      <h3>先手与目标</h3>
      <ul>
        <li>双方出战角色的先手值总和较低的一方先手；相同则随机决定。</li>
        <li>攻击策略可选：<ol>${strategies}</ol></li>
      </ul>

      <h3>结算顺序</h3>
      <ol>
        <li>开战：诅咒巫师剥夺敌方一名角色的标签 → 判定先手</li>
        <li>每轮开始：结算全场中毒伤害</li>
        <li>按出击位依次行动，先手方在同一位次中先出手</li>
        <li>单次攻击：攻击方修正（狂暴）→ 自爆判定 → 护盾分担 → 重装减伤 → 扣血 → 命中效果（中毒、穿透）→ 自爆反噬</li>
        <li>任一方全灭立即结束；单局最多 ${c.max_rounds_per_duel} 回合，之后按剩余总血量判定</li>
      </ol>

      <h3>标签效果</h3>
      <table><thead><tr><th>标签</th><th>效果</th></tr></thead><tbody>${tagRows}</tbody></table>

      <h3>角色一览</h3>
      <table>
        <thead><tr><th>#</th><th>角色</th><th>定位</th><th>ATK</th><th>HP</th><th>先手</th><th>标签</th><th>星域</th></tr></thead>
        <tbody>${characterRows}</tbody>
      </table>
    `;
  }

  function openRules() {
    const active = document.querySelector(".screen.is-active");
    state.previousScreen = active ? active.id : "screen-lobby";
    loadRules();
    show("screen-rules");
  }

  /* ------------------------------------------------------------ 事件绑定 */
  function bindEvents() {
    el("btn-create").addEventListener("click", () => {
      const name = currentNickname();
      if (!name) {
        el("lobby-hint").textContent = "请先填写昵称，对手能看到它。";
        el("input-name").focus();
        return;
      }
      el("lobby-hint").textContent = "";
      rememberInputs();
      send({ type: "create_room", name: name, mode: state.modeKey });
    });

    el("btn-random").addEventListener("click", () => {
      const name = currentNickname();
      if (!name) {
        el("lobby-hint").textContent = "请先填写昵称，对手能看到它。";
        el("input-name").focus();
        return;
      }
      el("lobby-hint").textContent = "";
      rememberInputs();
      send({ type: "join_random", name: name, mode: state.modeKey });
    });

    el("btn-cancel-match").addEventListener("click", () => send({ type: "cancel_matchmaking" }));

    el("btn-join").addEventListener("click", () => {
      const name = currentNickname();
      const code = el("input-code").value.trim().toUpperCase();
      if (!name) {
        el("lobby-hint").textContent = "请先填写昵称，对手能看到它。";
        el("input-name").focus();
        return;
      }
      if (!code) {
        el("lobby-hint").textContent = "请输入 4 位房间号。";
        el("input-code").focus();
        return;
      }
      el("lobby-hint").textContent = "";
      rememberInputs();
      send({ type: "join_room", name: name, room_code: code });
    });

    el("input-name").addEventListener("keydown", (event) => {
      if (event.key === "Enter") el("btn-create").click();
    });
    el("input-code").addEventListener("keydown", (event) => {
      if (event.key === "Enter") el("btn-join").click();
    });

    el("btn-copy").addEventListener("click", copyRoomCode);
    el("btn-copy-link").addEventListener("click", copyInviteLink);
    el("btn-review").addEventListener("click", showBattleReview);
    el("btn-leave-waiting").addEventListener("click", () => {
      send({ type: "leave_room" });
      resetToLobby("已离开房间");
    });
    el("btn-clear").addEventListener("click", clearSelection);
    el("btn-submit").addEventListener("click", submitPlan);
    el("btn-pause").addEventListener("click", togglePause);
    el("btn-speed").addEventListener("click", cycleSpeed);
    el("btn-skip").addEventListener("click", skipToEnd);
    el("btn-rules").addEventListener("click", openRules);
    el("btn-rules-back").addEventListener("click", () => show(state.previousScreen));
    el("select-tag").addEventListener("change", (event) => {
      state.strategy.tag = event.target.value;
      updateSubmitState();
    });

    // 回车提交：在备战页且方案合法时，直接触发提交（输入框/下拉里不拦截）
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      const tag = (event.target && event.target.tagName) || "";
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      const inPrepare = document.querySelector("#screen-prepare.is-active");
      const submit = el("btn-submit");
      if (inPrepare && submit && !submit.disabled) submit.click();
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.timerHandle) {
        /* 回到标签页时立刻刷新倒计时，避免后台节流造成的偏差 */
        el("timer-text").textContent = Math.ceil(Math.max(0, state.deadlineAt - Date.now()) / 1000) + "s";
      }
      // 回到标签页时也让战斗回放立刻追上时间轴
      if (document.visibilityState === "visible" && state.battle && state.battle.playing) {
        scheduleNext();
      }
    });
  }

  function main() {
    // 旧版本把会话写在 localStorage 里（会被同一浏览器的多个标签页互相覆盖），这里清掉
    try {
      window.localStorage.removeItem(SESSION_KEY);
    } catch (err) {
      /* 忽略 */
    }
    restoreInputs();
    restoreMode();
    bindEvents();
    connect();
    loadRules();
    renderHistory();
    applyInviteLink();
    fetch("/api/version")
      .then((response) => response.json())
      .then((data) => {
        el("brand-version").textContent = data.name + " v" + data.version;
      })
      .catch(() => {});
    loadRules().then(() => {
      if (state.rules && state.rules.constants) {
        el("howto-timeout").textContent = String(state.rules.constants.prepare_timeout);
      }
    });
    setInterval(() => {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) send({ type: "ping" });
    }, 25000);
  }

  function applyInviteLink() {
    const invited = (new URLSearchParams(location.search).get("room") || "").trim().toUpperCase();
    if (!invited) return;
    el("input-code").value = invited;
    el("lobby-hint").textContent = `已填入邀请的房间号 ${invited}，填好昵称后点「加入房间」即可。`;
    el("input-name").focus();
  }

  document.addEventListener("DOMContentLoaded", main);
})();
