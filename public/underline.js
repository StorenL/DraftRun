/* 下划线从鼠标进入的位置向两侧展开。
 *
 * 三个地方共用这一套：首页文章标题、顶部导航、归档页的行标题。
 * CSS 读 --ox（0~100%），这里在指针进入时按位置写进去。
 */
(function () {
  "use strict";

  var SELECTOR = ".post-item h3 a, .nav a, .archive__name";

  function attach() {
    document.querySelectorAll(SELECTOR).forEach(function (el) {
      if (el.__underlineBound) return;
      el.__underlineBound = true;
      el.addEventListener("pointerenter", function (e) {
        var rect = el.getBoundingClientRect();
        if (!rect.width) return;
        var ox = ((e.clientX - rect.left) / rect.width) * 100;
        el.style.setProperty("--ox", Math.max(0, Math.min(100, ox)) + "%");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
