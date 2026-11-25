#!/usr/bin/env python3
"""潦草地的静态站构建脚本。

只依赖 Python 标准库。把 content/posts/*.md 渲染成 public/posts/<slug>.html，
并重新生成首页列表、sitemap.xml、rss.xml。

用法：
    python tools/build.py
    python tools/build.py --new          新建一篇草稿
"""

import hashlib
import html
import json
import re
import shutil
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
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


HIGHLIGHT_LANGS = {'css', 'scss', 'js', 'ts', 'javascript', 'kotlin', 'java', 'bash', 'sh'}


def code_block(code: str, info: str) -> str:
    parts = (info or "").strip().split()
    lang = parts[0] if parts else ""
    filename = parts[1] if len(parts) > 1 else ""
    lang_label = lang.upper() if lang else "CODE"
    file_html = (f'<span class="code__file">{html.escape(filename)}</span>'
                 if filename else '<span class="code__file"></span>')
    plain = lang not in HIGHLIGHT_LANGS
    if plain:
        lines = "\n".join(
            '<span class="line">' + html.escape(raw) + '</span>'
            for raw in code.rstrip("\n").split("\n")
        )
    else:
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
                '<div class="table-wrap">'
                f"<table>{cap}<thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"
                "</div>"
            )
            continue

        # 表格标题：单独一行 "表：xxx"
        cap = re.match(r"^表[：:]\s*(.+)$", line.strip())
        if cap:
            blocks.append(("caption", cap.group(1).strip()))
            i += 1
            continue

        # 独占一行的图片，输出成 figure 以便撑宽
        only_img = re.match(r"^!\[([^\]]*)\]\(([^)\s]+)\)\s*$", line)
        if only_img:
            blocks.append(
                '<figure class="prose-figure">'
                f'<img src="{only_img.group(2)}" alt="{html.escape(only_img.group(1))}" loading="lazy" />'
                "</figure>"
            )
            i += 1
            continue

        # 水平线
        if re.match(r"^---+\s*$", line):
            blocks.append("<hr />")
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

    # 给每个顶层块打上序号，批注靠它定位。序号 = 块在正文里的顺序
    cleaned = []
    for b in blocks:
        if isinstance(b, tuple):
            continue
        idx = len(cleaned)
        cleaned.append(re.sub(r"^(<[a-zA-Z][\w-]*)", rf'\1 data-block="{idx}"', b, count=1))
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
    # 日期暂时不上页面：有的文章是早先写的、近期改的，露出"首发于"会误导
    meta_line = (
        f'<span>{html.escape(meta.get("tag", "笔记"))}</span>\n'
        '          <span class="sep">·</span>\n'
        f'          <span>约 {meta.get("minutes", "5")} 分钟</span>'
    )
    return (
        tpl.replace("{{TITLE}}", html.escape(title))
        .replace("{{DESC}}", html.escape(dek))
        .replace("{{SLUG}}", slug)
        .replace("{{CANONICAL}}", url)
        .replace("{{KICKER}}", html.escape(meta.get("tag", "笔记")))
        .replace("{{DEK}}", inline(dek))
        .replace("{{META}}", meta_line)
        .replace("{{TOC}}", toc_html)
        .replace("{{BODY}}", body_html)
        .replace("{{DATE_CN}}", cn_date(meta["date"]))
        .replace("{{NOTE}}", inline(meta.get("note", "写完改了三遍。发现哪里读着别扭，那多半是真的别扭。")))
    )


INDEX_LIMIT = 6  # 首页最多显示几篇，其余进归档页

SHOWMORE = '''<div class="showmore-wrap">
              <a class="showmore" href="/archive">
                全部 {{TOTAL}} 篇
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M5 12h13M12.5 5.5 19 12l-6.5 6.5" />
                </svg>
              </a>
            </div>'''


# 纯中文标签没有可用的 URL slug，在这里显式映射；新增中文标签记得补
TAG_SLUGS = {"排版": "typography", "笔记": "notes"}


