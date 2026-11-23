#!/usr/bin/env python3
"""把 android-* 目录里的成品稿件导入成博客文章。

做的都是机械活：
  · 找出目录里那篇 md（排除 README.md）
  · 把 images/*.png 转成 WebP 放进 static/images/<slug>/
  · 去掉开头的一级标题和元信息引用（内容进 front matter）
  · 图片路径改成 /images/<slug>/xxx.webp
  · 补上 front matter，写到 content/posts/<slug>.md

正文自动留白，标题/阅读时长/标签尽量从原文里抽，抽不到就用参数。

用法：
    python tools/import_post.py --src ../android-binder-ipc --slug android-binder-ipc \
        --dek "一句话摘要" --excerpt "首页列表用的长摘要" --note "文末那句署名"
"""

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"
IMAGES = ROOT / "static" / "images"


def find_markdown(src: Path) -> Path:
    candidates = [
        p for p in src.glob("*.md") if p.name.lower() != "readme.md"
    ]
    if not candidates:
        raise SystemExit(f"{src} 里没找到正文 md")
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise SystemExit(f"找到多篇 md，不知道用哪个：{names}")
    return candidates[0]


def convert_images(src: Path, slug: str) -> dict:
    """PNG → WebP，返回 {原文件名: webp 文件名}。"""
    out_dir = IMAGES / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    src_dir = src / "images"
    if not src_dir.exists():
        return mapping

    total_png = total_webp = 0
    for png in sorted(src_dir.glob("*.png")):
        img = Image.open(png).convert("RGB")
        webp = out_dir / (png.stem + ".webp")
        img.save(webp, "WEBP", lossless=True, quality=100, method=6)
        size = webp.stat().st_size
        if size >= png.stat().st_size * 0.85:
            img.save(webp, "WEBP", quality=88, method=6)
            size = webp.stat().st_size
        total_png += png.stat().st_size
        total_webp += size
        mapping[png.name] = webp.name
        print(f"  {png.name:22} {png.stat().st_size // 1024:4d}KB -> {size // 1024:4d}KB")
    if total_png:
        saved = 100 - 100 * total_webp / total_png
        print(f"  合计 {total_png // 1024}KB -> {total_webp // 1024}KB（省 {saved:.0f}%）")
    return mapping


def parse_meta(body: str) -> dict:
    """从开头的引用块里抽标题、阅读时长、第一个标签。"""
    meta = {}
    h1 = re.search(r"(?m)^#\s+(.+)$", body)
    if h1:
        meta["title"] = h1.group(1).strip()
    read = re.search(r"预计阅读\**[：:]\s*(\d+)\s*分钟", body)
    if read:
        meta["minutes"] = read.group(1)
    tag_line = re.search(r"标签[：:]\s*([^\n]+)", body)
    if tag_line:
        tags = re.findall(r"#([^\s#]+)", tag_line.group(1))
        if tags:
            meta["tag"] = tags[0]
    return meta


def strip_head(body: str) -> str:
    """去掉一级标题和紧随其后的元信息引用块。"""
    lines = body.split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith("# ")):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and lines[i].startswith(">"):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--dek", required=True)
    ap.add_argument("--excerpt", required=True)
    ap.add_argument("--note", required=True)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--minutes", default=None)
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--hot", action="store_true")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    md_path = find_markdown(src)
    raw = md_path.read_text(encoding="utf-8")
    auto = parse_meta(raw)
    body = strip_head(raw)

    print(f"源文件：{md_path.name}")
    print("图片：")
    mapping = convert_images(src, args.slug)

    def rewrite(m):
        alt, path = m.group(1), m.group(2)
        name = Path(path).name
        new = mapping.get(name)
        if not new:
            print(f"  ! 找不到图片 {path}，保持原样", file=sys.stderr)
            return m.group(0)
        return f"![{alt}](/images/{args.slug}/{new})"

    body = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", rewrite, body)

    # 文末常见「分隔线 + 斜体免责声明」，这段内容应该进 note
    lines = body.rstrip().split("\n")
    while lines and (not lines[-1].strip() or re.fullmatch(r"-{3,}", lines[-1].strip())):
        lines.pop()
    if lines and re.fullmatch(r"\*[^*].*\*", lines[-1].strip()):
        dropped = lines.pop().strip().strip("*")
        print(f"  文末免责声明已移出正文：{dropped[:28]}…")
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and re.fullmatch(r"-{3,}", lines[-1].strip()):
            lines.pop()
    body = "\n".join(lines).strip() + "\n"

    title = auto.get("title") or args.slug
    minutes = args.minutes or auto.get("minutes", "10")
    tag = args.tag or auto.get("tag", "笔记")

    clock = datetime.now().strftime("%H:%M")
    front = ["---", f"title: {title}", f"dek: {args.dek}", f"excerpt: {args.excerpt}",
             f"date: {args.date}", f"time: {clock}", f"minutes: {minutes}", f"tag: {tag}"]
    if args.hot:
        front.append("hot: true")
    front += [f"note: {args.note}", "---", ""]

    out = POSTS / f"{args.slug}.md"
    out.write_text("\n".join(front) + body, encoding="utf-8", newline="\n")

    words = len(re.sub(r"\s", "", body))
    print(f"\n已写入 {out.relative_to(ROOT)}")
    print(f"  标题：{title}")
    print(f"  分类：{tag} ｜ 阅读：{minutes} 分钟 ｜ 正文约 {words} 字")
    print("  下一步：python tools/build.py")


if __name__ == "__main__":
    main()
