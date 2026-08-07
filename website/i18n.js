/**
 * SourceCraft 官网 i18n —— JS 运行时多语言
 *
 * 设计（与工具端 i18n.py 同理念）：
 *   - key = 中文原文；zh 表恒为空（原文即 key，回退中文）
 *   - 默认语言 = en（英文翻译表必须完整）
 *   - 未命中 key 回退中文原文，永不崩
 *
 * 用法：
 *   - 文本节点：<span data-i18n="中文原文">中文原文</span>
 *   - HTML 富文本：<div data-i18n-html="key">…原始内容…</div>
 *   - 属性：<input data-i18n-attr="placeholder|title|aria-label" placeholder="中文">
 *   - 动态 JS 文本：I18N.t("中文原文")
 *
 * 加载顺序：zh.js / en.js（定义 window.I18N_TABLE）→ i18n.js（本文件）
 */
(function () {
  "use strict";

  var STORAGE_KEY = "sourcecraft_lang_v2"; // v2：隔离旧版可能写入的脏值
  var SUPPORTED = ["zh", "en"]; // 顺序即切换器顺序
  var DEFAULT_LANG = "en"; // 默认英文

  var current = null;

  /** URL 参数 ?lang=xx 优先（诊断/分享/SEO 用） */
  function getUrlLang() {
    try {
      var m = (location.search || "").match(/[?&]lang=([a-zA-Z_-]+)/);
      return m ? m[1].toLowerCase() : "";
    } catch (e) {
      return "";
    }
  }

  function getStored() {
    try {
      var v = localStorage.getItem(STORAGE_KEY);
      return v || "";
    } catch (e) {
      return "";
    }
  }

  function detect() {
    var urlLang = getUrlLang();
    if (urlLang && SUPPORTED.indexOf(urlLang) >= 0) return urlLang;
    var stored = getStored();
    if (stored && SUPPORTED.indexOf(stored) >= 0) return stored;
    return DEFAULT_LANG;
  }

  function table() {
    var all = (window.I18N_TABLES && typeof window.I18N_TABLES === "object")
      ? window.I18N_TABLES
      : {};
    var tbl = all[current] || {};
    return (typeof tbl === "object") ? tbl : {};
  }

  /** 翻译：查表命中返回译文；未命中返回原文（中文）；非法 key 返回 key 本身 */
  function t(key) {
    if (key == null) return key;
    var tbl = table();
    if (Object.prototype.hasOwnProperty.call(tbl, key)) {
      return tbl[key];
    }
    return key;
  }

  function setLang(lang) {
    if (SUPPORTED.indexOf(lang) < 0) lang = DEFAULT_LANG;
    current = lang;
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch (e) { /* ignore */ }
    // 用户手动选择后清除 URL 参数影响（history.replaceState 保留页面状态）
    try {
      if (getUrlLang()) {
        var u = new URL(location.href);
        u.searchParams.delete("lang");
        history.replaceState(null, "", u.toString());
      }
    } catch (e) { /* ignore */ }
    document.documentElement.setAttribute("lang", lang === "zh" ? "zh-CN" : "en");
    applyAll();
    updateSwitcher();
  }

  function getLang() {
    return current;
  }

  /** 替换单个元素文本（保留子元素？不——纯文本节点语义，直接设 textContent） */
  function applyText(el) {
    var key = el.getAttribute("data-i18n");
    if (!key) return;
    el.textContent = t(key);
  }

  /** 替换富文本 HTML 内容 */
  function applyHtml(el) {
    var key = el.getAttribute("data-i18n-html");
    if (!key) return;
    el.innerHTML = t(key);
  }

  /** 替换属性（data-i18n-attr="attr1|attr2"） */
  function applyAttrs(el) {
    var spec = el.getAttribute("data-i18n-attr");
    if (!spec) return;
    var key = el.getAttribute("data-i18n-attr-key");
    if (!key) return;
    var translated = t(key);
    spec.split("|").forEach(function (attr) {
      attr = attr.trim();
      if (attr) el.setAttribute(attr, translated);
    });
  }

  function applyAll() {
    var els = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < els.length; i++) applyText(els[i]);
    els = document.querySelectorAll("[data-i18n-html]");
    for (var j = 0; j < els.length; j++) applyHtml(els[j]);
    els = document.querySelectorAll("[data-i18n-attr]");
    for (var k = 0; k < els.length; k++) applyAttrs(els[k]);
    // 更新 <title> 与 meta description（若标记）
    var titleEl = document.querySelector("title");
    if (titleEl && titleEl.getAttribute("data-i18n")) {
      titleEl.textContent = t(titleEl.getAttribute("data-i18n"));
    }
    document.title = document.title || "";
    // meta[name=description] / meta[property^=og:] / meta[name^=twitter:]
    var metas = document.querySelectorAll("meta[data-i18n]");
    for (var mi = 0; mi < metas.length; mi++) {
      var meta = metas[mi];
      var k = meta.getAttribute("data-i18n");
      if (k) meta.setAttribute("content", t(k));
    }
    // 触发自定义事件，供 script.js 等做动态内容翻译
    document.dispatchEvent(new CustomEvent("i18n:changed", { detail: { lang: current } }));
  }

  /** 构建/更新语言切换器（自定义下拉：按钮 + 弹出列表） */
  function buildSwitcher() {
    var container = document.getElementById("lang-switcher");
    if (!container) return;
    if (container.getAttribute("data-built")) return;
    container.setAttribute("data-built", "1");

    // 按钮
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "lang-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = '<span class="lang-btn-globe">🌐</span><span class="lang-btn-label"></span><span class="lang-btn-caret">▾</span>';

    // 下拉列表
    var menu = document.createElement("ul");
    menu.className = "lang-menu";
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", "Language");
    SUPPORTED.forEach(function (code) {
      var item = document.createElement("li");
      item.className = "lang-menu-item";
      item.setAttribute("role", "option");
      item.setAttribute("data-lang", code);
      item.textContent = code === "zh" ? "中文" : "English";
      item.addEventListener("click", function () {
        setLang(code);
        closeMenu();
      });
      menu.appendChild(item);
    });

    function openMenu() {
      menu.classList.add("open");
      btn.setAttribute("aria-expanded", "true");
    }
    function closeMenu() {
      menu.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
    }
    function toggleMenu() {
      if (menu.classList.contains("open")) closeMenu();
      else openMenu();
    }

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      toggleMenu();
    });
    // 点击外部关闭
    document.addEventListener("click", function (e) {
      if (!container.contains(e.target)) closeMenu();
    });
    // Esc 关闭
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMenu();
    });

    container.appendChild(btn);
    container.appendChild(menu);
    updateSwitcher();
  }

  function updateSwitcher() {
    var container = document.getElementById("lang-switcher");
    if (!container) return;
    var btn = container.querySelector("button.lang-btn");
    if (btn) {
      var label = btn.querySelector(".lang-btn-label");
      if (label) label.textContent = current === "zh" ? "中文" : "English";
    }
    var items = container.querySelectorAll(".lang-menu-item");
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (it.getAttribute("data-lang") === current) {
        it.classList.add("active");
        it.setAttribute("aria-selected", "true");
      } else {
        it.classList.remove("active");
        it.setAttribute("aria-selected", "false");
      }
    }
  }

  // 初始化
  function init() {
    current = detect();
    document.documentElement.setAttribute("lang", current === "zh" ? "zh-CN" : "en");
    applyAll();
    buildSwitcher();
  }

  // DOM ready 时初始化；若 DOM 已就绪立即执行
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // 暴露全局 API
  window.I18N = {
    t: t,
    setLang: setLang,
    getLang: getLang,
    applyAll: applyAll,
  };
})();