def tag_slug(tag):
    slug = re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")
    if slug:
        return slug
    mapped = TAG_SLUGS.get(tag)
    if mapped:
        return mapped
    return "t-" + hashlib.md5(tag.encode("utf-8")).hexdigest()[:6]


def collect_tags(posts):
    """{标签: [文章]}，按文章数倒序。"""
    groups = {}
    for p in posts:
        groups.setdefault(p.get("tag", "笔记"), []).append(p)
    return dict(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])))
def render_archive(posts):
    """归档页：扁平列表，只列标题和分类。

    不显示日期——排序用的是 date + time，但页面上一律不露出，
    避免"最近改过"的文章看起来像是刚写的。
    """
    tpl = (TEMPLATES / "archive.html").read_text(encoding="utf-8")
    rows = []
    for p in posts:
        rows.append(
            f'<li class="archive__row">'
            f'<a href="/posts/{p["slug"]}">'
            f'<span class="archive__name">{html.escape(p["title"])}</span>'
            f'<span class="archive__tag">{html.escape(p.get("tag", ""))}</span>'
            f"</a></li>"
        )
    return (tpl.replace("{{COUNT}}", f"共 {len(posts)} 篇")
               .replace("<!--ARCHIVE-->", '<ul class="archive__rows">' + "".join(rows) + "</ul>"))

def render_categories(posts):
    """分类索引：一行一个分类，带文章数。"""
    tpl = (TEMPLATES / "categories.html").read_text(encoding="utf-8")
    tags = collect_tags(posts)
    rows = []
    for tag, items in tags.items():
        rows.append(
            f'<li class="archive__row">'
            f'<a href="/categories/{tag_slug(tag)}">'
            f'<span class="archive__name">{html.escape(tag)}</span>'
            f'<span class="archive__tag">{len(items)} 篇</span>'
            f"</a></li>"
        )
    return (
        tpl.replace("{{DESC}}", "潦草地的全部文章分类。")
        .replace("{{LABEL}}", "CATEGORIES")
        .replace("{{HEADING}}", "分类")
        .replace("{{SUB}}", f"共 {len(tags)} 个分类")
        .replace("<!--CHIPS-->", "")
        .replace("<!--ROWS-->", '<ul class="archive__rows">' + "".join(rows) + "</ul>")
    )


def render_category(tag, items, all_tags):
    """单个分类下的文章列表，顶部带切换用的标签行。"""
    tpl = (TEMPLATES / "category.html").read_text(encoding="utf-8")
    rows = []
    for p in items:
        rows.append(
            f'<li class="archive__row">'
            f'<a href="/posts/{p["slug"]}">'
            f'<span class="archive__name">{html.escape(p["title"])}</span>'
            f'<span class="archive__tag">{html.escape(p.get("dek", ""))}</span>'
            f"</a></li>"
        )
    chips = "".join(
        f'<a class="cat-chip{" is-active" if t == tag else ""}" '
        f'href="/categories/{tag_slug(t)}">{html.escape(t)}'
        f'<span>{len(v)}</span></a>'
        for t, v in all_tags.items()
    )
    return (
        tpl.replace("{{DESC}}", f"潦草地中「{html.escape(tag)}」分类下的全部文章。")
        .replace("{{LABEL}}", "CATEGORY")
        .replace("{{HEADING}}", html.escape(tag))
        .replace("{{SUB}}", f"{len(items)} 篇")
        .replace("<!--CHIPS-->", f'<nav class="cat-chips">{chips}</nav>')
        .replace("<!--ROWS-->", '<ul class="archive__rows">' + "".join(rows) + "</ul>")
    )


