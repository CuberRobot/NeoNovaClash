/* 星陨竞技场 · 核心层
   共享状态、通用工具、会话存储、界面偏好。
   五个脚本按顺序加载：core → audio → prepare → battle → app，全部挂在 window.NC 上（无构建步骤）。 */
(function () {
  "use strict";

  const NC = (window.NC = window.NC || {});

  NC.ASSET_BASE = "/static/assets";
  NC.MAX_NAME = 12;
  NC.HISTORY_KEY = "nc_history";
  NC.HISTORY_LIMIT = 5;
  NC.RECONNECT_LIMIT = 5;
  NC.SESSION_KEY = "nc_session";
  NC.MODE_KEY = "nc_mode";
  NC.VIEW_KEY = "nc_view"; // 备战页视图：cards（默认）| buttons
  NC.SOUND_KEY = "nc_sound"; // 音效开关：on | off
  NC.VIEW_CARDS = "cards";
  NC.VIEW_BUTTONS = "buttons";

  NC.state = {
    ws: null,
    connected: false,
    reconnectTimer: null,
    reconnectAttempts: 0,
    name: "",
    roomCode: null,
    seat: null,
    roundIndex: 0,
    score: [0, 0],
    // 备战
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
    undoStack: [],
    armedBonus: null,
    swapFrom: null,
    view: NC.VIEW_CARDS,
    // 战斗
    battle: null,
    lastBattle: null,
    pendingResult: null,
    lastRoundResult: null, // 已进入下一局时，仍然在顶部横幅里展示的上一局结果
    pendingGameOver: null,
    nextRound: null,
    nextRoundDeadline: 0,
    resultTimer: null,
    // 其他
    modes: [],
    lobbyMode: "standard",
    roomMode: null,
    randomTags: false,
    rules: null,
    rulesLoading: false,
    previousScreen: "screen-lobby",
    overlayActive: false,
    opponentDisconnected: false,
  };

  NC.el = (id) => document.getElementById(id);
  NC.teamLabel = (team) => (team === NC.state.seat ? "你" : "对手");

  // ---------------------------------------------------------------- 通用 UI
  NC.show = function show(screenId) {
    document.querySelectorAll(".screen").forEach((node) => {
      node.classList.toggle("is-active", node.id === screenId);
    });
  };

  NC.setConn = function setConn(kind, text) {
    const node = NC.el("conn-status");
    if (!node) return;
    node.textContent = text;
    node.className = "pill " + (kind === "ok" ? "pill-ok" : kind === "warn" ? "pill-warn" : "pill-bad");
  };

  let toastTimer = null;
  NC.toast = function toast(message, isError) {
    const node = NC.el("toast");
    node.textContent = message;
    node.className = "toast is-visible" + (isError ? " is-error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.className = "toast" + (isError ? " is-error" : "");
    }, 3200);
  };

  NC.showOverlay = function showOverlay(html) {
    NC.el("overlay-card").innerHTML = html;
    NC.el("overlay").classList.remove("hidden");
    NC.state.overlayActive = true;
  };

  NC.hideOverlay = function hideOverlay() {
    NC.el("overlay").classList.add("hidden");
    NC.state.overlayActive = false;
  };

  NC.send = function send(payload) {
    const ws = NC.state.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      NC.toast("尚未连接到服务器，请稍后重试", true);
      return false;
    }
    ws.send(JSON.stringify(payload));
    return true;
  };

  NC.escapeHtml = function escapeHtml(text) {
    return String(text).replace(
      /[&<>"']/g,
      (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])
    );
  };

  NC.button = function button(label, className, onClick, disabled) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    node.disabled = Boolean(disabled);
    node.addEventListener("click", onClick);
    return node;
  };

  NC.copyText = function copyText(text, okMessage) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => NC.toast(okMessage),
        () => NC.toast("复制失败，请手动复制：" + text, true)
      );
    } else {
      NC.toast("请手动复制：" + text);
    }
  };

  // ---------------------------------------------------------------- 本地存储
  NC.store = {
    get(key, fallback) {
      try {
        const raw = window.localStorage.getItem(key);
        return raw === null ? fallback : raw;
      } catch (err) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        window.localStorage.setItem(key, value);
      } catch (err) {
        /* 隐私模式忽略 */
      }
    },
  };

  /** 会话必须放在 sessionStorage：localStorage 会被同源的所有标签页共享，
   *  双开对战时后加入的标签页会覆盖前一个的 token，刷新后会重连到对手的座位。 */
  function sessionStore() {
    try {
      return window.sessionStorage;
    } catch (err) {
      return null;
    }
  }

  NC.saveSession = function saveSession(roomCode, token) {
    const store = sessionStore();
    if (!store) return;
    try {
      store.setItem(NC.SESSION_KEY, JSON.stringify({ roomCode: roomCode, token: token }));
    } catch (err) {
      /* 忽略 */
    }
  };

  NC.readSession = function readSession() {
    const store = sessionStore();
    if (!store) return null;
    try {
      const raw = JSON.parse(store.getItem(NC.SESSION_KEY) || "null");
      return raw && raw.roomCode && raw.token ? raw : null;
    } catch (err) {
      return null;
    }
  };

  NC.clearSession = function clearSession() {
    const store = sessionStore();
    try {
      if (store) store.removeItem(NC.SESSION_KEY);
    } catch (err) {
      /* 忽略 */
    }
  };

  // ---------------------------------------------------------------- 最近战绩
  NC.readHistory = function readHistory() {
    try {
      const list = JSON.parse(window.localStorage.getItem(NC.HISTORY_KEY) || "[]");
      return Array.isArray(list) ? list : [];
    } catch (err) {
      return [];
    }
  };

  NC.recordMatch = function recordMatch(win, myScore, opponentScore) {
    const list = NC.readHistory();
    list.unshift({ at: Date.now(), win: win, my: myScore, opp: opponentScore });
    NC.store.set(NC.HISTORY_KEY, JSON.stringify(list.slice(0, NC.HISTORY_LIMIT)));
    NC.renderHistory();
  };

  NC.renderHistory = function renderHistory() {
    const box = NC.el("lobby-history");
    const list = NC.el("recent-history");
    if (!box || !list) return;
    const items = NC.readHistory();
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
  };

  // ---------------------------------------------------------------- 棋子素材
  NC.pieceUrl = function pieceUrl(charId) {
    return `${NC.ASSET_BASE}/pieces/piece-${String(charId).padStart(2, "0")}.svg`;
  };

  NC.tagIconUrl = function tagIconUrl(tagKey) {
    return `${NC.ASSET_BASE}/tags/tag-${tagKey}.svg`;
  };

  /** 生成一个棋子元素（img），供备战页与战斗舞台共用。 */
  NC.pieceImage = function pieceImage(charId, name, className) {
    const img = document.createElement("img");
    img.src = NC.pieceUrl(charId);
    img.alt = name || "棋子";
    img.className = className || "piece";
    img.draggable = false;
    return img;
  };
})();
