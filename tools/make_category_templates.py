#!/usr/bin/env python3
"""从 archive.html 骨架派生出分类页与分类索引页的模板。

两个页面共用一套结构：标签 + 大标题 + 一行说明 + 文章列表。
分类索引页把"文章列表"换成"分类列表"，所以只需要一个模板加一个占位区。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "templates"

archive = (TPL / "archive.html").read_text(encoding="utf-8")

main_start = archive.index('<main id="main">')
main_end = archive.index("</main>") + len("</main>")
head, tail = archive[:main_start], archive[main_end:]


def make(name, title, canonical):
    h = head.replace("<title>全部文章 · 潦草地</title>", f"<title>{title} · 潦草地</title>")
    h = h.replace('content="潦草地的全部文章，按发布时间排列。"', '{{DESC}}')
    h = h.replace('<link rel="canonical" href="https://draft.run/archive" />',
                  f'<link rel="canonical" href="https://draft.run/{canonical}" />')
    h = h.replace('<meta property="og:title" content="全部文章 · 潦草地" />',
                  f'<meta property="og:title" content="{title} · 潦草地" />')
    h = h.replace('<meta property="og:url" content="https://draft.run/archive" />',
                  f'<meta property="og:url" content="https://draft.run/{canonical}" />')
    main = f'''<main id="main">
      <section class="archive">
        <div class="shell">
          <header class="archive__head">
            <p class="label">{{{{LABEL}}}}</p>
            <h1 class="archive__title">{{{{HEADING}}}}</h1>
            <p class="archive__count">{{{{SUB}}}}</p>
          </header>
          <!--CHIPS-->
          <div class="archive__list">
            <!--ROWS-->
          </div>
        </div>
      </section>
    </main>'''
    (TPL / name).write_text(h + main + tail, encoding="utf-8", newline="\n")
    print(f"  templates/{name}")


make("categories.html", "分类", "categories")
make("category.html", "分类", "categories")
