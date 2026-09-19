/* 星陨竞技场 · 音效层
   素材：Kenney.nl 的 CC0 音效包（Interface / Impact / Sci-Fi / RPG / 8-Bit Jingles），
   见 frontend/assets/audio/LICENSE-Kenney.txt。 */
(function () {
  "use strict";

  const NC = window.NC;
  const BASE = `${NC.ASSET_BASE}/audio`;

  // 名称 → 文件：调用方只关心"是什么事"，不关心素材细节
  const SOUNDS = {
    "ui-click": "ui-click.ogg", // 点选卡牌
    "ui-select": "ui-select.ogg", // 切换到某个模式 / 选中
    "ui-place": "ui-place.ogg", // 上阵、放增益
    "ui-remove": "ui-remove.ogg", // 下阵、撤销
    "ui-confirm": "ui-confirm.ogg", // 提交方案
    "ui-error": "ui-error.ogg", // 非法操作
    "ui-round": "ui-round.ogg", // 回合横幅
    "hit-light": "hit-light.ogg", // 普通攻击
    "hit-heavy": "hit-heavy.ogg", // 重击 / 重装触发
    "hit-pierce": "hit-pierce.ogg", // 箭矢 / 穿透
    explosion: "explosion.ogg", // 自爆 / 对撞
    shatter: "shatter.ogg", // 阵亡碎裂
    heal: "heal.ogg", // 治疗 / 复活
    poison: "shatter.ogg", // 中毒结算（复用碎裂音，短促）
    aoe: "aoe.ogg", // 群伤
    victory: "victory.ogg", // 整场胜利
    defeat: "defeat.ogg", // 整场失利
  };

  // 各音效的相对音量与最小间隔（避免 5v5 同帧叠成噪音）
  const MIX = {
    "ui-click": [0.45, 30],
    "ui-select": [0.5, 40],
    "ui-place": [0.6, 40],
    "ui-remove": [0.5, 40],
    "ui-confirm": [0.6, 80],
    "ui-error": [0.45, 150],
    "ui-round": [0.5, 200],
    "hit-light": [0.5, 45],
    "hit-heavy": [0.6, 60],
    "hit-pierce": [0.45, 60],
    explosion: [0.7, 120],
    shatter: [0.5, 80],
    heal: [0.5, 80],
    poison: [0.35, 120],
    aoe: [0.6, 150],
    victory: [0.7, 500],
    defeat: [0.6, 500],
  };

  let enabled = NC.store.get(NC.SOUND_KEY, "on") !== "off";
  let unlocked = false;
  const lastPlayed = {};

  function audioFactory() {
    return typeof window.Audio === "function" ? window.Audio : null;
  }

  /** 浏览器要求先有一次用户交互才允许播放声音。 */
  function unlock() {
    if (unlocked) return;
    const AudioCtor = audioFactory();
    if (!AudioCtor) return;
    unlocked = true;
    // 预加载全部音效，避免第一次播放时卡顿
    Object.values(SOUNDS).forEach((file) => {
      const audio = new AudioCtor(`${BASE}/${file}`);
      audio.preload = "auto";
    });
  }

  function play(name, options) {
    if (!enabled || !unlocked) return;
    const file = SOUNDS[name];
    const AudioCtor = audioFactory();
    if (!file || !AudioCtor) return;

    const [baseVolume, minGap] = MIX[name] || [0.6, 0];
    const now = Date.now();
    if (minGap && lastPlayed[name] && now - lastPlayed[name] < minGap) return;
    lastPlayed[name] = now;

    const volume = Math.max(0, Math.min(1, (options && options.volume !== undefined ? options.volume : 1) * baseVolume));
    const audio = new AudioCtor(`${BASE}/${file}`);
    audio.volume = volume;
    if (options && options.rate) audio.playbackRate = options.rate;
    const promise = audio.play();
    if (promise && promise.catch) promise.catch(() => {});
  }

  function setEnabled(on) {
    enabled = Boolean(on);
    NC.store.set(NC.SOUND_KEY, enabled ? "on" : "off");
    updateToggle();
  }

  function updateToggle() {
    const node = NC.el("btn-sound");
    if (node) node.textContent = enabled ? "🔊 音效" : "🔇 静音";
  }

  NC.audio = {
    play,
    unlock,
    setEnabled,
    isEnabled: () => enabled,
    updateToggle,
    names: () => Object.keys(SOUNDS),
  };
})();
