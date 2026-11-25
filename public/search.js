/* 站内搜索：零依赖，索引是构建时生成的 /search.json */
(function () {
  "use strict";

  var data = null;
  var loading = null;
  var body = null; // 正文索引，打开搜索后后台加载
  var root, input, list, empty, panel;
  var lastFocus = null;

  /* ---------- DOM ---------- */

  function build() {
    root = document.createElement("div");
    root.className = "search";
    root.hidden = true;
    root.innerHTML =
      '<div class="search__backdrop" data-close></div>' +
      '<div class="search__panel" role="dialog" aria-modal="true" aria-label="搜索文章">' +
      '  <div class="search__field">' +
      '    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
      '      <circle cx="10.5" cy="10.5" r="6.5" /><path d="m19.5 19.5-4-4" />' +
      "    </svg>" +
      '    <input type="search" placeholder="搜索标题、摘要、小节…" autocomplete="off" spellcheck="false" aria-label="搜索关键词" />' +
      '    <kbd>Esc</kbd>' +
      "  </div>" +
      '  <div class="search__results" role="listbox" aria-label="搜索结果"></div>' +
      '  <p class="search__empty" hidden>没有匹配的文章</p>' +
      '  <p class="search__hint">↑↓ 选择 · Enter 打开</p>' +
      "</div>";
    document.body.appendChild(root);

    input = root.querySelector("input");
    list = root.querySelector(".search__results");
    empty = root.querySelector(".search__empty");
    panel = root.querySelector(".search__panel");

    root.addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-close")) close();
    });
    input.addEventListener("input", render);
    input.addEventListener("keydown", onKey);
  }

  /* ---------- 打开 / 关闭 ---------- */

  function open() {
    if (!root) build();
    lastFocus = document.activeElement;
    root.hidden = false;
    document.documentElement.classList.add("is-searching");
    input.value = "";
    render();
    load();
    loadBody();
    input.focus();
  }

  function close() {
    if (!root || root.hidden) return;
    root.hidden = true;
    document.documentElement.classList.remove("is-searching");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  /* ---------- 数据 ---------- */

  function load() {
    if (data || loading) return;
    loading = fetch("/search.json?v=bd3d13e7")
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        data = rows;
        render();
      })
      .catch(function () {
        data = [];
        empty.hidden = false;
        empty.textContent = "搜索索引加载失败";
      });
  }

  // 正文索引大（约 400KB），不阻塞首屏：先出元信息结果，到了再补一版
  function loadBody() {
    if (body) return;
    fetch("/search-body.json?v=9b036e0c")
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        body = rows;
        render(); // 用更全的数据重排一次
      })
      .catch(function () {});
  }

  /* ---------- 打分 ---------- */

  function score(item, q) {
    var total = 0;
    var title = item.title.toLowerCase();
    var dek = (item.dek || "").toLowerCase();
    var exc = (item.excerpt || "").toLowerCase();
    var tag = (item.tag || "").toLowerCase();
    var heads = (item.headings || []).join(" ").toLowerCase();

    // 整串命中，优先级最高
    if (title.indexOf(q) >= 0) total += title.indexOf(q) === 0 ? 120 : 80;
    if (heads.indexOf(q) >= 0) total += 40;
    if (tag.indexOf(q) >= 0) total += 40;
    if (dek.indexOf(q) >= 0) total += 35;
    if (exc.indexOf(q) >= 0) total += 25;
    if (body && body[item.url] && body[item.url].toLowerCase().indexOf(q) >= 0) {
      total += 15; // 只在正文里出现，排得比元信息命中低
    }

    // 中文没有词边界，整串搜不到时退回按字命中；
    // 英文绝不能这么做——"binder" 里的 i、n、d 几乎每篇都有，会全是噪音
    if (!/[a-z0-9]/i.test(q)) {
      var hit = 0;
      for (var i = 0; i < q.length; i++) {
        if (title.indexOf(q[i]) >= 0) hit += 1;
      }
      if (hit > 1) total += Math.round((hit / q.length) * 20);
    }
    return total;
  }

  /* ---------- 渲染 ---------- */

  function render() {
    if (!data) return;
    var q = input.value.trim().toLowerCase();
    var rows = data;

    if (q) {
      rows = data
        .map(function (item) { return { item: item, s: score(item, q) }; })
        .filter(function (r) { return r.s > 0; })
        .sort(function (a, b) { return b.s - a.s; })
        .map(function (r) { return r.item; });
    }

    list.innerHTML = "";
    empty.hidden = rows.length > 0;
    if (!rows.length) {
      empty.textContent = q ? "没有匹配的文章" : "";
      return;
    }

    rows.slice(0, 12).forEach(function (item, i) {
      var a = document.createElement("a");
      a.className = "search__item";
      a.href = item.url;
      a.setAttribute("role", "option");
      if (i === 0) a.classList.add("is-active");
      a.innerHTML =
        '<span class="search__title"></span>' +
        '<span class="search__meta"></span>';
      a.querySelector(".search__title").textContent = item.title;
      a.querySelector(".search__meta").textContent =
        [item.tag, item.dek].filter(Boolean).join(" · ");
      a.addEventListener("pointerenter", function () {
        list.querySelectorAll(".is-active").forEach(function (el) {
          el.classList.remove("is-active");
        });
        a.classList.add("is-active");
      });
      list.appendChild(a);
    });
  }

  /* ---------- 键盘 ---------- */

  function onKey(e) {
    var items = Array.prototype.slice.call(list.querySelectorAll(".search__item"));
    if (!items.length) return;
    var idx = items.findIndex(function (el) {
      return el.classList.contains("is-active");
    });

    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      idx = e.key === "ArrowDown" ? idx + 1 : idx - 1;
      if (idx < 0) idx = items.length - 1;
      if (idx >= items.length) idx = 0;
      items.forEach(function (el) { el.classList.remove("is-active"); });
      items[idx].classList.add("is-active");
      items[idx].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (items[idx]) window.location.href = items[idx].href;
    }
  }

  /* ---------- 绑定 ---------- */

  function init() {
    document.querySelectorAll("[data-search]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        open();
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        root && !root.hidden ? close() : open();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