def render_search_index(posts):
    """搜索索引：标题、副标题、摘要、标签、小节标题。不含正文，控制体积。"""
    entries = []
    for p in posts:
        heads = re.findall(r"(?m)^##\s+(.+)$", p["_body"])
        entries.append({
            "url": f"/posts/{p['slug']}",
            "title": p["title"],
            "dek": p.get("dek", ""),
            "tag": p.get("tag", ""),
            "excerpt": p.get("excerpt", ""),
            "headings": [h.strip() for h in heads],
        })
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":"))


def render_body_index(posts):
    """正文索引：去掉代码块、图片和标记后的纯文本，供搜索深层匹配。

    单独一个文件，体积约 400KB，只在用户打开搜索时后台加载。
    """
    rows = {}
    for p in posts:
        text = p["_body"]
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
        text = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1", text)
        text = re.sub(r"[#>*`|_\-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        rows[f"/posts/{p['slug']}"] = text
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))

def render_index(posts):
    tpl = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    items = []
    for p in posts[:INDEX_LIMIT]:
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

    # 分类：从所有文章的真实标签聚合，按出现次数排序
    counts = {}
    for p in posts:
        tag = p.get("tag", "")
        if tag:
            counts[tag] = counts.get(tag, 0) + 1
    cats = "\n              ".join(
        f'<li><a href="/categories/{tag_slug(t)}">{html.escape(t)}</a></li>'
        for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    # 热门：先按 tag 里标 hot=true 的，不够就拿最新几篇
    hot = [p for p in posts if p.get("hot", "").lower() == "true"]
    rest = [p for p in posts if p not in hot]
    picks = (hot + rest)[:5]
    arrow = (
        '<span class="arrow-grow" aria-hidden="true">'
        '<svg viewBox="0 0 22 18" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<line class="shaft" x1="1" y1="9" x2="13" y2="9" vector-effect="non-scaling-stroke" />'
        '<path class="head" d="M7.5 3L15 9l-7.5 6" />'
        "</svg></span>"
    )
    popular = "\n                ".join(
        f'<li>\n                  <a href="/posts/{p["slug"]}">\n'
        f"                    {arrow}\n"
        f'                    <span>{html.escape(p["title"])}</span>\n'
        f"                  </a>\n                </li>"
        for p in picks
    )

    return (tpl.replace("{{NEWEST}}", newest)
            .replace("<!--POSTS-->", "\n            ".join(items))
            .replace("<!--CATEGORIES-->", cats)
            .replace("<!--POPULAR-->", popular)
            # 文章不足 6 篇时不显示 Show more
            # 首页目前渲染全部文章，Show more 没有可展开的内容，先不输出；
            # 等做出归档页再挂上去。
            .replace("<!--SHOWMORE-->", SHOWMORE.replace("{{TOTAL}}", str(len(posts))) if len(posts) > INDEX_LIMIT else ""))


def render_sitemap(posts):
    urls = [f"  <url>\n    <loc>{SITE}/</loc>\n    <lastmod>{date.today()}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n    <priority>1.0</priority>\n  </url>"]
    urls.append(
        f"  <url>\n    <loc>{SITE}/archive</loc>\n    <lastmod>{date.today()}</lastmod>\n"
        f"    <changefreq>weekly</changefreq>\n    <priority>0.6</priority>\n  </url>"
    )
    urls.append(
        f"  <url>\n    <loc>{SITE}/categories</loc>\n    <lastmod>{date.today()}</lastmod>\n"
        f"    <changefreq>weekly</changefreq>\n    <priority>0.5</priority>\n  </url>"
    )
    for tag in collect_tags(posts):
        urls.append(
            f"  <url>\n    <loc>{SITE}/categories/{tag_slug(tag)}</loc>\n    <lastmod>{date.today()}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n    <priority>0.5</priority>\n  </url>"
        )
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
    for p in posts[:INDEX_LIMIT]:
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
    # 日期只到天，同一天必须靠 time 分先后，否则会退化成按文件名排
    posts.sort(key=lambda p: (p["date"], p.get("time", "00:00")), reverse=True)
    return posts


# ---------------------------------------------------------------- 发布前检查

REQUIRED = ("title", "dek", "excerpt", "date", "minutes", "tag")
DEK_MAX = 34      # 副标题超过这个长度就会折行，列表会显得乱
EXCERPT_MIN, EXCERPT_MAX = 50, 180


def validate(posts):
    """检查那些肉眼容易漏、但机器一眼能看出的问题。有问题就拒绝构建。"""
    problems = []
    seen_titles = {}

    for p in posts:
        name = p["_path"].name
        for key in REQUIRED:
            if not p.get(key):
                problems.append(f"{name}: 缺 front matter 字段 `{key}`")

        title = p.get("title", "")
        if title in seen_titles:
            problems.append(f"{name}: 标题与 {seen_titles[title]} 重复")
        seen_titles[title] = name

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.get("date", "")):
            problems.append(f"{name}: date 必须是 YYYY-MM-DD，现在是 {p.get('date')!r}")

        if p.get("time") and not re.fullmatch(r"\d{2}:\d{2}", p["time"]):
            problems.append(f"{name}: time 必须是 HH:MM，现在是 {p.get('time')!r}")

        if not p.get("minutes", "").isdigit():
            problems.append(f"{name}: minutes 必须是数字")

        dek = p.get("dek", "")
        if len(dek) > DEK_MAX:
            problems.append(
                f"{name}: 副标题 {len(dek)} 字，超过 {DEK_MAX} 字会在列表里折行，"
                f"把解释留给 excerpt"
            )

        exc = p.get("excerpt", "")
        if not (EXCERPT_MIN <= len(exc) <= EXCERPT_MAX):
            problems.append(
                f"{name}: 摘要 {len(exc)} 字，建议 {EXCERPT_MIN}-{EXCERPT_MAX} 字"
            )

        body = p["_body"]
        if re.search(r"!\[[^\]]*\]\(images/", body):
            problems.append(f"{name}: 图片用了相对路径 images/，构建后会被解析到 /posts/images/")

        for src in re.findall(r"!\[[^\]]*\]\((/images/[^)]+)\)", body):
            if not (ROOT / "static" / src.lstrip("/")).exists():
                problems.append(f"{name}: 引用的图片不存在 {src}")

        if re.search(r"\n-{3,}\s*\n+\*[^*].*\*\s*$", body.strip() + "\n"):
            problems.append(f"{name}: 文末还留着「--- + 斜体免责声明」，应该挪进 note")

    # 文件编码：带 BOM 会让 _headers 之类的配置解析失败
    for folder in (ROOT / "static", ROOT / "templates", CONTENT):
        if not folder.exists():
            continue
        for f in folder.rglob("*"):
            if f.is_file() and f.suffix in {".css", ".html", ".md", ".xml", ".txt", ".svg"} or f.name == "_headers":
                if f.read_bytes()[:3] == b"\xef\xbb\xbf":
                    problems.append(f"{f.name}: 文件带 BOM")

    if problems:
        print("\n发布前检查未通过：")
        for item in problems:
            print(f"  x  {item}")
        print(f"\n共 {len(problems)} 处。改完再跑一次 python tools/build.py")
        raise SystemExit(1)

    print(f"  发布前检查通过（{len(posts)} 篇）")


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


