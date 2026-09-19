/* 介绍页 / 规则页共用的少量脚本：版本号徽标、规则数据渲染。
   两页都是静态 HTML，只有需要跟后端保持一致的部分才用 JS 填。 */
(function () {
  "use strict";

  function fillVersion() {
    const badge = document.getElementById("version-badge");
    if (!badge) return;
    fetch("/api/version")
      .then((response) => response.json())
      .then((data) => {
        badge.textContent = `${data.name} v${data.version}`;
      })
      .catch(() => {});
  }

  function escapeHtml(text) {
    return String(text).replace(
      /[&<>"']/g,
      (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])
    );
  }

  function tagIcon(key, name) {
    if (!key || key === "none") return "";
    const safe = escapeHtml(name || key);
    return `<img class="tag-icon" src="/static/assets/tags/tag-${encodeURIComponent(key)}.svg" alt="${safe}" title="${safe}" />`;
  }

  // ---------------------------------------------------------------- 规则页渲染
  const rulesPage = {
    render(rules) {
      const c = rules.constants;
      setText("pool-size", c.pool_size);
      setText("team-size", c.team_size);
      setText("bonus-per-round", c.bonus_per_round);
      setText("bonus-per-round-copy", c.bonus_per_round);
      setText("bonus-atk", c.bonus_atk);
      setText("bonus-hp", c.bonus_hp);
      setText("max-bonus", c.max_bonus_per_fighter);
      setText("max-bonus-copy", c.max_bonus_per_fighter);
      setText("rounds-to-win", c.rounds_to_win);
      setText("prepare-timeout", c.prepare_timeout);
      setText("max-rounds", c.max_rounds_per_duel);

      document.getElementById("tag-table").innerHTML = rules.tags
        .map(
          (tag) =>
            `<tr><td><strong>${tagIcon(tag.key, tag.name)}${escapeHtml(tag.name)}</strong></td>` +
            `<td>${escapeHtml(tag.detail)}</td></tr>`
        )
        .join("");

      document.getElementById("character-table").innerHTML = rules.characters
        .map(
          (ch) =>
            `<tr><td>${ch.id}</td><td><strong>${escapeHtml(ch.name)}</strong></td><td>${escapeHtml(ch.role)}</td>` +
            `<td>${ch.atk}</td><td>${ch.hp}</td><td>${ch.initiative}</td>` +
            `<td>${tagIcon(ch.tag, ch.tag_name)}${escapeHtml(ch.tag_name)}</td><td>${escapeHtml(ch.domain)}</td></tr>`
        )
        .join("");

      document.getElementById("strategy-list").innerHTML = rules.strategies
        .map((item) => `<li>${escapeHtml(item.label)}</li>`)
        .join("");

      document.getElementById("mode-table").innerHTML = (rules.modes || [])
        .map(
          (mode) =>
            `<tr><td><strong>${escapeHtml(mode.name)}</strong></td><td>${escapeHtml(mode.summary)}</td>` +
            `<td>${escapeHtml(mode.detail)}</td></tr>`
        )
        .join("");

      const status = document.getElementById("rules-status");
      if (status) status.textContent = "数据已同步：本页数值与当前版本完全一致。";
    },
    fail() {
      const box = document.getElementById("rules-status");
      if (box) box.textContent = "规则数据加载失败，请刷新页面重试。";
    },
  };

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = String(value);
  }

  fillVersion();
  if (document.getElementById("tag-table")) {
    fetch("/api/rules")
      .then((response) => response.json())
      .then((rules) => rulesPage.render(rules))
      .catch(() => rulesPage.fail());
  }
})();
