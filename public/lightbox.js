/* 图片放大：点文章里的图，铺满视口看细节，再点一下切成原始尺寸 */
(function () {
  "use strict";

  var root, img, caption, panel;
  var lastFocus = null;

  function build() {
    root = document.createElement("div");
    root.className = "lightbox";
    root.hidden = true;
    root.innerHTML =
      '<div class="lightbox__backdrop" data-close></div>' +
      '<figure class="lightbox__panel">' +
      '  <div class="lightbox__bar">' +
      '    <span class="lightbox__hint">点图放大到原始尺寸 · Esc 关闭</span>' +
      '    <button class="lightbox__close" type="button" data-close aria-label="关闭">' +
      '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
      '        <path d="M6 6l12 12M18 6L6 18" />' +
      "      </svg>" +
      "    </button>" +
      "  </div>" +
      '  <div class="lightbox__stage"><img alt="" /></div>' +
      '  <figcaption class="lightbox__caption"></figcaption>' +
      "</figure>";
    document.body.appendChild(root);

    img = root.querySelector("img");
    caption = root.querySelector(".lightbox__caption");
    panel = root.querySelector(".lightbox__panel");

    root.addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-close")) close();
    });
    // 再点图切换「适应窗口 / 原始尺寸」
    img.addEventListener("click", function () {
      root.classList.toggle("is-actual");
      if (!root.classList.contains("is-actual")) {
        root.querySelector(".lightbox__stage").scrollTo(0, 0);
      }
    });
  }

  function open(src, alt, text) {
    if (!root) build();
    lastFocus = document.activeElement;
    img.src = src;
    img.alt = alt || "";
    caption.textContent = text || alt || "";
    caption.hidden = !(text || alt);
    root.classList.remove("is-actual");
    root.hidden = false;
    document.documentElement.classList.add("is-lightboxed");
    root.querySelector(".lightbox__close").focus();
  }

  function close() {
    if (!root || root.hidden) return;
    root.hidden = true;
    root.classList.remove("is-actual");
    document.documentElement.classList.remove("is-lightboxed");
    img.removeAttribute("src");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function init() {
    var figures = document.querySelectorAll(".prose-figure");
    Array.prototype.forEach.call(figures, function (fig) {
      var image = fig.querySelector("img");
      if (!image) return;
      var cap = fig.querySelector("figcaption");
      image.classList.add("is-zoomable");
      image.setAttribute("tabindex", "0");
      image.setAttribute("role", "button");
      image.setAttribute("aria-label", "放大查看图片");
      function fire() {
        open(image.currentSrc || image.src, image.alt, cap ? cap.textContent.trim() : "");
      }
      image.addEventListener("click", fire);
      image.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          fire();
        }
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