def stamp_assets():
    """给静态资源地址加上内容哈希，避免 HTML 更新了而 CSS/JS 还在缓存里。

    不这么做的话，每次改了样式再发布，老访客会拿到「新 HTML + 旧 CSS」，
    页面结构在但样式不生效。
    """
    versions = {}
    for name in ("style.css", "search.js", "lightbox.js", "notes.js"):
        f = STATIC / name
        if f.exists():
            versions[name] = hashlib.md5(f.read_bytes()).hexdigest()[:8]

    for page in PUBLIC.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        original = text
        for name, ver in versions.items():
            text = text.replace(f'"/{name}"', f'"/{name}?v={ver}"')
        if text != original:
            page.write_text(text, encoding="utf-8", newline="\n")

    # 搜索索引同理：内容一变，URL 就要变
    js = (PUBLIC / "search.js").read_text(encoding="utf-8")
    for name in ("search.json", "search-body.json"):
        f = PUBLIC / name
        if f.exists():
            ver = hashlib.md5(f.read_bytes()).hexdigest()[:8]
            js = js.replace(f'"/{name}"', f'"/{name}?v={ver}"')
    (PUBLIC / "search.js").write_text(js, encoding="utf-8", newline="\n")

    print(f"  资源版本：{'、'.join(f'{k}->{v}' for k, v in versions.items())}")

