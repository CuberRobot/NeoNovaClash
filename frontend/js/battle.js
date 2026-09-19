/* 星陨竞技场 · 战斗层
   把事件化战报"演出来"：两军左右对峙、出手冲刺/弹道、命中反馈、飘字、碎裂、复活、状态角标。

   播放器原则（重要）：
   - 逐条推进，用 requestAnimationFrame 驱动，**不做时间追赶**，永远不会"一瞬间全部跑完"；
   - 页面切到后台就暂停，回到前台继续，不会偷偷快进；
   - 每条动作的时长按整场预算自适应缩放，保证 5v5 也不会看太久。 */
(function () {
  "use strict";

  const NC = window.NC;
  const state = NC.state;
  const el = NC.el;
  const toast = NC.toast;

  const SPEEDS = [1, 2, 4];
  const REPLAY_BUDGET_MS = 22000; // 1× 下整场回放的目标时长
  const MIN_ACTION_MS = 70;
  const MAX_ACTION_MS = 900;

  // 各类事件的基准时长（会再按整场预算缩放）
  const BASE_DURATION = {
    battle_start: 800,
    round_start: 620,
    curse: 700,
    attack: 380,
    damage: 240,
    tag: 260,
    shield: 320,
    pierce: 420,
    poison: 200,
    poison_tick: 260,
    heal: 360,
    revive: 720,
    death: 680,
    clash: 780,
    skip: 60,
    battle_end: 900,
  };

  const player = {
    actions: [],
    index: 0,
    playing: false,
    finished: false,
    speed: 1,
    frame: null,
    elapsed: 0,
    startedAt: 0,
    fighters: new Map(), // uid → {data, node, parts}
  };

  // 每局打完都留一份可重播的快照，玩家可以回看任意一局是怎么输的
  const reports = [];

  // 供 app.js 挂钩：回放结束（或跳到结果）后决定下一步
  const hooks = { onFinished: null };

  // ---------------------------------------------------------------- 舞台渲染
  function fighterNode(fighter, side) {
    const node = document.createElement("div");
    node.className = `fighter side-${side}`;
    node.dataset.uid = fighter.uid;

    const head = document.createElement("div");
    head.className = "fighter-head";
    const pos = document.createElement("span");
    pos.className = "fighter-pos";
    pos.textContent = fighter.position;
    const name = document.createElement("span");
    name.className = "fighter-name";
    name.textContent = fighter.name;
    const hpText = document.createElement("span");
    hpText.className = "fighter-hp-text";
    hpText.textContent = `${fighter.hp}/${fighter.max_hp}`;
    head.append(pos, name, hpText);
    node.appendChild(head);

    const body = document.createElement("div");
    body.className = "fighter-body";
    body.appendChild(NC.pieceImage(fighter.char_id, fighter.name, "battle-piece"));
    const badges = document.createElement("div");
    badges.className = "badges";
    body.appendChild(badges);
    const floaters = document.createElement("div");
    floaters.className = "floaters";
    body.appendChild(floaters);
    node.appendChild(body);

    const hp = document.createElement("div");
    hp.className = "hp";
    const lag = document.createElement("i");
    lag.className = "hp-lag";
    const main = document.createElement("i");
    main.className = "hp-main";
    hp.append(lag, main);
    node.appendChild(hp);

    const tagRow = document.createElement("div");
    tagRow.className = "fighter-tags";
    (fighter.tags || []).forEach((tag) => {
      const icon = document.createElement("img");
      icon.src = NC.tagIconUrl(tag);
      icon.alt = tag;
      icon.title = tag;
      tagRow.appendChild(icon);
    });
    node.appendChild(tagRow);

    return node;
  }

  function setHp(entry, hp, { lag = true } = {}) {
    const ratio = Math.max(0, Math.min(1, hp / entry.max));
    entry.hp = hp;
    entry.hpText.textContent = `${Math.max(0, hp)}/${entry.max}`;
    entry.main.style.width = `${ratio * 100}%`;
    if (lag) {
      // 滞后条：慢一拍再追上去，"这一下掉了多少"看得见
      entry.lag.style.width = `${ratio * 100}%`;
    } else {
      entry.lag.style.width = `${ratio * 100}%`;
    }
  }

  function floater(entry, text, kind) {
    const node = document.createElement("span");
    node.className = `floater ${kind || ""}`;
    node.textContent = text;
    entry.floaters.appendChild(node);
    setTimeout(() => node.remove(), 900);
  }

  function badge(entry, text, kind) {
    const node = document.createElement("span");
    node.className = `badge ${kind || ""}`;
    node.textContent = text;
    entry.badges.appendChild(node);
  }

  function setBadge(entry, key, text, kind) {
    entry.badgeMap = entry.badgeMap || new Map();
    const existing = entry.badgeMap.get(key);
    if (existing) {
      existing.textContent = text;
      return;
    }
    const node = document.createElement("span");
    node.className = `badge ${kind || ""}`;
    node.textContent = text;
    entry.badges.appendChild(node);
    entry.badgeMap.set(key, node);
  }

  function initStage(message) {
    const lineups = message.lineups || [];
    // 自己的队伍永远在左侧：座位 1 的玩家也要从左往右看自己的阵容
    const stages = [
      { list: el("stage-a"), side: "a", team: lineups[state.seat] || [] },
      { list: el("stage-b"), side: "b", team: lineups[1 - state.seat] || [] },
    ];
    player.fighters.clear();
    stages.forEach((stage) => {
      stage.list.innerHTML = "";
      stage.team.forEach((fighter) => {
        const node = fighterNode(fighter, stage.side);
        stage.list.appendChild(node);
        player.fighters.set(fighter.uid, {
          data: fighter,
          node,
          side: stage.side,
          hp: fighter.hp,
          max: fighter.max_hp,
          main: node.querySelector(".hp-main"),
          lag: node.querySelector(".hp-lag"),
          hpText: node.querySelector(".fighter-hp-text"),
          badges: node.querySelector(".badges"),
          floaters: node.querySelector(".floaters"),
          body: node.querySelector(".fighter-body"),
          alive: true,
        });
      });
    });
    // 入场动画
    document.querySelectorAll("#stage-a .fighter, #stage-b .fighter").forEach((node, index) => {
      node.classList.add("is-entering");
      setTimeout(() => node.classList.remove("is-entering"), 400 + index * 60);
    });
  }

  function entryOf(uid) {
    return player.fighters.get(uid) || null;
  }

  function banner(text, kind) {
    const node = el("stage-banner");
    node.className = `stage-banner ${kind || ""}`;
    node.textContent = text;
    node.classList.remove("hidden");
    clearTimeout(node.dataset.timer);
    const timer = setTimeout(() => node.classList.add("hidden"), 1100);
    node.dataset.timer = timer;
  }

  function projectile(fromEntry, toEntry, kind) {
    if (!fromEntry || !toEntry) return;
    const from = fromEntry.body.getBoundingClientRect();
    const to = toEntry.body.getBoundingClientRect();
    const node = document.createElement("div");
    node.className = `projectile ${kind || ""}`;
    node.style.left = `${from.left + from.width / 2}px`;
    node.style.top = `${from.top + from.height / 2}px`;
    document.body.appendChild(node);
    requestAnimationFrame(() => {
      node.style.transform = `translate(${to.left + to.width / 2 - (from.left + from.width / 2)}px, ${
        to.top + to.height / 2 - (from.top + from.height / 2)
      }px)`;
    });
    setTimeout(() => node.remove(), 420);
  }

  function dash(entry, direction) {
    if (!entry) return;
    entry.body.style.setProperty("--dash-x", `${direction * 22}px`);
    entry.body.classList.add("is-dashing");
    setTimeout(() => entry.body.classList.remove("is-dashing"), 260);
  }

  function hitFlash(entry, kind) {
    if (!entry) return;
    entry.body.classList.add(kind === "aura" ? "is-aura" : "is-hit");
    setTimeout(() => entry.body.classList.remove(kind === "aura" ? "is-aura" : "is-hit"), 320);
  }

  function shatter(entry) {
    if (!entry) return;
    entry.node.classList.add("is-dead");
    const body = entry.body;
    for (let i = 0; i < 9; i += 1) {
      const shard = document.createElement("span");
      shard.className = "shard";
      const angle = (Math.PI * 2 * i) / 9 + Math.random() * 0.5;
      shard.style.setProperty("--dx", `${Math.cos(angle) * (24 + Math.random() * 22)}px`);
      shard.style.setProperty("--dy", `${Math.sin(angle) * (24 + Math.random() * 22)}px`);
      shard.style.setProperty("--rot", `${(Math.random() - 0.5) * 540}deg`);
      body.appendChild(shard);
      setTimeout(() => shard.remove(), 900);
    }
  }

  function flashScreen() {
    document.body.classList.add("screen-flash");
    setTimeout(() => document.body.classList.remove("screen-flash"), 260);
  }

  function shakeScreen() {
    document.body.classList.add("screen-shake");
    setTimeout(() => document.body.classList.remove("screen-shake"), 420);
  }

  function sideDirection(entry) {
    return entry.side === "a" ? 1 : -1;
  }

  // ---------------------------------------------------------------- 事件 → 动作
  function buildActions(events) {
    const actions = events.map((event) => ({
      event,
      duration: BASE_DURATION[event.kind] || 220,
      run: () => runEvent(event),
    }));

    const total = actions.reduce((sum, action) => sum + action.duration, 0) || 1;
    const scale = Math.max(0.4, Math.min(1, REPLAY_BUDGET_MS / total));
    actions.forEach((action, index) => {
      const event = action.event;
      let duration = action.duration * scale;
      // 致命一击与阵亡给一点戏剧化的停顿
      if (event.kind === "damage" && event.data && event.data.lethal) duration *= 1.8;
      if (event.kind === "clash" || event.kind === "revive" || event.kind === "death") duration *= 1.15;
      action.duration = Math.max(MIN_ACTION_MS, Math.min(MAX_ACTION_MS, Math.round(duration)));
      action.index = index;
    });
    return actions;
  }

  function runEvent(event) {
    const data = event.data || {};
    switch (event.kind) {
      case "battle_start": {
        const first = data.first_team;
        banner(`开战 · ${first === state.seat ? "你" : "对手"}先手`, "round");
        NC.audio.play("ui-round");
        break;
      }
      case "round_start":
        banner(`第 ${event.round} 回合`);
        NC.audio.play("ui-round");
        break;
      case "curse": {
        const target = entryOf(data.target);
        if (target) {
          hitFlash(target, "aura");
          floater(target, "标签被剥夺", "status");
        }
        banner("标签剥夺", "minor");
        NC.audio.play("ui-error", { volume: 0.5 });
        break;
      }
      case "attack": {
        const attacker = entryOf(data.attacker);
        const targets = data.targets ? data.targets.map(entryOf).filter(Boolean) : [entryOf(data.target)];
        if (!attacker) break;
        if (data.aoe) {
          dash(attacker, sideDirection(attacker));
          targets.forEach((target) => projectile(attacker, target, "wave"));
          shakeScreen();
          NC.audio.play("aoe");
          break;
        }
        const target = targets[0];
        const ranged = (attacker.data.tags || []).some((tag) => tag === "pierce" || tag === "aoe" || tag === "poison");
        if (ranged) {
          projectile(attacker, target, "arrow");
          NC.audio.play("hit-pierce", { volume: 0.7 });
        } else {
          dash(attacker, sideDirection(attacker));
          NC.audio.play("hit-light");
        }
        break;
      }
      case "damage": {
        const target = entryOf(data.target);
        if (!target) break;
        hitFlash(target);
        setHp(target, data.hp_after);
        if (data.lethal) {
          flashScreen();
          floater(target, "击杀", "lethal");
        } else {
          floater(target, `-${data.amount}`, "damage");
        }
        break;
      }
      case "tag": {
        const target = entryOf(data.defender || data.target);
        if (target && data.before && data.after) {
          floater(target, `-${data.before - data.after}`, "block");
          setBadge(target, "block", "减伤", "block");
        }
        NC.audio.play("hit-heavy", { volume: 0.5 });
        break;
      }
      case "shield": {
        const bearer = entryOf(data.bearer);
        const target = entryOf(data.target);
        if (bearer && target) {
          projectile(bearer, target, "shield");
          floater(target, `护盾 ${data.amount}`, "shield");
          if (typeof data.charges_left === "number") {
            setBadge(bearer, "shield", `护盾 ×${data.charges_left}`, "shield");
          }
        }
        NC.audio.play("hit-heavy", { volume: 0.5 });
        break;
      }
      case "pierce": {
        const attacker = entryOf(data.attacker);
        const target = entryOf(data.target);
        if (attacker && target) {
          projectile(attacker, target, "arrow");
          banner("箭矢穿透", "minor");
        }
        NC.audio.play("hit-pierce", { volume: 0.6 });
        break;
      }
      case "poison": {
        const target = entryOf(data.target);
        if (target) {
          setBadge(target, "poison", `毒 ×${data.stacks}`, "poison");
          floater(target, "中毒", "status");
        }
        NC.audio.play("poison", { volume: 0.5 });
        break;
      }
      case "poison_tick": {
        const target = entryOf(data.target);
        if (target) {
          hitFlash(target, "aura");
          setHp(target, data.hp_after);
          floater(target, `-${data.amount}`, "poison");
          setBadge(target, "poison", `毒 ×${data.stacks || 0}`, "poison");
        }
        NC.audio.play("poison", { volume: 0.4 });
        break;
      }
      case "heal": {
        const target = entryOf(data.target);
        if (target) {
          setHp(target, data.hp_after);
          floater(target, `+${data.amount}`, "heal");
          hitFlash(target, "aura");
        }
        NC.audio.play("heal", { volume: 0.7 });
        break;
      }
      case "revive": {
        const target = entryOf(data.target);
        if (target) {
          target.node.classList.remove("is-dead");
          target.alive = true;
          setHp(target, data.hp_after);
          floater(target, "复活", "heal");
          banner("死灵法师复活了队友", "minor");
          if (typeof data.charges_left === "number") {
            const healer = entryOf(data.healer);
            if (healer) setBadge(healer, "revive", `复活 ×${data.charges_left}`, "heal");
          }
        }
        NC.audio.play("heal");
        break;
      }
      case "death": {
        const target = entryOf(data.target);
        if (target) {
          target.alive = false;
          setHp(target, 0);
          shatter(target);
        }
        NC.audio.play("shatter");
        break;
      }
      case "clash": {
        const a = entryOf(data.attacker);
        const b = entryOf(data.target);
        [a, b].forEach((entry) => {
          if (!entry) return;
          entry.alive = false;
          setHp(entry, 0);
          shatter(entry);
        });
        flashScreen();
        shakeScreen();
        banner("同归于尽", "danger");
        NC.audio.play("explosion");
        break;
      }
      case "battle_end": {
        const winner = data.winner_team;
        banner(winner === null ? "平局" : winner === state.seat ? "你赢下本局" : "本局失利", "round");
        NC.audio.play(winner === state.seat ? "ui-confirm" : "ui-remove");
        break;
      }
      default:
        break;
    }
    appendLog(event);
  }

  function appendLog(event) {
    const log = el("battle-log");
    const li = document.createElement("li");
    li.className = "kind-" + event.kind;
    li.textContent = event.text;
    log.appendChild(li);
    if (el("battle-log-wrap").classList.contains("is-open")) log.scrollTop = log.scrollHeight;
  }

  // ---------------------------------------------------------------- 播放器
  function onReport(message) {
    stop();
    NC.hideOverlay();
    state.score = message.score || state.score;
    const events = (message.result && message.result.events) || [];
    state.lastBattle = {
      roundIndex: message.round_index,
      lines: events.map((event) => ({ round: event.round, kind: event.kind, text: event.text })),
    };
    const snapshot = {
      roundIndex: message.round_index,
      events: events,
      lineups: message.lineups || [],
    };
    const existing = reports.findIndex((item) => item.roundIndex === snapshot.roundIndex);
    if (existing >= 0) reports[existing] = snapshot;
    else reports.push(snapshot);
    // 保留阵容快照，这样「重播本局」不需要再向服务器要一次数据
    state.battle = { roundIndex: message.round_index, events: events, lineups: message.lineups || [], message: message };
    el("battle-round").textContent = String(message.round_index);
    el("battle-score").textContent = state.score.join(" : ");
    el("battle-log").innerHTML = "";
    el("log-progress").textContent = `0 / ${events.length}`;
    el("btn-pause").textContent = "暂停";
    el("btn-speed").textContent = "1×";
    el("stage-banner").classList.add("hidden");
    initStage(message);
    player.actions = buildActions(events);
    player.index = 0;
    player.elapsed = 0;
    player.speed = 1;
    player.finished = false;
    player.isReplay = false;
    player.playing = true;
    player.startedAt = performance.now();
    NC.show("screen-battle");
    // 上一局可能把页面滚到了战报底部，开新一局时把舞台带回视野
    window.scrollTo({ top: 0 });
    if (message.auto_submitted && message.auto_submitted.length) {
      const who = message.auto_submitted.map((seat) => NC.teamLabel(seat)).join("、");
      toast(`${who} 超时，已由系统随机提交方案`);
    }
    if (!player.actions.length) {
      finish();
      return;
    }
    player.frame = requestAnimationFrame(tick);
  }

  function tick(now) {
    if (!player.playing) return;
    if (player.index >= player.actions.length) {
      finish();
      return;
    }
    const action = player.actions[player.index];
    if (player.elapsed === 0) {
      // 每条动作只在开始时执行一次
      try {
        action.run();
      } catch (err) {
        /* 单条动作出错不影响整场回放 */
      }
      player.elapsed = action.duration;
    }
    player.elapsed -= (now - player.startedAt) * player.speed;
    player.startedAt = now;
    el("log-progress").textContent = progressText(player.index + 1, player.actions.length);
    if (player.elapsed <= 0) {
      player.index += 1;
      player.elapsed = 0;
    }
    player.frame = requestAnimationFrame(tick);
  }

  function progressText(done, total) {
    let text = `${done} / ${total}`;
    if (state.nextRoundDeadline) {
      const remain = Math.max(0, Math.round((state.nextRoundDeadline - Date.now()) / 1000));
      text += ` · 下一局剩余 ${remain}s`;
    }
    return text;
  }

  function stop() {
    if (player.frame) cancelAnimationFrame(player.frame);
    player.frame = null;
    player.playing = false;
  }

  /** 彻底收摊：停掉播放并清空这一局的回放数据。
   *  离开房间、重连、整场结束都要调用它，避免残留的 playing/finished 状态
   *  把后面新的一局 round_start 卡住。 */
  function reset() {
    stop();
    player.actions = [];
    player.index = 0;
    player.elapsed = 0;
    player.finished = true;
    player.isReplay = false;
  }

  function finish() {
    stop();
    player.finished = true;
    el("btn-pause").textContent = "已结束";
    el("log-progress").textContent = "播放完成";
    if (typeof hooks.onFinished === "function") hooks.onFinished({ replay: Boolean(player.isReplay) });
  }

  function togglePause() {
    if (player.finished) return;
    if (player.playing) {
      stop();
      el("btn-pause").textContent = "继续";
    } else {
      if (player.index >= player.actions.length) return;
      player.playing = true;
      player.startedAt = performance.now();
      player.frame = requestAnimationFrame(tick);
      el("btn-pause").textContent = "暂停";
    }
  }

  function cycleSpeed() {
    const next = SPEEDS[(SPEEDS.indexOf(player.speed) + 1) % SPEEDS.length];
    player.speed = next;
    el("btn-speed").textContent = `${next}×`;
    NC.audio.play("ui-select");
  }

  function runTo(targetIndex) {
    while (player.index < Math.min(targetIndex, player.actions.length)) {
      try {
        player.actions[player.index].run();
      } catch (err) {
        /* 忽略单条异常 */
      }
      player.index += 1;
    }
    el("log-progress").textContent = progressText(player.index, player.actions.length);
  }

  function nextKeyEvent() {
    const target = player.actions.findIndex((action, index) => {
      if (index <= player.index) return false;
      return ["death", "clash", "revive", "round_start", "battle_end"].includes(action.event.kind);
    });
    if (target < 0) {
      skipToEnd();
      return;
    }
    runTo(target);
  }

  function skipToEnd() {
    runTo(player.actions.length);
    finish();
  }

  /** 重播某一局：用本地存的快照重建舞台，不碰当前比分与回合进度。 */
  function playSnapshot(snapshot) {
    if (!snapshot) return;
    stop();
    NC.hideOverlay();
    el("battle-round").textContent = String(snapshot.roundIndex);
    el("battle-log").innerHTML = "";
    el("log-progress").textContent = `0 / ${snapshot.events.length}`;
    el("btn-pause").textContent = "暂停";
    el("stage-banner").classList.add("hidden");
    initStage({ lineups: snapshot.lineups });
    player.actions = buildActions(snapshot.events);
    player.index = 0;
    player.elapsed = 0;
    player.speed = 1;
    player.finished = false;
    player.isReplay = true;
    player.playing = true;
    player.startedAt = performance.now();
    el("btn-speed").textContent = "1×";
    show("screen-battle");
    toast(`重播第 ${snapshot.roundIndex} 局`);
    if (!player.actions.length) {
      finish();
      return;
    }
    player.frame = requestAnimationFrame(tick);
  }

  /** 重播入口：只有一局就直接放，有多局先让玩家挑。 */
  function replay() {
    if (!reports.length) {
      toast("还没有可以重播的对局");
      return;
    }
    if (reports.length === 1) {
      playSnapshot(reports[0]);
      return;
    }
    const rows = reports
      .map(
        (item) =>
          `<li><button class="ghost" type="button" data-replay="${item.roundIndex}">第 ${item.roundIndex} 局</button>
           <span class="hint">${item.events.length} 条战报</span></li>`
      )
      .join("");
    NC.showOverlay(
      `<h2>重播哪一局？</h2>
       <ul class="replay-list">${rows}</ul>
       <div class="overlay-actions"><button class="primary" id="btn-replay-close" type="button">关闭</button></div>`
    );
    document.querySelectorAll("[data-replay]").forEach((node) => {
      node.addEventListener("click", () => {
        const roundIndex = Number(node.dataset.replay);
        playSnapshot(reports.find((item) => item.roundIndex === roundIndex));
      });
    });
    el("btn-replay-close").addEventListener("click", NC.hideOverlay);
  }

  // 页面切到后台就暂停，避免"偷偷跑完"
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && player.playing) {
      stop();
      el("btn-pause").textContent = "继续";
      toast("页面切到后台，回放已暂停");
    }
  });

  // ---------------------------------------------------------------- 文字战报
  function toggleLog(forceOpen) {
    const wrap = el("battle-log-wrap");
    if (forceOpen === undefined) wrap.classList.toggle("is-open");
    else wrap.classList.toggle("is-open", Boolean(forceOpen));
    el("btn-log").textContent = wrap.classList.contains("is-open") ? "收起战报" : "展开战报";
    if (wrap.classList.contains("is-open")) {
      const log = el("battle-log");
      log.scrollTop = log.scrollHeight;
    }
  }

  function showReview() {
    const review = state.lastBattle;
    if (!review) {
      toast("还没有可以回看的战报");
      return;
    }
    const rows = review.lines
      .map((line) => `<p class="${line.kind === "round_start" ? "is-round" : ""}">${NC.escapeHtml(line.text)}</p>`)
      .join("");
    NC.showOverlay(
      `<h2>第 ${review.roundIndex} 局战报</h2>
       <div class="battle-review">${rows}</div>
       <div class="overlay-actions"><button class="primary" id="btn-review-close" type="button">关闭</button></div>`
    );
    el("btn-review-close").addEventListener("click", NC.hideOverlay);
  }

  NC.battle = {
    onReport,
    stop,
    reset,
    finish,
    togglePause,
    cycleSpeed,
    skipToEnd,
    nextKeyEvent,
    replay,
    toggleLog,
    showReview,
    isPlaying: () => player.playing,
    // 没有待播动作也算结束：这样 teardown 之后不会被误判成"还在播"
    isFinished: () => player.finished || player.actions.length === 0,
    hooks,
  };
})();
