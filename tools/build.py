#!/usr/bin/env python3
"""潦草地的静态站构建脚本。

只依赖 Python 标准库。把 content/posts/*.md 渲染成 public/posts/<slug>.html，
并重新生成首页列表、sitemap.xml、rss.xml。

用法：
    python tools/build.py
    python tools/build.py --new          新建一篇草稿
"""

import html
import json
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "posts"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
PUBLIC = ROOT / "public"
SITE = "https://draft.run"

# ---------------------------------------------------------------- front matter


def split_front_matter(text: str):
    """拆出 --- 之间的元数据和正文。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        raise SystemExit("缺少 front matter：文件开头要有 --- 包住的元数据")
    meta = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, m.group(2)


# ---------------------------------------------------------------- 行内语法

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def inline(text: str) -> str:
    """处理行内标记。先抽出代码片段，避免里面的符号被当成标记。"""
    stash = []

    def keep(fragment):
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    text = IMAGE.sub(lambda m: keep(
        f'<img src="{m.group(2)}" alt="{html.escape(m.group(1))}" loading="lazy" />'), text)
    text = INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = html.escape(text)
    text = BOLD.sub(r"<strong>\1</strong>", text)
    text = ITALIC.sub(r"<em>\1</em>", text)
    text = LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    for i, fragment in enumerate(stash):
        text = text.replace(f"\x00{i}\x00", fragment)
    return text


# ---------------------------------------------------------------- 代码高亮

KEYWORDS = {
    "kotlin": {"fun", "val", "var", "class", "object", "interface", "data", "if", "else",
               "when", "for", "while", "return", "import", "package", "private", "public",
               "internal", "override", "suspend", "null", "true", "false", "is", "in",
               "by", "lateinit", "companion", "init", "sealed", "open", "abstract"},
    "java": {"public", "private", "protected", "class", "interface", "void", "new",
             "return", "if", "else", "for", "while", "final", "static", "null", "true", "false"},
    "js": {"const", "let", "var", "function", "return", "if", "else", "for", "while",
           "class", "new", "this", "import", "export", "from", "async", "await", "null",
           "true", "false", "typeof"},
    "bash": {"cd", "ls", "git", "npm", "pnpm", "python", "curl", "echo", "export"},
}

TOK = re.compile(
    r"(?P<comment>/\*.*?\*/|//[^\n]*|#[^\n]*)"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)"
    r"|(?P<number>\b\d+(?:\.\d+)?(?:ch|px|rem|em|ms|s|%|f|dp|sp)?\b)"
    r"|(?P<word>[A-Za-z_$][\w$-]*)"
    r"|(?P<sel>[.:#][\w-]+)",
    re.S,
)


def highlight(code: str, lang: str) -> list:
    """按行返回 HTML，每行包在 <span class="line"> 里。"""
    lang = (lang or "").lower()
    keywords = KEYWORDS.get(lang, set())
    out = []
    for raw in code.split("\n"):
        pieces = []
        pos = 0
        in_block_comment = False
        for m in TOK.finditer(raw):
            if m.start() > pos:
                pieces.append(html.escape(raw[pos:m.start()]))
            kind = m.lastgroup
            text = m.group()
            if kind == "comment":
                pieces.append(f'<span class="tok-com">{html.escape(text)}</span>')
            elif kind == "string":
                pieces.append(f'<span class="tok-str">{html.escape(text)}</span>')
            elif kind == "number":
                pieces.append(f'<span class="tok-num">{html.escape(text)}</span>')
            elif kind == "sel":
                cls = "tok-sel" if lang in ("css", "scss") else "tok-prop"
                pieces.append(f'<span class="{cls}">{html.escape(text)}</span>')
            elif kind == "word":
                stripped = raw[m.start():]
                if lang in ("css", "scss") and re.match(r"[\w-]+\s*:", stripped) and not stripped.startswith(("var", "--")):
                    pieces.append(f'<span class="tok-prop">{html.escape(text)}</span>')
                elif text in keywords:
                    pieces.append(f'<span class="tok-kw">{html.escape(text)}</span>')
                elif re.match(r"[\w-]*\s*\(", stripped) and raw[m.start():m.start() + 1].isalpha():
                    pieces.append(f'<span class="tok-fn">{html.escape(text)}</span>')
                else:
                    pieces.append(html.escape(text))
            pos = m.end()
        if pos < len(raw):
            pieces.append(html.escape(raw[pos:]))
        out.append('<span class="line">' + "".join(pieces) + "</span>")
    return out


def code_block(code: str, info: str) -> str:
    parts = (info or "").strip().split()
    lang = parts[0] if parts else ""
    filename = parts[1] if len(parts) > 1 else ""
    lang_label = lang.upper() if lang else "CODE"
    file_html = (f'<span class="code__file">{html.escape(filename)}</span>'
                 if filename else '<span class="code__file"></span>')
    lines = "\n".join(highlight(code.rstrip("\n"), lang))
    return (
        '<div class="code">\n'
        '              <div class="code__bar">\n'
        f"                {file_html}\n"
        f'                <span class="code__lang">{lang_label}</span>\n'
        '                <button class="code__copy" type="button" data-copy>\n'
        "                  复制到剪贴板\n"
        "                </button>\n"
        "              </div>\n"
        f"              <pre><code>{lines}</code></pre>\n"
        "            </div>"
    )


# ---------------------------------------------------------------- 块级解析

def slugify(text: str, index: int) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return ascii_part or f"section-{index}"


def md_to_html(body: str):
    """返回 (html, toc)。toc 是 [(id, 标题文本), ...]。"""
    lines = body.split("\n")
    blocks = []
    toc = []
    heading_index = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        # 围栏代码
        fence = re.match(r"^```(\S*)\s*(.*)$", line)
        if fence:
            info = (fence.group(1) + " " + fence.group(2)).strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(code_block("\n".join(buf), info))
            continue

        # 标题
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if h:
            level = len(h.group(1))
            text = h.group(2).strip()
            explicit = re.search(r"\s*\{#([\w-]+)\}\s*$", text)
            if explicit:
                anchor = explicit.group(1)
                text = text[: explicit.start()].strip()
            else:
                heading_index += 1
                anchor = slugify(text, heading_index)
            label = re.sub(r"[*`]", "", text)
            if level == 2:
                toc.append((anchor, label))
                blocks.append(
                    f'<h2 id="{anchor}">\n              {inline(text)}\n'
                    f'              <a class="anchor" href="#{anchor}">链接到此标题</a>\n            </h2>'
                )
            else:
                blocks.append(f'<h{level} id="{anchor}">{inline(text)}</h{level}>')
            i += 1
            continue

        # 引用
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip())
                i += 1
            paras = [p for p in "\n".join(buf).split("\n\n") if p.strip()]
            inner = "".join(f"<p>{inline(p.strip())}</p>" for p in paras)
            blocks.append(f"<blockquote>{inner}</blockquote>")
            continue

        # 表格
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            caption = None
            if blocks and isinstance(blocks[-1], tuple) and blocks[-1][0] == "caption":
                caption = blocks.pop()[1]
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            thead = "".join(f"<th>{inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in rows
            )
            cap = f"<caption>{inline(caption)}</caption>" if caption else ""
            blocks.append(
                f"<table>{cap}<thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"
            )
            continue

        # 表格标题：单独一行 "表：xxx"
        cap = re.match(r"^表[：:]\s*(.+)$", line.strip())
        if cap:
            blocks.append(("caption", cap.group(1).strip()))
            i += 1
            continue

        # 列表
        if re.match(r"^\s*[-*+]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]))
                i += 1
            blocks.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            blocks.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue

        # 原样 HTML 块：从 <tag 开始，到空行为止
        if line.lstrip().startswith("<"):
            buf = []
            depth = 0
            while i < len(lines):
                cur = lines[i]
                buf.append(cur)
                depth += len(re.findall(r"<div\b", cur)) - len(re.findall(r"</div>", cur))
                i += 1
                if depth <= 0 and (cur.strip() == "" or cur.strip().endswith(">")):
                    if i < len(lines) and not lines[i].strip():
                        break
                    if depth <= 0 and cur.strip():
                        break
            blocks.append("\n".join(buf).strip())
            continue

        # 段落
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{1,6}\s|```|>|\s*\||\s*[-*+]\s|\s*\d+\.\s|<)", lines[i]
        ):
            buf.append(lines[i].strip())
            i += 1
        blocks.append(f"<p>{inline(' '.join(buf))}</p>")

    cleaned = [b[1] if isinstance(b, tuple) else b for b in blocks if not isinstance(b, tuple)]
    return "\n\n".join(cleaned), toc


# ---------------------------------------------------------------- 渲染

def cn_date(value: str) -> str:
    d = datetime.strptime(value, "%Y-%m-%d")
    return f"{d.year} 年 {d.month} 月 {d.day} 日"


def render_post(meta, body_html, toc):
    tpl = (TEMPLATES / "post.html").read_text(encoding="utf-8")
    slug = meta["slug"]
    title = meta["title"]
    dek = meta.get("dek", "")
    url = f"{SITE}/posts/{slug}"
    toc_html = "".join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t in toc)
    meta_line = (
        f'<span>首发于 <a href="/">{cn_date(meta["date"])}</a></span>\n'
        '          <span class="sep">·</span>\n'
        f'          <span>约 {meta.get("minutes", "5")} 分钟</span>\n'
        '          <span class="sep">·</span>\n'
        f'          <a href="/#writing">{html.escape(meta.get("tag", "笔记"))}</a>'
    )
    return (
        tpl.replace("{{TITLE}}", html.escape(title))
        .replace("{{DESC}}", html.escape(dek))
        .replace("{{CANONICAL}}", url)
        .replace("{{KICKER}}", html.escape(meta.get("tag", "笔记")))
        .replace("{{DEK}}", inline(dek))
        .replace("{{META}}", meta_line)
        .replace("{{TOC}}", toc_html)
        .replace("{{BODY}}", body_html)
        .replace("{{DATE_CN}}", cn_date(meta["date"]))
        .replace("{{NOTE}}", inline(meta.get("note", "写完改了三遍。发现哪里读着别扭，那多半是真的别扭。")))
    )


def render_index(posts):
    tpl = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    items = []
    for p in posts:
        item = f'''<article class="post-item">
              <h3><a href="/posts/{p["slug"]}">{html.escape(p["title"])}</a></h3>
              <p class="post-item__sub">{html.escape(p.get("dek", ""))}</p>
              <p class="post-item__excerpt">{inline(p.get("excerpt", p.get("dek", "")))}</p>
              <a class="readmore" href="/posts/{p["slug"]}">
                Read more
                <span class="trail" aria-hidden="true">
                  <svg class="trail__lead" viewBox="0 0 24 18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 9h14M11 3l6 6-6 6"/></svg>
                  <svg class="trail__ghost" viewBox="0 0 11 18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l5.5 6L3 15"/></svg>
                  <svg class="trail__ghost" viewBox="0 0 11 18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l5.5 6L3 15"/></svg>
                  <svg class="trail__ghost" viewBox="0 0 11 18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l5.5 6L3 15"/></svg>
                </span>
              </a>
            </article>'''
        items.append(item)
    newest = f"/posts/{posts[0]['slug']}" if posts else "/"
    return (tpl.replace("{{NEWEST}}", newest)
            .replace("<!--POSTS-->", "\n            ".join(items)))


def render_sitemap(posts):
    urls = [f"  <url>\n    <loc>{SITE}/</loc>\n    <lastmod>{date.today()}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n    <priority>1.0</priority>\n  </url>"]
    for p in posts:
        urls.append(
            f"  <url>\n    <loc>{SITE}/posts/{p['slug']}</loc>\n    <lastmod>{p['date']}</lastmod>\n"
            f"    <changefreq>monthly</changefreq>\n    <priority>0.8</priority>\n  </url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


def render_rss(posts):
    items = []
    for p in posts:
        pub = datetime.strptime(p["date"], "%Y-%m-%d").replace(
            hour=9, tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        items.append(
            "    <item>\n"
            f"      <title>{html.escape(p['title'])}</title>\n"
            f"      <link>{SITE}/posts/{p['slug']}</link>\n"
            f"      <guid isPermaLink=\"true\">{SITE}/posts/{p['slug']}</guid>\n"
            f"      <pubDate>{pub}</pubDate>\n"
            f"      <category>{html.escape(p.get('tag', '笔记'))}</category>\n"
            f"      <description><![CDATA[<p>{html.escape(p.get('dek', ''))}</p>]]></description>\n"
            "    </item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        "    <title>潦草地</title>\n"
        f"    <link>{SITE}/</link>\n"
        "    <description>写在草稿纸上的技术笔记：Android、工具与写作。</description>\n"
        "    <language>zh-CN</language>\n"
        f"    <lastBuildDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>\n"
        f'    <atom:link href="{SITE}/rss.xml" rel="self" type="application/rss+xml" />\n'
        + "\n".join(items)
        + "\n  </channel>\n</rss>\n"
    )


# ---------------------------------------------------------------- 主流程

def load_posts():
    posts = []
    for path in sorted(CONTENT.glob("*.md")):
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        if meta.get("draft", "").lower() == "true":
            continue
        meta.setdefault("slug", path.stem)
        meta["_path"] = path
        meta["_body"] = body
        posts.append(meta)
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def new_post():
    slug = input("文件名（英文或数字，用连字符，例如 no-framework-search）：").strip()
    title = input("标题：").strip()
    dek = input("副标题：").strip()
    tag = input("分类：").strip() or "笔记"
    path = CONTENT / f"{slug}.md"
    if path.exists():
        raise SystemExit(f"{path} 已存在")
    path.write_text(
        f"---\ntitle: {title}\ndek: {dek}\ndate: {date.today()}\nminutes: 5\ntag: {tag}\n---\n\n"
        "正文从这里开始。\n\n## 第一个小节\n\n段落之间空一行。\n",
        encoding="utf-8",
    )
    print(f"已创建 {path}")


def main():
    if "--new" in sys.argv:
        new_post()
        return

    posts = load_posts()
    if not posts:
        raise SystemExit("content/posts/ 里没有文章")

    # 每次全量重建，避免删掉的文章留下孤儿页面
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True)
    (PUBLIC / "posts").mkdir()

    for p in posts:
        body_html, toc = md_to_html(p["_body"])
        out = PUBLIC / "posts" / f"{p['slug']}.html"
        out.write_text(render_post(p, body_html, toc), encoding="utf-8")
        print(f"  posts/{p['slug']}.html  （{len(toc)} 个目录项）")

    for f in STATIC.iterdir():
        (PUBLIC / f.name).write_bytes(f.read_bytes())

    (PUBLIC / "index.html").write_text(render_index(posts), encoding="utf-8")
    (PUBLIC / "sitemap.xml").write_text(render_sitemap(posts), encoding="utf-8")
    (PUBLIC / "rss.xml").write_text(render_rss(posts), encoding="utf-8")
    print(f"  index.html / sitemap.xml / rss.xml  （共 {len(posts)} 篇）")


if __name__ == "__main__":
    main()