def main():
    if "--new" in sys.argv:
        new_post()
        return

    posts = load_posts()
    if not posts:
        raise SystemExit("content/posts/ 里没有文章")

    validate(posts)

    # 每次全量重建，避免删掉的文章留下孤儿页面
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True)
    (PUBLIC / "posts").mkdir()

    for p in posts:
        body_html, toc = md_to_html(p["_body"])
        out = PUBLIC / "posts" / f"{p['slug']}.html"
        out.write_text(render_post(p, body_html, toc), encoding="utf-8", newline="\n")
        print(f"  posts/{p['slug']}.html  （{len(toc)} 个目录项）")

    for f in STATIC.iterdir():
        if f.is_dir():
            shutil.copytree(f, PUBLIC / f.name, dirs_exist_ok=True)
        else:
            (PUBLIC / f.name).write_bytes(f.read_bytes())

    (PUBLIC / "index.html").write_text(render_index(posts), encoding="utf-8", newline="\n")
    (PUBLIC / "archive.html").write_text(render_archive(posts), encoding="utf-8", newline="\n")

    # 分类页
    tags = collect_tags(posts)
    cats_dir = PUBLIC / "categories"
    cats_dir.mkdir()
    (PUBLIC / "categories.html").write_text(render_categories(posts), encoding="utf-8", newline="\n")
    for tag, items in tags.items():
        (cats_dir / f"{tag_slug(tag)}.html").write_text(
            render_category(tag, items, tags), encoding="utf-8", newline="\n"
        )
    print(f"  categories/  （{len(tags)} 个分类：{'、'.join(tags)}）")
    for tag in tags:
        if not re.search(r"[a-z0-9]", tag, re.I) and tag not in TAG_SLUGS:
            print(f"  ! 分类「{tag}」没有 URL 映射，已用哈希代替；在 build.py 的 TAG_SLUGS 里加一条更可读")

    # 搜索索引，前端按需拉取
    (PUBLIC / "search.json").write_text(render_search_index(posts), encoding="utf-8", newline="\n")
    body_index = render_body_index(posts)
    (PUBLIC / "search-body.json").write_text(body_index, encoding="utf-8", newline="\n")
    body_kb = len(body_index.encode("utf-8")) // 1024
    print(f"  search.json + search-body.json （{body_kb}KB 正文索引）")
    # 提醒阈值：见 PUBLISHING.md「搜索的性能底线」
    if body_kb >= 900:
        print(f"  ! 正文索引 {body_kb}KB 已超过 900KB，该考虑分片了（见 PUBLISHING.md）")
    elif body_kb >= 600:
        print(f"  · 正文索引 {body_kb}KB，接近 900KB 提醒线")
    print(f"  archive.html  （全部 {len(posts)} 篇）")
    (PUBLIC / "sitemap.xml").write_text(render_sitemap(posts), encoding="utf-8", newline="\n")
    (PUBLIC / "rss.xml").write_text(render_rss(posts), encoding="utf-8", newline="\n")
    print(f"  index.html / sitemap.xml / rss.xml  （共 {len(posts)} 篇）")

    # 最后一步：给 style.css / search.js / 搜索索引加内容哈希
    stamp_assets()


if __name__ == "__main__":
    main()
