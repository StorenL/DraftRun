# 潦草地 · 构建与部署

纯静态站，没有运行时依赖。构建脚本只用 Python 标准库。

## 目录

```
content/posts/*.md    你写的文章（唯一需要手写的东西）
templates/            页面骨架
  index.html          首页（含 <!--POSTS--> 标记）
  post.html           文章页（{{占位符}}）
static/               原样复制的资源
  style.css  favicon.svg  robots.txt  _headers  404.html
tools/build.py        构建脚本
public/               构建产物（不要手改）
wrangler.toml         Cloudflare Workers 配置
```

`wrangler.toml` 只发布 `public/`，所以 md、模板、脚本都不会被公开。

## 写一篇文章

### 1. 新建

```bash
python tools/build.py --new
```

按提示填文件名、标题、副标题、分类。它会在 `content/posts/` 下建一个 md。

也可以直接手写一个 `content/posts/我的文章.md`：

```markdown
---
title: 没有框架，怎么做搜索和标签
dek: 手写一个 300 行的静态站索引
excerpt: 首页列表里显示的摘要，比 dek 长一点
date: 2026-12-01
minutes: 7
tag: 工具
note: 文章末尾那句署名
---

正文。

## 小节标题

段落之间空一行。
```

文件名就是 URL：`no-framework-search.md` → `/posts/no-framework-search`。

### 2. 支持的语法

| 写法 | 效果 |
|---|---|
| `## 标题` | 二级标题，自动进目录 |
| `## 标题 {#custom-id}` | 指定锚点，不进目录排序 |
| `**粗体**`、`*斜体*`、`` `行内代码` `` | 行内标记 |
| `[文字](链接)` | 链接 |
| `> 引用` | 引用块，空行分段 |
| `- 列表` / `1. 列表` | 列表 |
| `表：说明文字` 换行接 `\| 表格 \|` | 带标题的表格 |
| ```` ```css css/tokens.css ```` | 代码块：语言 + 文件名 |
| 直接写 `<div class="demo">…</div>` | 原样输出，用来放 demo、callout |

代码块的语言决定高亮规则，目前内置 `css` `js` `kotlin` `java` `bash`。

### 3. 构建

```bash
python tools/build.py
```

它会：

- 把 md 渲染成 `public/posts/<slug>.html`
- 重新生成首页列表（按日期倒序）
- 重新生成 `sitemap.xml`、`rss.xml`
- 把所有 `static/` 复制进 `public/`

**每次都是全量重建**，`public/` 会被先清空，所以删掉一篇文章也安全。

### 4. 发布

```bash
git add -A
git commit -m "新文章：xxx"
git push
```

Cloudflare 自动构建，几十秒后上线。

> `public/` 是提交进仓库的。改完 md 一定要跑一次 `python tools/build.py` 再提交，否则线上还是旧内容。

## 部署

Cloudflare Workers + Static Assets，从 GitHub 自动构建。

| 设置 | 值 |
|---|---|
| Framework preset | None |
| Build command | 空 |
| Deploy command | `npx wrangler deploy` |
| 入口配置 | `wrangler.toml` 的 `assets.directory = "./public"` |

域名 `draft.run` 托管在 Cloudflare，Worker 里绑 Custom Domain。

## 自动构建坏了怎么办

2026-11-27 起，Cloudflare 的自动构建一直失败，日志里是：

```
Build failed: Your build is configured with a build token that belongs to a
user who has left your organization. This token is no longer valid and cannot
be managed by other members.
```

修法（Dashboard）：**Worker → draftrun → Settings → Builds → API token**，
重新选一个或新建一个 build token，然后重试构建。

在修好之前，用本地部署兜底：

```powershell
cd deploy
python tools/build.py
node "C:\Users\18720\Documents\AiWork\Blog\.tools\node_modules\wrangler\bin\wrangler.js" deploy
```

wrangler 装在仓库外（`.tools/`），登录状态存在用户目录，长期有效；
失效就重新 `wrangler login`（授权码只有 2 分钟，浏览器要立刻点）。

本地部署约 15 秒，比自动构建（约 2 分钟）还快。

## 注意

**缓存**：`_headers` 里 CSS 是 10 分钟 + `stale-while-revalidate`。改样式最多 10 分钟生效，不用改文件名。

**HTML 不缓存**：`max-age=0, must-revalidate`，所以文章改完立刻能看到。

**BOM**：所有文件必须是 UTF-8 **无 BOM**。`_headers` 带 BOM 会让部署直接失败（`Expected a path before headers`）。构建脚本用 `encoding="utf-8"` 写出，符合要求；但如果你用别的编辑器手改 `static/` 里的文件，注意别存成「UTF-8 with BOM」。
