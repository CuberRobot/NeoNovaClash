/* 星陨竞技场 · 应用层
   负责大厅、连接、房间流程与消息分发；
   备战交给 prepare.js，战斗回放交给 battle.js。
   加载顺序：core → audio → prepare → battle → app。 */
(function () {
  "use strict";

  const NC = window.NC;
  const state = NC.state;
  const el = NC.el;
  const toast = NC.toast;
  const show = NC.show;
  const send = NC.send;

  /* ------------------------------------------------------------ 昵称与本地偏好 */
  function currentNickname() {
    const value = el("input-name").value.trim();
    return value.slice(0, NC.MAX_NAME);
  }

  function rememberInputs() {
    NC.store.set("nc_name", currentNickname());
    NC.store.set("nc_code", el("input-code").value.trim().toUpperCase());
  }

  function restoreInputs() {
    const name = NC.store.get("nc_name", "");
    const code = NC.store.get("nc_code", "");
    if (name) el("input-name").value = name;
    if (code) el("input-code").value = code;
  }

  /* ------------------------------------------------------------ 游戏模式 */
  function renderModes() {
    const box = el("mode-options");
    if (!state.modes.length) return;
    box.innerHTML = "";
    state.modes.forEach((mode) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mode-option" + (mode.key === state.lobbyMode ? " is-active" : "");
      const title = document.createElement("strong");
      title.textContent = mode.name;
      const summary = document.createElement("span");
      summary.textContent = mode.summary;
      button.append(title, summary);
      button.addEventListener("click", () => {
        if (state.lobbyMode !== mode.key) NC.audio.play("ui-select");
        state.lobbyMode = mode.key;
        NC.store.set(NC.MODE_KEY, mode.key);
        renderModes();
      });
      box.appendChild(button);
    });
  }

  function restoreMode() {
    const saved = NC.store.get(NC.MODE_KEY, "");
    if (saved) state.lobbyMode = saved;
  }

  function applyModeFromMessage(message) {
    if (!message.mode) return;
    state.roomMode = message.mode;
    state.randomTags = Boolean(message.random_tags);
    const badge = el("mode-badge");
    badge.textContent = message.mode_name || message.mode;
    badge.classList.remove("hidden");
    const waitingMode = el("waiting-mode");
    if (waitingMode) {
      waitingMode.textContent =
        `本房间模式：${message.mode_name || message.mode}` +
        (message.random_tags ? "（标签为本局随机分配）" : "");
    }
    document.body.dataset.mode = message.mode;
  }

  /* ------------------------------------------------------------ 流程切换 */
  function resetToLobby(message) {
    NC.prepare.stopTimer();
    NC.battle.reset();
    NC.clearSession();
    state.roomCode = null;
    state.seat = null;
    state.pool = [];
    state.selection = [];
    state.bonuses = [];
    state.submitted = false;
    state.opponentReady = false;
    state.opponentDisconnected = false;
    state.battle = null;
    state.pendingResult = null;
    state.pendingGameOver = null;
    state.nextRound = null;
    state.nextRoundDeadline = 0;
    state.lastRoundResult = null;
    clearTimeout(state.resultTimer);
    state.resultTimer = null;
    NC.hideOverlay();
    el("matching").classList.add("hidden");
    unlockLobbyActions();
    el("timer").classList.remove("is-urgent");
    show("screen-lobby");
    if (message) toast(message);
  }

  /* ------------------------------------------------------------ WebSocket */
  function connect() {
    if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const scheme = location.protocol === "https:" ? "wss://" : "ws://";
    NC.setConn("warn", "连接中…");
    const ws = new WebSocket(scheme + location.host + "/ws");
    state.ws = ws;

    ws.onopen = () => {
      state.connected = true;
      state.reconnectAttempts = 0;
      NC.setConn("ok", "已连接");
      const session = NC.readSession();
      if (session) {
        // 带着上次的房间凭据回来：优先恢复原来的对局
        NC.setConn("warn", "正在恢复对局…");
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
      NC.setConn("bad", "已断开");
      if (!state.overlayActive) {
        if (state.reconnectAttempts < NC.RECONNECT_LIMIT) {
          state.reconnectAttempts += 1;
          const delay = Math.min(8000, 500 * 2 ** state.reconnectAttempts);
          NC.setConn("warn", `重连中…（${state.reconnectAttempts}/${NC.RECONNECT_LIMIT}）`);
          clearTimeout(state.reconnectTimer);
          state.reconnectTimer = setTimeout(connect, delay);
          return;
        }
        NC.showOverlay(
          `<h2>与服务器断开连接</h2>
           <p>多次自动重连都没有成功。检查网络后刷新页面即可重新开始。</p>
           <div class="overlay-actions"><button class="primary" onclick="location.reload()">刷新重连</button></div>`
        );
      }
    };

    ws.onerror = () => NC.setConn("bad", "连接异常");
  }

  function handleMessage(message) {
    switch (message.type) {
      case "hello":
        el("brand-version").textContent = message.server + " v" + message.version;
        break;
      case "room_joined":
        unlockLobbyActions();
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
        el("matching").classList.add("hidden");
        unlockLobbyActions();
        toast(message.reason || "已取消匹配");
        break;
      case "opponent_disconnected":
        state.opponentDisconnected = true;
        showOpponentBanner(`${message.name} 掉线了，正在等待重连（最多 ${message.grace_seconds} 秒）`);
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
        NC.prepare.onPlanAccepted(message, false);
        break;
      case "pool_updated":
        // 补卡生效：角色池多一张，后面就能 7 选 3 / 8 选 3
        NC.prepare.onPoolUpdated(message);
        break;
      case "plan_auto_submitted":
        NC.prepare.onPlanAccepted(message, true);
        break;
      case "opponent_ready":
        state.opponentReady = true;
        NC.prepare.render();
        toast("对手已提交方案");
        break;
      case "prepare_started":
        // 双方都看完回放了，本局的倒计时现在才真正开始
        NC.prepare.startTimer(message.deadline_seconds || 90);
        state.nextRoundDeadline = Date.now() + (message.deadline_seconds || 90) * 1000;
        toast("回放已看完，开始计时");
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
        state.lastRoundResult = null;
        state.pendingGameOver = null;
        state.nextRound = null;
        state.nextRoundDeadline = 0;
        state.battle = null;
        NC.hideOverlay();
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
        unlockLobbyActions();
        toast(message.message, true);
        if (message.fatal) resetToLobby();
        break;
      default:
        break;
    }
  }

  /* ------------------------------------------------------------ 大厅与等待 */
  function onRoomJoined(message) {
    state.roomCode = message.room_code;
    state.seat = message.seat;
    state.score = message.score || [0, 0];
    rememberPlayers(message.players);
    state.opponentDisconnected = false;
    el("matching").classList.add("hidden");
    hideOpponentBanner();
    if (message.token) NC.saveSession(message.room_code, message.token);
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
    rememberPlayers(message.players);
    NC.saveSession(message.room_code, message.token);
    NC.setConn("ok", "已连接");
    NC.prepare.stopTimer();
    NC.battle.reset();
    state.battle = null;
    state.pendingResult = null;
    state.pendingGameOver = null;
    state.nextRound = null;
    state.nextRoundDeadline = 0;
    state.submitted = Boolean(message.submitted);
    state.opponentReady = Boolean(message.opponent_ready);
    applyModeFromMessage(message);

    if (message.phase === "preparing" && message.pool) {
      NC.hideOverlay();
      NC.prepare.restoreFromSync(message);
      // 重连后没有回放要看了，直接替玩家确认，让倒计时正常开始
      if (message.awaiting_replay) send({ type: "replay_done" });
      toast("已回到原来的房间，继续你的部署");
      return;
    }

    // 整场已经结束：把结算面板重新弹出来，而不是把玩家丢回大厅
    if (message.phase === "finished" && message.final) {
      NC.hideOverlay();
      state.score = message.final.score || state.score;
      showGameOverOverlay(message.final, { record: false, restored: true });
      return;
    }

    if (message.phase === "waiting") {
      NC.hideOverlay();
      el("room-code").textContent = message.room_code;
      renderWaitingPlayers(message.players || []);
      show("screen-waiting");
      toast("已回到原来的房间，等待对手加入");
      return;
    }

    // 对局已经结束或已关闭：回大厅重新开始
    NC.clearSession();
    resetToLobby("上一场对局已经结束，请重新创建或加入房间");
  }

  function renderWaitingPlayers(players) {
    rememberPlayers(players);
    const list = el("waiting-players");
    list.innerHTML = "";
    for (let seat = 0; seat < 2; seat += 1) {
      const player = players.find((item) => item.seat === seat);
      const li = document.createElement("li");
      const nameNode = document.createElement("b");
      const status = document.createElement("span");
      if (player) {
        const suffix = seat === state.seat ? "（你）" : player.is_bot ? "（电脑）" : "";
        nameNode.textContent = player.name + suffix;
        status.textContent = "已就位";
      } else {
        nameNode.textContent = "等待对手加入…";
        nameNode.style.color = "var(--text-dim)";
        status.textContent = "空位";
      }
      li.append(nameNode, status);
      list.appendChild(li);
    }
  }

  /** 记住对手是谁（含"是不是电脑"），备战页与战场都要显示。 */
  function rememberPlayers(players) {
    if (!Array.isArray(players) || state.seat === null) return;
    const opponent = players.find((item) => item.seat !== state.seat);
    if (!opponent) return;
    state.opponentName = opponent.name || "";
    state.opponentIsBot = Boolean(opponent.is_bot);
    NC.prepare.renderOpponent();
  }

  function copyRoomCode() {
    const code = state.roomCode || "";
    if (!code) return;
    NC.copyText(code, "房间号已复制：" + code);
  }

  function copyInviteLink() {
    const code = state.roomCode || "";
    if (!code) return;
    const link = `${location.origin}${location.pathname}?room=${code}`;
    NC.copyText(link, "邀请链接已复制：" + link);
  }

  function applyInviteLink() {
    const invited = (new URLSearchParams(location.search).get("room") || "").trim().toUpperCase();
    if (!invited) return;
    el("input-code").value = invited;
    el("lobby-hint").textContent = `已填入邀请的房间号 ${invited}，填好昵称后点「加入房间」即可。`;
    el("input-name").focus();
  }

  /* ------------------------------------------------------------ 回合流程 */
  function onRoundStart(message) {
    NC.prepare.stopTimer();
    state.nextRound = message;
    // 等回放的这一局还没开始计时，先别给玩家一个假倒计时
    state.nextRoundDeadline = message.awaiting_replay
      ? 0
      : Date.now() + (message.deadline_seconds || 90) * 1000;
    // 上一局的回放还没放完：先让玩家看完，播完再由 hooks.onFinished 接上这一局
    if (state.battle && !NC.battle.isFinished()) {
      if (!NC.battle.isPlaying()) NC.battle.togglePause();
      return;
    }
    if (state.pendingResult && !state.overlayActive) {
      showResultOverlay(state.pendingResult);
      return;
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
    NC.hideOverlay();
    applyModeFromMessage(message);
    rememberPlayers(message.players);
    NC.prepare.enterRound(message);
    // 这一局的倒计时要等"回放看完"的确认：刚重连/没拿到战报的客户端直接确认
    if (message.awaiting_replay) send({ type: "replay_done" });
  }

  /* ------------------------------------------------------------ 战斗回放 */
  function onBattleReport(message) {
    NC.prepare.stopTimer();
    NC.battle.onReport(message);
  }

  /** 回放自然结束（或玩家跳到结果）后的衔接。 */
  function onReplayFinished(info) {
    if (info && info.replay) {
      // 重播是玩家主动回看，不该顶掉正在进行的下一局
      NC.showOverlay(
        `<h2>重播结束</h2>
         <p class="hint">你可以继续留在战场翻看战报，或者回到备战页准备下一局。</p>
         <div class="overlay-actions">
           <button class="primary" id="btn-replay-back" type="button">回到备战</button>
           <button class="ghost" id="btn-replay-stay" type="button">留在战场</button>
         </div>`
      );
      el("btn-replay-back").addEventListener("click", () => {
        NC.hideOverlay();
        if (state.nextRound) applyNextRound();
        else {
          NC.prepare.render();
          show("screen-prepare");
        }
      });
      el("btn-replay-stay").addEventListener("click", () => {
        NC.hideOverlay();
        NC.battle.toggleLog(true);
      });
      return;
    }
    if (state.pendingGameOver) {
      showGameOverOverlay(state.pendingGameOver);
      return;
    }
    if (state.pendingResult) {
      showResultOverlay(state.pendingResult);
      return;
    }
    if (state.nextRound) applyNextRound();
  }

  /* ------------------------------------------------------------ 结算 */
  function onRoundResult(message) {
    state.pendingResult = message;
    // 记住最近一局的完整结果：顶部横幅要用它自己的比分，而不是后来变动的当前比分
    state.lastRoundResult = message;
    state.score = message.score || state.score;
    el("battle-score").textContent = state.score.join(" : ");
    if (message.replay) toast("本局双方同归于尽，重新开一局");
    const finished = !state.battle || NC.battle.isFinished();
    if (finished && !message.match_over) showResultOverlay(message);
  }

  function showResultOverlay(result) {
    const myScore = state.score[state.seat] || 0;
    const opponentScore = state.score[1 - state.seat] || 0;
    const title = result.winner_seat === null ? "本局平局" : result.winner_seat === state.seat ? "你赢下本局" : "本局失利";
    const remain = state.nextRoundDeadline
      ? Math.max(0, Math.round((state.nextRoundDeadline - Date.now()) / 1000))
      : 0;
    const hint = state.nextRound
      ? state.nextRound.awaiting_replay
        ? "下一局的倒计时会在回放播完后开始，先看演算不吃准备时间。"
        : `下一局已经开始计时，准备时间还剩约 ${remain} 秒。`
      : "下一局马上开始，角色池不变，换一套打法试试。";
    NC.audio.play(result.winner_seat === state.seat ? "victory" : result.winner_seat === null ? "ui-round" : "defeat", {
      volume: 0.5,
    });
    NC.showOverlay(
      `<h2>${title}</h2>
       <div class="score-big">${myScore} : ${opponentScore}</div>
       <p>${NC.escapeHtml(result.reason || "")}${result.rounds ? "，共 " + result.rounds + " 回合" : ""}</p>
       <p class="hint">${hint}</p>
       <div class="overlay-actions">
         <button class="primary" id="btn-result-next" type="button">立即进入下一局</button>
         <button class="ghost" id="btn-result-log" type="button">看完整战报</button>
       </div>`
    );
    const next = el("btn-result-next");
    next.addEventListener("click", () => {
      if (state.nextRound) applyNextRound();
      else NC.hideOverlay();
    });
    el("btn-result-log").addEventListener("click", () => {
      NC.hideOverlay();
      show("screen-battle");
      NC.battle.toggleLog(true);
    });
    clearTimeout(state.resultTimer);
    state.resultTimer = setTimeout(() => {
      if (state.nextRound) applyNextRound();
    }, 2600);
  }

  function onGameOver(message) {
    // 最后一局也要让玩家看完战斗回放，回放结束后再弹最终结算
    if (state.battle && !NC.battle.isFinished()) {
      state.pendingGameOver = message;
      return;
    }
    showGameOverOverlay(message);
  }

  function showGameOverOverlay(message, options) {
    const opts = options || {};
    state.pendingGameOver = null;
    state.pendingResult = null;
    state.nextRound = null;
    state.nextRoundDeadline = 0;
    NC.battle.reset();
    state.score = message.score || state.score;
    const win = message.winner_seat === state.seat;
    const myScore = state.score[state.seat] || 0;
    const opponentScore = state.score[1 - state.seat] || 0;
    // 赛后重连会再弹一次结算（不能重复记），练习模式打电脑也不计入战绩
    if (opts.record !== false && !state.opponentIsBot) NC.recordMatch(win, myScore, opponentScore);
    NC.audio.play(win ? "victory" : "defeat");
    const history = (message.history || [])
      .map((item) => {
        const label = item.winner_seat === null ? "平局" : item.winner_seat === state.seat ? "胜" : "负";
        return `<li><span>第 ${item.round_index} 局 · ${label}</span><span>${item.rounds} 回合</span></li>`;
      })
      .join("");
    NC.showOverlay(
      `<h2>${win ? "🏆 你赢下了整场对局" : "对局结束"}</h2>
       <div class="score-big">${myScore} : ${opponentScore}</div>
       <p>${win ? "星核为你亮起，归寂潮退去。" : "对手的部署更胜一筹，再来一局试试别的思路。"}</p>
       ${opts.restored ? '<p class="hint">这是刚才那场对局的结算页，重新打开页面也会回到这里。</p>' : ""}
       <ul class="history">${history}</ul>
       <div class="overlay-actions">
         <button class="primary" id="btn-rematch" type="button">再来一局</button>
         <button class="ghost" id="btn-back-lobby" type="button">返回大厅</button>
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
    NC.prepare.stopTimer();
    NC.battle.reset();
    NC.showOverlay(
      `<h2>房间已关闭</h2>
       <p>${NC.escapeHtml(message.reason || "对手已离开房间。")}</p>
       <div class="overlay-actions"><button class="primary" id="btn-closed-back" type="button">返回大厅</button></div>`
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
      if (state.modes.length && !state.modes.some((mode) => mode.key === state.lobbyMode)) {
        state.lobbyMode = state.modes[0].key;
      }
      renderModes();
      renderRules();
    } catch (err) {
      el("rules-content").innerHTML = "<p class='hint'>规则加载失败，请刷新页面重试。</p>";
    } finally {
      state.rulesLoading = false;
    }
  }

  function tagIcon(key, name) {
    if (!key || key === "none") return "";
    return `<img class="tag-icon" src="${NC.tagIconUrl(key)}" alt="${NC.escapeHtml(name || key)}" title="${NC.escapeHtml(
      name || key
    )}" />`;
  }

  function renderRules() {
    const rules = state.rules;
    if (!rules) return;
    const c = rules.constants;
    const characterRows = rules.characters
      .map(
        (ch) =>
          `<tr><td>${ch.id}</td><td><strong>${NC.escapeHtml(ch.name)}</strong></td><td>${NC.escapeHtml(
            ch.role
          )}</td><td>${ch.atk}</td><td>${ch.hp}</td><td>${ch.initiative}</td><td>${tagIcon(
            ch.tag,
            ch.tag_name
          )}${NC.escapeHtml(ch.tag_name)}</td><td>${NC.escapeHtml(ch.domain)}</td></tr>`
      )
      .join("");
    const tagRows = rules.tags
      .filter((tag) => tag.key !== "none")
      .map(
        (tag) =>
          `<tr><td><strong>${tagIcon(tag.key, tag.name)}${NC.escapeHtml(tag.name)}</strong></td><td>${NC.escapeHtml(
            tag.detail
          )}</td></tr>`
      )
      .join("");
    const strategies = rules.strategies.map((item) => `<li>${NC.escapeHtml(item.label)}</li>`).join("");
    const modeRows = (rules.modes || [])
      .map(
        (mode) =>
          `<tr><td><strong>${NC.escapeHtml(mode.name)}</strong></td><td>${NC.escapeHtml(
            mode.summary
          )}</td><td>${NC.escapeHtml(mode.detail)}</td></tr>`
      )
      .join("");

    el("rules-content").innerHTML = `
      <h3>游戏模式</h3>
      <table><thead><tr><th>模式</th><th>一句话</th><th>说明</th></tr></thead><tbody>${modeRows}</tbody></table>

      <h3>一局怎么打</h3>
      <ul>
        <li><b>整场三局共用一份角色池</b>：开局双方各自拿到 ${c.pool_size} 名随机角色，这三局里池子不变，每局从同一份池子里选出 ${c.team_size} 名出战。</li>
        <li>所以真正的胜负手是<b>猜对手会怎么用他手上的牌</b>：谁上阵、排在哪一位、增益给了谁、优先打谁。</li>
        <li><b>补卡</b>：第二局起每局发 3 张候选（从整场没被抽到的角色里随机），选 1 张加入角色池——
        第二局是 7 选 ${c.team_size}，第三局是 8 选 ${c.team_size}。没选就由系统随机补一张，
        所以池子只会变大、不会变小。</li>
        <li>选择顺序就是出击顺序，战斗时按 A1 → B1 → A2 → B2 → A3 → B3 依次行动。</li>
        <li>每人有 ${c.bonus_per_round} 次增益机会：攻击 +${c.bonus_atk} 或生命 +${c.bonus_hp}，单个出击位最多 ${c.max_bonus_per_fighter} 次。</li>
        <li>双方提交后自动演算，先赢下 ${c.rounds_to_win} 局的一方获得整场胜利。</li>
        <li>备局限时 ${c.prepare_timeout} 秒；上一局的回放播完（或你点「跳到结果」）之后才开始计时，看演算不吃准备时间。</li>
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

      <h3>操作方式</h3>
      <ul>
        <li>备战页默认是<b>卡牌视图</b>：把手牌拖到出击位即上阵，拖动可换位，拖回手牌即下阵。手机上按住卡牌约 0.15 秒再拖动。</li>
        <li>补卡面板出现时，点其中一张就把它收进角色池，手牌会立刻从 6 张变成 7、8 张。</li>
        <li>不习惯拖放可以点右上角「切到按钮视图」：点角色依次落位，再点两个出击位交换。</li>
        <li>键盘：<b>1~9</b> 把对应手牌上阵/收回，<b>Backspace</b> 撤销，<b>Enter</b> 提交；战斗页 <b>空格</b> 暂停，<b>→</b> 跳到下一个关键节点。</li>
      </ul>

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
  // 大厅三个按钮的"防连点"锁：一次只允许发出一个进房/匹配请求。
  // 连点两次曾经会把自己配给自己（服务端也已修，这里是前端的第一道防线）。
  const LOBBY_ACTIONS = ["btn-create", "btn-random", "btn-practice", "btn-join"];
  let lobbyLockTimer = null;

  function lockLobbyActions() {
    LOBBY_ACTIONS.forEach((id) => {
      const node = el(id);
      if (node) node.disabled = true;
    });
    clearTimeout(lobbyLockTimer);
    // 网络异常时不要把按钮永久锁死
    lobbyLockTimer = setTimeout(unlockLobbyActions, 6000);
  }

  function unlockLobbyActions() {
    clearTimeout(lobbyLockTimer);
    lobbyLockTimer = null;
    LOBBY_ACTIONS.forEach((id) => {
      const node = el(id);
      if (node) node.disabled = false;
    });
  }

  function requireName() {
    const name = currentNickname();
    if (!name) {
      el("lobby-hint").textContent = "请先填写昵称，对手能看到它。";
      el("input-name").focus();
      return null;
    }
    el("lobby-hint").textContent = "";
    rememberInputs();
    return name;
  }

  function bindEvents() {
    el("btn-create").addEventListener("click", () => {
      const name = requireName();
      if (!name) return;
      lockLobbyActions();
      if (!send({ type: "create_room", name: name, mode: state.lobbyMode })) unlockLobbyActions();
    });

    el("btn-random").addEventListener("click", () => {
      const name = requireName();
      if (!name) return;
      lockLobbyActions();
      if (!send({ type: "join_random", name: name, mode: state.lobbyMode })) unlockLobbyActions();
    });

    el("btn-practice").addEventListener("click", () => {
      const name = requireName();
      if (!name) return;
      lockLobbyActions();
      if (!send({ type: "create_practice", name: name, mode: state.lobbyMode })) unlockLobbyActions();
    });

    el("btn-cancel-match").addEventListener("click", () => send({ type: "cancel_matchmaking" }));

    el("btn-join").addEventListener("click", () => {
      const name = requireName();
      if (!name) return;
      const code = el("input-code").value.trim().toUpperCase();
      if (!code) {
        el("lobby-hint").textContent = "请输入 4 位房间号。";
        el("input-code").focus();
        return;
      }
      lockLobbyActions();
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
    el("btn-review").addEventListener("click", () => NC.battle.showReview());
    el("btn-undo").addEventListener("click", () => NC.prepare.undo());
    el("btn-clear").addEventListener("click", () => NC.prepare.clearPlan());
    el("btn-submit").addEventListener("click", () => NC.prepare.submit());
    el("btn-view").addEventListener("click", () => NC.prepare.toggleView());
    el("btn-leave-waiting").addEventListener("click", () => {
      send({ type: "leave_room" });
      resetToLobby("已离开房间");
    });

    // 战斗回放控制
    el("btn-pause").addEventListener("click", () => NC.battle.togglePause());
    el("btn-speed").addEventListener("click", () => NC.battle.cycleSpeed());
    el("btn-next-event").addEventListener("click", () => NC.battle.nextKeyEvent());
    el("btn-log").addEventListener("click", () => NC.battle.toggleLog());
    el("btn-skip").addEventListener("click", () => NC.battle.skipToEnd());
    el("btn-replay").addEventListener("click", () => NC.battle.replay());

    // 音效开关
    el("btn-sound").addEventListener("click", () => {
      NC.audio.setEnabled(!NC.audio.isEnabled());
      if (NC.audio.isEnabled()) NC.audio.play("ui-select");
      toast(NC.audio.isEnabled() ? "音效已开启" : "音效已关闭，之后可在顶部随时打开");
    });
    // 浏览器要求先有用户交互才允许播放：第一次按下鼠标/触摸时解锁
    const unlock = () => NC.audio.unlock();
    document.addEventListener("pointerdown", unlock, { once: true });
    document.addEventListener("keydown", unlock, { once: true });
    NC.audio.updateToggle();

    el("btn-rules").addEventListener("click", openRules);
    el("btn-rules-back").addEventListener("click", () => show(state.previousScreen));
    el("select-tag").addEventListener("change", (event) => NC.prepare.setStrategyTag(event.target.value));

    document.addEventListener("keydown", (event) => {
      const tag = (event.target && event.target.tagName) || "";
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;

      if (document.querySelector("#screen-battle.is-active")) {
        if (event.key === " ") {
          event.preventDefault();
          NC.battle.togglePause();
        } else if (event.key === "ArrowRight") {
          NC.battle.nextKeyEvent();
        } else if (event.key === "l" || event.key === "L") {
          NC.battle.toggleLog();
        }
        return;
      }

      if (!document.querySelector("#screen-prepare.is-active")) return;
      if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault();
        NC.prepare.undo();
        return;
      }
      if (/^[1-9]$/.test(event.key)) {
        const character = state.pool[Number(event.key) - 1];
        if (character) {
          NC.audio.play("ui-click");
          NC.prepare.toggleCard(character.id);
        }
        return;
      }
      if (event.key !== "Enter") return;
      const submit = el("btn-submit");
      if (submit && !submit.disabled) submit.click();
    });

    // 回到标签页时立刻刷新倒计时，避免后台节流造成显示偏差
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      if (state.timerHandle) {
        el("timer-text").textContent = Math.ceil(Math.max(0, state.deadlineAt - Date.now()) / 1000) + "s";
      }
    });
  }

  /* ------------------------------------------------------------ 启动 */
  function main() {
    // 兜底：任何没被捕获的脚本错误都让玩家知道该刷新，而不是停在原地不动
    window.addEventListener("error", (event) => {
      const message = event && event.message ? event.message : "未知错误";
      if (NC.el("toast")) NC.toast("界面出了点问题（" + message + "），刷新页面可恢复", true);
      if (window.console && window.console.error) window.console.error("[NeoNovaClash]", event.error || event);
    });
    window.addEventListener("unhandledrejection", (event) => {
      if (window.console && window.console.error) window.console.error("[NeoNovaClash] unhandled", event.reason);
    });
    // 旧版本把会话写在 localStorage 里（会被同一浏览器的多个标签页互相覆盖），这里清掉
    try {
      window.localStorage.removeItem(NC.SESSION_KEY);
    } catch (err) {
      /* 忽略 */
    }
    restoreInputs();
    restoreMode();
    NC.prepare.restoreView();
    NC.prepare.bindDrag();
    NC.battle.hooks.onFinished = onReplayFinished;
    bindEvents();
    connect();
    loadRules();
    NC.renderHistory();
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
      // 兜底看门狗：万一某个 round_start 因为回放状态异常没被处理，
      // 这里保证新一局一定会开始，玩家不会永远停在上一局的战场上。
      if (state.nextRound && !state.overlayActive && NC.battle.isFinished()) applyNextRound();
    }, 25000);
  }

  document.addEventListener("DOMContentLoaded", main);
})();
