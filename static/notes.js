/* 批注：选中文字 → 记一条；公开的读者也能看到，私有的只有自己可见。
 *
 * 定位方式：data-block（构建时打的块序号）+ 块内字符偏移 + 原文。
 * 原文对不上时按 quote 在该块里重找，还找不到就标成「原文已变化」。
 */
(function () {
  "use strict";

  // slug 由构建时写进 <html data-slug>，不靠 URL 猜（带查询串或尾斜杠时都会猜错）
  var SLUG =
    document.documentElement.dataset.slug ||
    location.pathname.replace(/^\/posts\//, "").replace(/\/$/, "");
  var TOKEN_KEY = "notes-token";
  var root, button, drawer, listEl, toolbar, popover, compose;
  var notes = [];
  var filter = "open";
  var pending = null; // 正在撰写的位置信息

  if (!SLUG || !document.querySelector(".prose")) return;

  function token() {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch (e) {
      return "";
    }
  }
  function isAuthor() {
    return !!token();
  }

  /* ---------------------------------------------------------------- 网络 */

  function api(path, options) {
    options = options || {};
    var headers = options.headers || {};
    if (isAuthor()) headers["authorization"] = "Bearer " + token();
    if (options.body) headers["content-type"] = "application/json";
    return fetch("/api/notes" + path, {
      method: options.method || "GET",
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    }).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.headers.get("content-type").indexOf("json") >= 0
        ? res.json()
        : res.text();
    });
  }

  function loadNotes() {
    return api("?slug=" + encodeURIComponent(SLUG))
      .then(function (data) {
        notes = data.notes || [];
        if (data.author === false && isAuthor()) {
          // 令牌失效了
          localStorage.removeItem(TOKEN_KEY);
          location.reload();
        }
        return notes;
      })
      .catch(function () {
        notes = [];
        return notes;
      });
  }

  /* ---------------------------------------------------------------- 定位 */

  function textNodesOf(block) {
    var walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue) return NodeFilter.FILTER_REJECT;
        if (node.parentElement && node.parentElement.closest(".note-mark")) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    var nodes = [];
    var node;
    while ((node = walker.nextNode())) nodes.push(node);
    return nodes;
  }

  function blockText(block) {
    return textNodesOf(block)
      .map(function (n) {
        return n.nodeValue;
      })
      .join("");
  }

  function rangeFromOffsets(block, start, end) {
    var nodes = textNodesOf(block);
    var range = document.createRange();
    var acc = 0;
    var started = false;
    for (var i = 0; i < nodes.length; i++) {
      var len = nodes[i].nodeValue.length;
      if (!started && acc + len >= start) {
        range.setStart(nodes[i], start - acc);
        started = true;
      }
      if (started && acc + len >= end) {
        range.setEnd(nodes[i], end - acc);
        return range;
      }
      acc += len;
    }
    return started ? null : null;
  }

  function offsetsOfSelection(block, range) {
    var pre = document.createRange();
    pre.selectNodeContents(block);
    pre.setEnd(range.startContainer, range.startOffset);
    var start = pre.toString().length;
    return { start: start, end: start + range.toString().length };
  }

  function resolve(note) {
    var block = document.querySelector('[data-block="' + note.block + '"]');
    if (!block) return { block: null, range: null, stale: true };
    var text = blockText(block);
    var start = note.start;
    var end = note.end;
    if (text.slice(start, end) !== note.quote) {
      var at = text.indexOf(note.quote);
      if (at < 0) return { block: block, range: null, stale: true };
      start = at;
      end = at + note.quote.length;
    }
    var range = rangeFromOffsets(block, start, end);
    return { block: block, range: range, stale: !range };
  }

  /* ---------------------------------------------------------------- 高亮 */

  function paint(notes) {
    document.querySelectorAll(".note-mark").forEach(function (m) {
      var parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m);
      parent.normalize();
    });

    notes.forEach(function (note) {
      var got = resolve(note);
      if (!got.range) return;
      var mark = document.createElement("mark");
      mark.className =
        "note-mark note-mark--" + note.visibility + " note-mark--" + note.status;
      mark.dataset.noteId = note.id;
      mark.title = note.body;
      try {
        var frag = got.range.extractContents();
        mark.appendChild(frag);
        got.range.insertNode(mark);
      } catch (e) {
        /* 跨块选区之类，跳过 */
      }
    });
  }

  /* ---------------------------------------------------------------- 侧栏 */

  function statusLabel(s) {
    return { open: "待处理", applied: "已处理", dismissed: "不改" }[s] || s;
  }

  function renderList() {
    if (!listEl) return;
    listEl.innerHTML = "";
    var shown = notes.filter(function (n) {
      return filter === "all" || n.status === filter;
    });
    if (!shown.length) {
      listEl.innerHTML = '<p class="notes__empty">这里还没有批注</p>';
      return;
    }
    shown.forEach(function (note) {
      var got = resolve(note);
      var el = document.createElement("article");
      el.className = "note-card" + (got.stale ? " is-stale" : "");
      el.innerHTML =
        '<div class="note-card__head">' +
        '<span class="note-card__pos">第 ' + (note.block + 1) + " 块</span>" +
        '<span class="note-card__vis">' +
        (note.visibility === "public" ? "公开" : "私有") +
        "</span>" +
        (got.stale ? '<span class="note-card__stale">原文已变化</span>' : "") +
        "</div>" +
        '<blockquote class="note-card__quote"></blockquote>' +
        '<p class="note-card__body"></p>' +
        (note.resolution ? '<p class="note-card__resolution"></p>' : "") +
        '<div class="note-card__actions">' +
        (note.status !== "applied"
          ? '<button type="button" data-act="applied">标记已处理</button>'
          : "") +
        (note.status !== "dismissed"
          ? '<button type="button" data-act="dismissed">不改</button>'
          : "") +
        (note.status !== "open"
          ? '<button type="button" data-act="open">重新打开</button>'
          : "") +
        '<button type="button" data-act="edit">编辑</button>' +
        '<button type="button" data-act="delete" class="is-danger">删除</button>' +
        "</div>";
      el.querySelector(".note-card__quote").textContent = note.quote;
      el.querySelector(".note-card__body").textContent = note.body;
      if (note.resolution) {
        el.querySelector(".note-card__resolution").textContent = "处理：" + note.resolution;
      }

      el.addEventListener("click", function (ev) {
        var act = ev.target.getAttribute("data-act");
        if (!act) {
          if (got.range) {
            got.range.startContainer.parentElement.scrollIntoView({
              block: "center",
              behavior: "smooth",
            });
          }
          return;
        }
        ev.stopPropagation();
        if (act === "delete") {
          if (!confirm("删除这条批注？")) return;
          api("/" + note.id, { method: "DELETE" }).then(refresh);
          return;
        }
        if (act === "edit") {
          var body = prompt("修改批注内容", note.body);
          if (body === null || !body.trim()) return;
          api("/" + note.id, { method: "PATCH", body: { body: body } }).then(refresh);
          return;
        }
        var patch = { status: act };
        if (act === "applied") {
          var res = prompt("改了什么？（一句话，可留空）", note.resolution || "");
          if (res) patch.resolution = res;
        }
        api("/" + note.id, { method: "PATCH", body: patch }).then(refresh);
      });

      listEl.appendChild(el);
    });
  }

  function refresh() {
    return loadNotes().then(function () {
      paint(notes);
      renderList();
      updateBadge();
    });
  }

  function updateBadge() {
    if (!button) return;
    var open = notes.filter(function (n) {
      return n.status === "open";
    }).length;
    button.textContent = open ? "批注 " + open : "批注";
    button.classList.toggle("has-open", open > 0);
  }

  /* ---------------------------------------------------------------- 撰写 */

  function buildUI() {
    button = document.createElement("button");
    button.type = "button";
    button.className = "notes-fab";
    button.textContent = "批注";
    button.addEventListener("click", function () {
      drawer.classList.toggle("is-open");
    });
    document.body.appendChild(button);

    drawer = document.createElement("aside");
    drawer.className = "notes-drawer";
    drawer.innerHTML =
      '<header class="notes-drawer__head">' +
      "<h2>批注</h2>" +
      '<div class="notes-drawer__tools">' +
      '<button type="button" data-filter="open" class="is-on">待处理</button>' +
      '<button type="button" data-filter="applied">已处理</button>' +
      '<button type="button" data-filter="all">全部</button>' +
      "</div>" +
      '<button type="button" class="notes-drawer__close" aria-label="关闭">×</button>' +
      "</header>" +
      '<div class="notes-drawer__list"></div>' +
      '<footer class="notes-drawer__foot">' +
      '<a class="notes-export" href="/api/notes?slug=' +
      encodeURIComponent(SLUG) +
      '&format=md" target="_blank" rel="noopener">导出 Markdown</a>' +
      '<button type="button" class="notes-signout">退出批注模式</button>' +
      "</footer>";
    document.body.appendChild(drawer);
    listEl = drawer.querySelector(".notes-drawer__list");

    drawer.querySelector(".notes-drawer__close").addEventListener("click", function () {
      drawer.classList.remove("is-open");
    });
    drawer.querySelectorAll("[data-filter]").forEach(function (b) {
      b.addEventListener("click", function () {
        filter = b.dataset.filter;
        drawer.querySelectorAll("[data-filter]").forEach(function (x) {
          x.classList.toggle("is-on", x === b);
        });
        renderList();
      });
    });
    drawer.querySelector(".notes-export").addEventListener("click", function (e) {
      // 导出要带令牌，走 fetch 再存
      e.preventDefault();
      api("?slug=" + encodeURIComponent(SLUG) + "&format=md").then(function (text) {
        var blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = SLUG + "-notes.md";
        a.click();
        URL.revokeObjectURL(a.href);
      });
    });
    drawer.querySelector(".notes-signout").addEventListener("click", function () {
      localStorage.removeItem(TOKEN_KEY);
      location.reload();
    });

    toolbar = document.createElement("div");
    toolbar.className = "notes-toolbar";
    toolbar.hidden = true;
    toolbar.innerHTML = '<button type="button">＋ 批注</button>';
    document.body.appendChild(toolbar);
    toolbar.addEventListener("mousedown", function (e) {
      e.preventDefault(); // 别让选区丢掉
    });
    toolbar.querySelector("button").addEventListener("click", openCompose);
  }

  function openCompose() {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    var range = sel.getRangeAt(0);
    var block = range.startContainer.parentElement.closest("[data-block]");
    if (!block) return;
    if (!block.contains(range.endContainer)) {
      alert("批注只能选在同一段里");
      return;
    }
    var off = offsetsOfSelection(block, range);
    var quote = range.toString().trim();
    if (!quote) return;

    pending = {
      block: Number(block.dataset.block),
      start: off.start,
      end: off.end,
      quote: quote,
      prefix: blockText(block).slice(Math.max(0, off.start - 30), off.start),
      suffix: blockText(block).slice(off.end, off.end + 30),
    };

    if (!popover) {
      popover = document.createElement("div");
      popover.className = "notes-popover";
      popover.innerHTML =
        '<p class="notes-popover__quote"></p>' +
        '<textarea rows="4" placeholder="想怎么改？"></textarea>' +
        '<label class="notes-popover__vis">' +
        '<input type="checkbox" /> 公开这条（读者可见）' +
        "</label>" +
        '<div class="notes-popover__actions">' +
        '<button type="button" data-act="cancel">取消</button>' +
        '<button type="button" data-act="save" class="is-primary">保存</button>' +
        "</div>";
      document.body.appendChild(popover);
      popover.querySelector('[data-act="cancel"]').addEventListener("click", function () {
        popover.hidden = true;
      });
      popover.querySelector('[data-act="save"]').addEventListener("click", saveCompose);
    }
    popover.querySelector(".notes-popover__quote").textContent = quote;
    popover.querySelector("textarea").value = "";
    popover.querySelector('input[type="checkbox"]').checked = false;
    popover.hidden = false;

    var rect = range.getBoundingClientRect();
    var top = window.scrollY + rect.bottom + 10;
    var left = window.scrollX + rect.left;
    popover.style.top = top + "px";
    popover.style.left = Math.min(left, window.innerWidth - 340) + "px";
    popover.querySelector("textarea").focus();
    toolbar.hidden = true;
  }

  function saveCompose() {
    if (!pending) return;
    var body = popover.querySelector("textarea").value.trim();
    if (!body) {
      popover.querySelector("textarea").focus();
      return;
    }
    var visibility = popover.querySelector('input[type="checkbox"]').checked
      ? "public"
      : "private";
    var payload = Object.assign({ slug: SLUG, body: body, visibility: visibility }, pending);
    popover.hidden = true;
    api("", { method: "POST", body: payload }).then(function () {
      window.getSelection().removeAllRanges();
      refresh();
    });
  }

  /* ---------------------------------------------------------------- 选区 */

  function onSelection() {
    if (!toolbar || !isAuthor() || !popover || popover.hidden) {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) {
        if (toolbar) toolbar.hidden = true;
        return;
      }
      var range = sel.getRangeAt(0);
      var block = range.startContainer.parentElement;
      block = block && block.closest("[data-block]");
      if (!block || !block.closest(".prose")) {
        toolbar.hidden = true;
        return;
      }
      if (!block.contains(range.endContainer)) {
        toolbar.hidden = true;
        return;
      }
      var rect = range.getBoundingClientRect();
      if (!rect.width) {
        toolbar.hidden = true;
        return;
      }
      toolbar.hidden = false;
      toolbar.style.top = window.scrollY + rect.top - 44 + "px";
      toolbar.style.left =
        window.scrollX + Math.min(rect.left, window.innerWidth - 120) + "px";
    }
  }

  /* ---------------------------------------------------------------- 读者视角 */

  function renderPublicList() {
    var pub = notes.filter(function (n) {
      return n.visibility === "public";
    });
    if (!pub.length) return;
    var prose = document.querySelector(".prose");
    var box = document.createElement("section");
    box.className = "public-notes";
    box.innerHTML =
      "<h2>作者批注</h2><ol>" +
      pub
        .map(function (n) {
          return (
            "<li><blockquote>" +
            escapeHtml(n.quote) +
            "</blockquote><p>" +
            escapeHtml(n.body) +
            "</p></li>"
          );
        })
        .join("") +
      "</ol>";
    prose.appendChild(box);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---------------------------------------------------------------- 启动 */

  function promptToken() {
    var value = prompt("输入批注令牌（只存这台浏览器）");
    if (!value) return false;
    localStorage.setItem(TOKEN_KEY, value.trim());
    return true;
  }

  function boot() {
    if (/[?&]notes(=|&|$)/.test(location.search) && !isAuthor()) {
      if (promptToken()) location.replace(location.pathname);
      return;
    }

    if (isAuthor()) {
      buildUI();
      document.addEventListener("selectionchange", onSelection);
      document.addEventListener("mouseup", onSelection);
    }

    loadNotes().then(function () {
      paint(notes);
      if (isAuthor()) {
        renderList();
        updateBadge();
      } else {
        renderPublicList();
      }
    });

    document.addEventListener("click", function (e) {
      var mark = e.target.closest && e.target.closest(".note-mark");
      if (!mark) return;
      var id = Number(mark.dataset.noteId);
      var note = notes.filter(function (n) {
        return n.id === id;
      })[0];
      if (!note) return;
      if (isAuthor()) {
        drawer.classList.add("is-open");
        renderList();
      } else {
        alert(note.body);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
