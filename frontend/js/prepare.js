/* 星陨竞技场 · 备战层
   两种视图共用同一份状态与同一套动作：
     - 卡牌视图（默认）：手牌拖到出击位，拖动可换位，拖回手牌即下阵；
     - 按钮视图：点选角色依次落位，点位互换，筹码点选后点位置。
   所有规则校验（选几个人、增益上限、自爆首位）都只在这一层做一次。 */
(function () {
  "use strict";

  const NC = window.NC;
  const state = NC.state;
  const el = NC.el;
  const toast = NC.toast;

  // ---------------------------------------------------------------- 视图切换
  function view() {
    return state.view;
  }

  function restoreView() {
    const saved = NC.store.get(NC.VIEW_KEY, NC.VIEW_CARDS);
    state.view = saved === NC.VIEW_BUTTONS ? NC.VIEW_BUTTONS : NC.VIEW_CARDS;
  }

  function setView(next) {
    state.view = next === NC.VIEW_BUTTONS ? NC.VIEW_BUTTONS : NC.VIEW_CARDS;
    NC.store.set(NC.VIEW_KEY, state.view);
    render();
  }

  function toggleView() {
    setView(state.view === NC.VIEW_CARDS ? NC.VIEW_BUTTONS : NC.VIEW_CARDS);
    NC.audio.play("ui-select");
    toast(state.view === NC.VIEW_CARDS ? "已切换到卡牌视图（拖动上阵）" : "已切换到按钮视图");
  }

  function updateViewToggle() {
    const node = el("btn-view");
    if (node) node.textContent = state.view === NC.VIEW_CARDS ? "切到按钮视图" : "切到卡牌视图";
    const wrap = el("prepare-grid");
    if (wrap) wrap.classList.toggle("view-cards", state.view === NC.VIEW_CARDS);
  }

  // ---------------------------------------------------------------- 动作层
  function card(charId) {
    return state.pool.find((item) => item.id === charId) || null;
  }

  function snapshotPlan() {
    return {
      selection: state.selection.slice(),
      bonuses: state.bonuses.map((bonus) => ({ slot: bonus.slot, kind: bonus.kind })),
      strategy: { kind: state.strategy.kind, tag: state.strategy.tag },
    };
  }

  function pushUndo() {
    state.undoStack.push(snapshotPlan());
    if (state.undoStack.length > 30) state.undoStack.shift();
    const node = el("btn-undo");
    if (node) node.disabled = false;
  }

  function resetUndo() {
    state.undoStack = [];
    const node = el("btn-undo");
    if (node) node.disabled = true;
  }

  function undo() {
    const previous = state.undoStack.pop();
    if (!previous) {
      toast("没有可以撤销的操作");
      return;
    }
    state.selection = previous.selection;
    state.bonuses = previous.bonuses;
    state.strategy = previous.strategy;
    state.swapFrom = null;
    state.armedBonus = null;
    const node = el("btn-undo");
    if (node) node.disabled = state.undoStack.length === 0;
    NC.audio.play("ui-remove");
    render();
  }

  function bonusCountForSlot(slot) {
    return state.bonuses.filter((bonus) => bonus.slot === slot).length;
  }

  function slotStats(character, slot) {
    const atkBonus = state.bonuses.filter((b) => b.slot === slot && b.kind === "atk").length * 2;
    const hpBonus = state.bonuses.filter((b) => b.slot === slot && b.kind === "hp").length * 4;
    return { atk: character.atk + atkBonus, hp: character.hp + hpBonus, atkBonus, hpBonus };
  }

  /** 上阵：把角色放到指定出击位（已在阵容里则相当于换位）。 */
  function placeAt(charId, index, options) {
    const character = card(charId);
    if (!character) return false;
    const existing = state.selection.indexOf(charId);
    const target = Math.max(0, Math.min(index, state.teamSize - 1));
    if (existing === target) return false;

    pushUndo();
    if (existing >= 0) {
      state.selection.splice(existing, 1);
    } else if (state.selection.length >= state.teamSize && !state.selection[target]) {
      toast("最多只能选 " + state.teamSize + " 名角色，先取消一个再选", true);
      state.undoStack.pop();
      return false;
    }
    if (target >= state.selection.length) {
      state.selection.push(charId);
    } else {
      state.selection.splice(target, 0, charId);
    }
    state.selection = state.selection.slice(0, state.teamSize);
    const moved = normalizeFirstSlot();
    NC.audio.play("ui-place");
    if (moved && !(options && options.silent)) {
      toast(`${moved} 只能放在第一个出击位，已自动调整`);
    }
    render();
    return true;
  }

  /** 确保「只能放首位」的角色在第 1 位（自爆步兵）。 */
  function normalizeFirstSlot() {
    const index = state.selection.findIndex((charId) => {
      const character = card(charId);
      return character && character.place_first;
    });
    if (index <= 0) return null;
    const charId = state.selection.splice(index, 1)[0];
    state.selection.unshift(charId);
    return card(charId) ? card(charId).name : null;
  }

  function removeSlot(index) {
    if (!state.selection[index]) return;
    pushUndo();
    state.selection.splice(index, 1);
    state.bonuses = state.bonuses.filter((bonus, bonusIndex) => {
      void bonusIndex;
      return bonus.slot !== index + 1;
    });
    // 后面的增益位次前移
    state.bonuses = state.bonuses.map((bonus) => (bonus.slot > index + 1 ? { ...bonus, slot: bonus.slot - 1 } : bonus));
    state.swapFrom = null;
    NC.audio.play("ui-remove");
    render();
  }

  function toggleCard(charId) {
    const index = state.selection.indexOf(charId);
    if (index >= 0) {
      removeSlot(index);
      return;
    }
    const empty = state.selection.findIndex((item) => !item);
    const target = empty >= 0 ? empty : state.selection.length;
    placeAt(charId, target);
  }

  function swapSlots(a, b) {
    if (a === b) return;
    const moved = state.selection[a];
    const replaced = state.selection[b];
    const movedChar = moved ? card(moved) : null;
    const targetChar = replaced ? card(replaced) : null;
    const fixed = [movedChar, targetChar].find((character) => character && character.place_first);
    if (fixed) {
      toast(`${fixed.name} 只能待在第一个出击位，已自动调整`);
      // 首位角色不参与交换：把它放回第 1 位
      const charId = fixed.id;
      const from = state.selection.indexOf(charId);
      if (from > 0) {
        state.selection.splice(from, 1);
        state.selection.unshift(charId);
        render();
      }
      return;
    }
    pushUndo();
    state.selection[a] = replaced;
    state.selection[b] = moved;
    NC.audio.play("ui-place");
    render();
  }

  function clearPlan() {
    pushUndo();
    state.selection = [];
    state.bonuses = [];
    state.swapFrom = null;
    state.armedBonus = null;
    NC.audio.play("ui-remove");
    render();
  }

  function addBonus(slot, kind) {
    if (slot < 1 || slot > state.teamSize || !state.selection[slot - 1]) {
      toast("增益只能给已经上阵的出击位", true);
      return false;
    }
    if (state.bonuses.length >= state.bonusPerRound) {
      toast("增益次数已经用完（共 " + state.bonusPerRound + " 次）", true);
      NC.audio.play("ui-error");
      return false;
    }
    if (bonusCountForSlot(slot) >= state.maxBonusPerFighter) {
      toast("每个出击位最多获得 " + state.maxBonusPerFighter + " 次增益", true);
      NC.audio.play("ui-error");
      return false;
    }
    pushUndo();
    state.bonuses.push({ slot: slot, kind: kind });
    if (state.bonuses.length >= state.bonusPerRound) state.armedBonus = null;
    NC.audio.play("ui-place");
    render();
    return true;
  }

  function removeBonusAt(index) {
    if (index < 0 || index >= state.bonuses.length) return;
    pushUndo();
    state.bonuses.splice(index, 1);
    NC.audio.play("ui-remove");
    render();
  }

  function setStrategy(kind, tag) {
    pushUndo();
    state.strategy = { kind: kind, tag: tag === undefined ? null : tag };
    NC.audio.play("ui-select");
    render();
  }

  function setStrategyTag(tag) {
    state.strategy.tag = tag;
    updateSubmitState();
  }

  // ---------------------------------------------------------------- 渲染
  function render() {
    el("round-index").textContent = String(state.roundIndex);
    el("score-display").textContent = state.score.join(" : ");
    const review = el("btn-review");
    if (review) review.classList.toggle("hidden", !state.lastBattle);
    const badge = el("mode-badge");
    if (state.roomMode) {
      const mode = state.modes.find((item) => item.key === state.roomMode);
      badge.textContent = mode ? mode.name : state.roomMode;
      badge.classList.remove("hidden");
    }
    renderHand();
    renderSlots();
    renderStrategy();
    renderTokens();
    renderMetrics();
    renderChecklist();
    renderBanner();
    renderPrepareStatus();
    updateSubmitState();
    updateViewToggle();
  }

  function renderBanner() {
    const banner = el("last-round-banner");
    const result = state.pendingResult || state.lastRoundResult;
    if (!result) {
      banner.classList.add("hidden");
      return;
    }
    const myScore = state.score[state.seat] || 0;
    const opponentScore = state.score[1 - state.seat] || 0;
    const outcome =
      result.winner_seat === null ? "平局" : result.winner_seat === state.seat ? "你赢下本局" : "本局失利";
    banner.className =
      "banner " + (result.winner_seat === state.seat ? "win" : result.winner_seat === null ? "" : "lose");
    banner.textContent = `上一局：${outcome}（比分 ${myScore} : ${opponentScore}）— ${result.reason}`;
  }

  /** 手牌（卡牌视图）：角色池本身；已上阵的会标上出击位序号。 */
  function renderHand() {
    const container = el("hand");
    if (!container) return;
    container.innerHTML = "";
    state.pool.forEach((character) => {
      const order = state.selection.indexOf(character.id);
      const node = document.createElement("div");
      node.className = "hand-card" + (order >= 0 ? " is-selected" : "");
      node.dataset.charId = String(character.id);
      node.tabIndex = 0;

      const top = document.createElement("div");
      top.className = "hand-card-top";
      top.appendChild(NC.pieceImage(character.id, character.name, "hand-piece"));
      if (order >= 0) {
        const badge = document.createElement("span");
        badge.className = "order-badge";
        badge.textContent = String(order + 1);
        top.appendChild(badge);
      }
      node.appendChild(top);

      const name = document.createElement("strong");
      name.className = "hand-name";
      name.textContent = character.name;
      node.appendChild(name);

      const stats = document.createElement("div");
      stats.className = "hand-stats";
      stats.textContent = `ATK ${character.atk} · HP ${character.hp} · 先手 ${character.initiative}`;
      node.appendChild(stats);

      const tagRow = document.createElement("div");
      tagRow.className = "hand-tags";
      (character.tags || []).forEach((tag, index) => {
        if (!tag || tag === "none") return;
        const icon = document.createElement("img");
        icon.src = NC.tagIconUrl(tag);
        icon.alt = (character.tag_names || [])[index] || tag;
        icon.title = icon.alt;
        tagRow.appendChild(icon);
      });
      if ((character.tags || []).length) node.appendChild(tagRow);
      if (character.place_first) {
        const warn = document.createElement("span");
        warn.className = "hand-warn";
        warn.textContent = "只能放第 1 位";
        node.appendChild(warn);
      }
      node.addEventListener("click", () => {
        if (dragMoved) return; // 拖拽结束时不要误触发点击
        NC.audio.play("ui-click");
        toggleCard(character.id);
      });
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          NC.audio.play("ui-click");
          toggleCard(character.id);
        }
      });
      container.appendChild(node);
    });
  }

  function renderSlots() {
    const container = el("slots");
    container.innerHTML = "";
    for (let index = 0; index < state.teamSize; index += 1) {
      const charId = state.selection[index];
      const character = charId ? card(charId) : null;
      const isSwapSource = state.swapFrom === index;
      const li = document.createElement("li");
      li.dataset.slot = String(index);
      li.className =
        "slot" +
        (character ? " is-filled" : "") +
        (isSwapSource ? " is-swap-source" : "") +
        (state.swapFrom !== null && !isSwapSource ? " is-drop-target" : "") +
        (state.armedBonus && character ? " is-armed-target" : "");
      li.addEventListener("click", (event) => {
        if (event.target.closest("button") || event.target.closest(".bonus-chip")) return;
        if (dragMoved) return;
        onSlotClick(index);
      });

      const head = document.createElement("div");
      head.className = "slot-head";
      const label = document.createElement("span");
      label.className = "slot-index";
      label.textContent = "第 " + (index + 1) + " 位" + (isSwapSource ? "（点另一个位交换）" : "");
      head.appendChild(label);
      if (character) {
        const stats = slotStats(character, index + 1);
        const info = document.createElement("span");
        info.className = "card-stats";
        info.innerHTML = `<strong>${NC.escapeHtml(character.name)}</strong> · ATK ${stats.atk} · HP ${stats.hp}`;
        head.appendChild(info);
      }
      li.appendChild(head);

      if (!character) {
        const empty = document.createElement("div");
        empty.className = "slot-empty";
        empty.textContent = state.view === NC.VIEW_CARDS ? "把手牌拖到这里（或点击手牌）" : "点击左侧角色加入这一位";
        li.appendChild(empty);
        container.appendChild(li);
        continue;
      }

      const portrait = document.createElement("div");
      portrait.className = "slot-portrait";
      portrait.appendChild(NC.pieceImage(character.id, character.name, "slot-piece"));
      const tagRow = document.createElement("div");
      tagRow.className = "slot-tags";
      (character.tags || []).forEach((tag, tagIndex) => {
        if (!tag || tag === "none") return;
        const icon = document.createElement("img");
        icon.src = NC.tagIconUrl(tag);
        icon.alt = (character.tag_names || [])[tagIndex] || tag;
        icon.title = icon.alt;
        tagRow.appendChild(icon);
      });
      portrait.appendChild(tagRow);
      li.appendChild(portrait);

      const chips = document.createElement("div");
      chips.className = "slot-actions";
      state.bonuses.forEach((bonus, bonusIndex) => {
        if (bonus.slot !== index + 1) return;
        const chip = document.createElement("span");
        chip.className = "bonus-chip";
        chip.textContent = (bonus.kind === "atk" ? "攻击 +2" : "生命 +4") + " ×";
        chip.title = "点击移除这次增益";
        chip.addEventListener("click", (event) => {
          event.stopPropagation();
          removeBonusAt(bonusIndex);
        });
        chips.appendChild(chip);
      });
      li.appendChild(chips);

      const actions = document.createElement("div");
      actions.className = "slot-actions";
      actions.appendChild(NC.button("交换位次", "ghost small", () => startSwap(index)));
      actions.appendChild(NC.button("移除", "ghost small", () => removeSlot(index)));
      li.appendChild(actions);
      container.appendChild(li);
    }
    el("bonus-left").textContent = String(Math.max(0, state.bonusPerRound - state.bonuses.length));
  }

  function onSlotClick(index) {
    const character = state.selection[index] ? card(state.selection[index]) : null;
    if (!character) {
      if (state.swapFrom !== null) {
        state.swapFrom = null;
        renderSlots();
        return;
      }
      if (state.armedBonus) toast("先选好角色再放增益");
      return;
    }
    // 手里握着筹码时，点位置就是放增益；换位要等筹码放下后再点
    if (state.armedBonus) {
      addBonus(index + 1, state.armedBonus);
      return;
    }
    if (state.swapFrom !== null) {
      startSwap(index);
      return;
    }
    startSwap(index);
  }

  function startSwap(index) {
    if (!state.selection[index]) {
      state.swapFrom = null;
      renderSlots();
      return;
    }
    if (state.swapFrom === null) {
      state.swapFrom = index;
      renderSlots();
      return;
    }
    if (state.swapFrom === index) {
      state.swapFrom = null;
      renderSlots();
      return;
    }
    const from = state.swapFrom;
    state.swapFrom = null;
    swapSlots(from, index);
  }

  function renderStrategy() {
    const container = el("strategy-options");
    container.innerHTML = "";
    state.strategies.forEach((strategy) => {
      const label = document.createElement("label");
      label.className = "strategy-card" + (state.strategy.kind === strategy.kind ? " is-active" : "");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "strategy";
      input.value = strategy.kind;
      input.checked = state.strategy.kind === strategy.kind;
      input.addEventListener("change", () => setStrategy(strategy.kind, null));
      label.appendChild(input);
      const text = document.createElement("span");
      text.textContent = strategy.label;
      label.appendChild(text);
      container.appendChild(label);
    });

    const detail = el("strategy-detail");
    const current = state.strategies.find((item) => item.kind === state.strategy.kind);
    detail.textContent = current ? current.label + (current.need_tag ? "：需要再选一个标签" : "") : "";

    const picker = el("tag-picker");
    const isTagStrategy = state.strategy.kind === "tag_priority";
    picker.classList.toggle("hidden", !isTagStrategy);
    if (isTagStrategy) {
      const select = el("select-tag");
      const tags = [];
      state.pool.forEach((character) => {
        (character.tags || []).forEach((tag, index) => {
          if (tag && tag !== "none" && !tags.some((item) => item.key === tag)) {
            tags.push({ key: tag, name: (character.tag_names || [])[index] || tag });
          }
        });
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
        if (!tags.some((tag) => tag.key === previous)) state.strategy.tag = tags[0].key;
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

  function renderTokens() {
    const box = el("bonus-tokens");
    box.innerHTML = "";
    const remaining = state.bonusPerRound - state.bonuses.length;
    [
      { kind: "atk", label: "攻击 +2" },
      { kind: "hp", label: "生命 +4" },
    ].forEach((option) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "token" + (state.armedBonus === option.kind ? " is-armed" : "");
      chip.dataset.bonusKind = option.kind;
      chip.textContent = option.label;
      chip.disabled = remaining <= 0;
      chip.addEventListener("click", () => {
        if (remaining <= 0) {
          toast("增益次数已经用完（共 " + state.bonusPerRound + " 次）", true);
          NC.audio.play("ui-error");
          return;
        }
        state.armedBonus = state.armedBonus === option.kind ? null : option.kind;
        // 拿起筹码时退出换位状态，两个操作不会互相打断
        if (state.armedBonus) state.swapFrom = null;
        NC.audio.play("ui-select");
        renderTokens();
        renderSlots();
        toast(state.armedBonus ? `已选中「${option.label}」，点一个出击位放置` : "已取消选择");
      });
      box.appendChild(chip);
    });
  }

  function renderMetrics() {
    const box = el("metrics");
    const picked = state.selection.map((charId) => card(charId)).filter(Boolean);
    if (!picked.length) {
      box.innerHTML = `<span>选好角色后，这里会显示这套阵容的先手值、总攻击/总生命与标签构成。</span>`;
      return;
    }
    const initiative = picked.reduce((sum, character) => sum + character.initiative, 0);
    const complete = picked.length === state.teamSize;
    const values = state.pool.map((character) => character.initiative).sort((a, b) => a - b);
    const lowestPossible = values.slice(0, state.teamSize).reduce((sum, value) => sum + value, 0);
    const tier = !complete
      ? `还差 ${state.teamSize - picked.length} 名，选满后可与本池最低可能对比`
      : initiative <= lowestPossible + 2
        ? "偏低（较容易抢到先手）"
        : initiative <= lowestPossible + 6
          ? "中等"
          : "偏高（较难抢到先手）";

    let atk = 0;
    let hp = 0;
    const tagCounter = new Map();
    state.selection.forEach((charId, index) => {
      const character = card(charId);
      if (!character) return;
      const stats = slotStats(character, index + 1);
      atk += stats.atk;
      hp += stats.hp;
      (character.tags || []).forEach((tag, tagIndex) => {
        if (!tag || tag === "none") return;
        const name = (character.tag_names || [])[tagIndex] || tag;
        tagCounter.set(name, (tagCounter.get(name) || 0) + 1);
      });
    });
    const tags = [...tagCounter.entries()].map(([name, count]) => `${name} ×${count}`).join("　") || "无标签";

    box.innerHTML = `
      <div class="metric-row">
        <span>先手值 <strong>${initiative}</strong>${complete ? `（本池最低可能 ${lowestPossible}）` : ""}</span>
        <span class="initiative-tag">${tier}</span>
      </div>
      <div class="metric-row">
        <span>总攻击 <strong>${atk}</strong></span>
        <span>总生命 <strong>${hp}</strong></span>
        <span>出战 ${picked.length}/${state.teamSize}</span>
      </div>
      <div class="metric-row"><span>标签：${tags}</span></div>
    `;
  }

  function renderChecklist() {
    const list = el("checklist");
    const items = [
      {
        done: state.selection.length === state.teamSize,
        text: `选满 ${state.teamSize} 名角色（${state.selection.length}/${state.teamSize}）`,
      },
      {
        done: state.bonuses.length === state.bonusPerRound,
        text: `分配 ${state.bonusPerRound} 次增益（${state.bonuses.length}/${state.bonusPerRound}）`,
      },
      {
        done: state.strategy.kind !== "tag_priority" || Boolean(state.strategy.tag),
        text: "选好攻击策略" + (state.strategy.kind === "tag_priority" ? "与目标标签" : ""),
      },
    ];
    list.innerHTML = "";
    items.forEach((item) => {
      const li = document.createElement("li");
      li.className = item.done ? "is-done" : "";
      li.textContent = item.text;
      list.appendChild(li);
    });
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
    renderMetrics();
    renderChecklist();
  }

  function renderPrepareStatus() {
    const node = el("prepare-status");
    if (state.submitted && state.opponentReady) node.textContent = "双方已提交，正在结算…";
    else if (state.submitted) node.textContent = "你已提交，等待对手…";
    else if (state.opponentReady) node.textContent = "对手已提交，等待你";
    else node.textContent = "准备中";
  }

  // ---------------------------------------------------------------- 方案与提交
  function currentPlan() {
    return {
      selection: state.selection.slice(),
      bonuses: state.bonuses.map((bonus) => ({ slot: bonus.slot, kind: bonus.kind })),
      strategy: { kind: state.strategy.kind, tag: state.strategy.tag },
    };
  }

  function submit() {
    const plan = currentPlan();
    const payload = {
      type: "submit_plan",
      selection: plan.selection,
      bonuses: plan.bonuses,
      strategy: plan.strategy,
    };
    if (NC.send(payload)) {
      el("btn-submit").disabled = true;
      el("submit-hint").textContent = "正在提交…";
      NC.audio.play("ui-confirm");
    }
  }

  function onPlanAccepted(message, auto) {
    state.submitted = true;
    if (auto && message.plan) {
      state.selection = message.plan.selection.slice();
      state.bonuses = message.plan.bonuses.map((bonus) => ({ slot: bonus.slot, kind: bonus.kind }));
      state.strategy = { kind: message.plan.strategy.kind, tag: message.plan.strategy.tag };
      NC.toast(message.reason || "已自动提交方案");
    } else {
      NC.toast("方案已提交，仍可在双方提交前修改");
    }
    render();
    el("btn-submit").textContent = "更新方案";
  }

  // ---------------------------------------------------------------- 计时
  let urgentWarned = false;

  function startTimer(seconds) {
    stopTimer();
    urgentWarned = false;
    el("timer").classList.remove("is-urgent");
    state.deadlineAt = Date.now() + seconds * 1000;
    const total = seconds * 1000;
    const tick = () => {
      const remain = Math.max(0, state.deadlineAt - Date.now());
      el("timer-text").textContent = Math.ceil(remain / 1000) + "s";
      el("timer-fill").style.width = (total ? (remain / total) * 100 : 0) + "%";
      const urgent = remain > 0 && remain <= 10000;
      el("timer").classList.toggle("is-urgent", urgent);
      if (urgent && !urgentWarned) {
        urgentWarned = true;
        NC.toast("准备时间只剩 10 秒，超时系统会随机提交方案");
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

  // ---------------------------------------------------------------- 生命周期
  /** 进入新一局（由 app.js 在 round_start 后调用）。 */
  function enterRound(message, options) {
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
    state.swapFrom = null;
    state.armedBonus = null;
    state.submitted = false;
    state.opponentReady = false;
    resetUndo();
    el("btn-submit").textContent = "提交方案";
    el("submit-hint").textContent = "";
    render();
    NC.show("screen-prepare");
    startTimer(message.deadline_seconds || 60);
    if (options && options.restored) NC.toast("已回到原来的房间，继续你的部署");
  }

  /** 断线重连后按 state_sync 恢复备战界面。 */
  function restoreFromSync(message) {
    state.roundIndex = message.round_index;
    state.pool = message.pool || [];
    state.strategies = message.strategies || [];
    state.bonusOptions = message.bonus_options || [];
    state.teamSize = message.team_size || 3;
    state.poolSize = message.pool_size || 6;
    state.bonusPerRound = message.bonus_per_round || 4;
    state.maxBonusPerFighter = message.max_bonus_per_fighter || 2;
    state.submitted = Boolean(message.submitted);
    state.opponentReady = Boolean(message.opponent_ready);
    if (message.plan) {
      state.selection = message.plan.selection.slice();
      state.bonuses = message.plan.bonuses.map((bonus) => ({ slot: bonus.slot, kind: bonus.kind }));
      state.strategy = { kind: message.plan.strategy.kind, tag: message.plan.strategy.tag };
      el("btn-submit").textContent = "更新方案";
    } else {
      state.selection = [];
      state.bonuses = [];
      state.strategy = { kind: "lowest_hp", tag: null };
      el("btn-submit").textContent = "提交方案";
    }
    resetUndo();
    render();
    NC.show("screen-prepare");
    startTimer(message.remaining_seconds || 60);
  }

  // ---------------------------------------------------------------- 拖拽（卡牌视图）
  let drag = null;
  let dragMoved = false;
  const LONG_PRESS_MS = 150;

  function bindDrag() {
    const hand = el("hand");
    const slots = el("slots");
    [hand, slots].forEach((zone) => {
      if (!zone) return;
      zone.addEventListener("pointerdown", onPointerDown);
    });
    document.addEventListener("pointermove", onPointerMove, { passive: false });
    document.addEventListener("pointerup", onPointerUp);
    document.addEventListener("pointercancel", cancelDrag);
  }

  function onPointerDown(event) {
    if (state.view !== NC.VIEW_CARDS) return;
    if (event.target.closest("button")) return;
    const cardNode = event.target.closest("[data-char-id]");
    const slotNode = event.target.closest("[data-slot]");
    if (!cardNode && !slotNode) return;
    const source = cardNode
      ? { type: "hand", charId: Number(cardNode.dataset.charId) }
      : { type: "slot", index: Number(slotNode.dataset.slot) };
    if (source.type === "slot" && !state.selection[source.index]) return;

    drag = {
      source,
      startX: event.clientX,
      startY: event.clientY,
      pointerType: event.pointerType,
      active: false,
      ghost: null,
      timer: setTimeout(() => beginDrag(event.clientX, event.clientY), LONG_PRESS_MS),
    };
  }

  function onPointerMove(event) {
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.active) {
      const moved = Math.hypot(dx, dy);
      if (drag.pointerType === "mouse" && moved > 5) {
        clearTimeout(drag.timer);
        beginDrag(event.clientX, event.clientY);
      } else if (moved > 60) {
        // 触屏上先滑动就当成滚动，不进入拖拽
        cancelDrag();
      }
      return;
    }
    event.preventDefault();
    moveGhost(event.clientX, event.clientY);
  }

  function beginDrag(x, y) {
    if (!drag || drag.active) return;
    const charId = drag.source.type === "hand" ? drag.source.charId : state.selection[drag.source.index];
    const character = card(charId);
    if (!character) return;
    drag.active = true;
    dragMoved = true;
    const ghost = document.createElement("div");
    ghost.className = "drag-ghost";
    ghost.appendChild(NC.pieceImage(character.id, character.name, "ghost-piece"));
    const label = document.createElement("span");
    label.textContent = character.name;
    ghost.appendChild(label);
    document.body.appendChild(ghost);
    drag.ghost = ghost;
    moveGhost(x, y);
    document.body.classList.add("is-dragging");
    NC.audio.play("ui-click");
  }

  function moveGhost(x, y) {
    if (!drag || !drag.ghost) return;
    drag.ghost.style.transform = `translate(${x - 34}px, ${y - 34}px) scale(1.08)`;
    highlightDropTarget(x, y);
  }

  function highlightDropTarget(x, y) {
    document.querySelectorAll(".slot.is-drop-hover").forEach((node) => node.classList.remove("is-drop-hover"));
    const target = dropTargetAt(x, y);
    if (target && target.type === "slot") {
      const node = el("slots").querySelector(`[data-slot="${target.index}"]`);
      if (node) node.classList.add("is-drop-hover");
    }
  }

  function dropTargetAt(x, y) {
    const node = document.elementFromPoint(x, y);
    if (!node) return null;
    const slotNode = node.closest("[data-slot]");
    if (slotNode) return { type: "slot", index: Number(slotNode.dataset.slot) };
    if (node.closest("#hand")) return { type: "hand" };
    return null;
  }

  function onPointerUp(event) {
    if (!drag) return;
    clearTimeout(drag.timer);
    if (!drag.active) {
      drag = null;
      return;
    }
    const target = dropTargetAt(event.clientX, event.clientY);
    const source = drag.source;
    cancelDrag();
    if (!target) return;

    if (source.type === "hand" && target.type === "slot") {
      if (target.index >= state.teamSize) return;
      const occupied = state.selection[target.index];
      if (occupied && occupied !== source.charId) {
        // 目标位已有人：换位（被换下的人回到手牌，可再次上阵）
        const from = state.selection.indexOf(source.charId);
        if (from >= 0) {
          swapSlots(from, target.index);
        } else {
          pushUndo();
          state.selection[target.index] = source.charId;
          state.selection = state.selection.filter((item) => item !== undefined && item !== null);
          const moved = normalizeFirstSlot();
          if (moved) toast(`${moved} 只能放在第一个出击位，已自动调整`);
          NC.audio.play("ui-place");
          render();
        }
      } else {
        placeAt(source.charId, target.index);
      }
      return;
    }
    if (source.type === "slot" && target.type === "slot") {
      const occupied = state.selection[target.index];
      if (!occupied) {
        pushUndo();
        const charId = state.selection.splice(source.index, 1)[0];
        const insertAt = Math.min(target.index, state.selection.length);
        state.selection.splice(insertAt, 0, charId);
        const moved = normalizeFirstSlot();
        if (moved) toast(`${moved} 只能放在第一个出击位，已自动调整`);
        NC.audio.play("ui-place");
        render();
      } else {
        swapSlots(source.index, target.index);
      }
      return;
    }
    if (source.type === "slot" && target.type === "hand") {
      removeSlot(source.index);
    }
  }

  function cancelDrag() {
    if (drag && drag.ghost) drag.ghost.remove();
    if (drag && drag.timer) clearTimeout(drag.timer);
    drag = null;
    document.body.classList.remove("is-dragging");
    document.querySelectorAll(".slot.is-drop-hover").forEach((node) => node.classList.remove("is-drop-hover"));
    // 让紧随其后的 click 事件不要误触发选中
    setTimeout(() => {
      dragMoved = false;
    }, 0);
  }

  NC.prepare = {
    render,
    enterRound,
    restoreFromSync,
    submit,
    currentPlan,
    undo,
    resetUndo,
    clearPlan,
    setStrategy,
    setStrategyTag,
    toggleView,
    setView,
    view,
    restoreView,
    updateViewToggle,
    placeAt,
    toggleCard,
    card,
    swapSlots,
    removeSlot,
    addBonus,
    startTimer,
    stopTimer,
    onPlanAccepted,
    bindDrag,
  };
})();
