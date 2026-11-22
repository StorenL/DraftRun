# 部署清单 · draft.run

站点是纯静态的：4 个页面 + 1 个样式表 + 1 个 SVG，没有构建步骤，没有依赖。

## 目录

```
deploy/
  wrangler.toml      Cloudflare Workers 配置
  DEPLOY.md
  .gitattributes
  public/            这里面的东西才会被发布
    index.html       首页
    post.html        文章页
    404.html         找不到页面
    style.css        全部样式
    favicon.svg      站点图标
    rss.xml          订阅源
    robots.txt
    sitemap.xml
    _headers         响应头配置
```

为什么用 `public/`：发布目录指向它，仓库里的 `DEPLOY.md`、`wrangler.toml` 就不会被当成静态文件公开。

## 一、推到 GitHub

```bash
cd deploy
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin git@github.com:<你的账号>/draft-run.git
git push -u origin main
```

仓库里只放 `deploy/` 里的东西。**不要把 v2/v3/v4/v5、tools、preview 推上去**，那些是草稿。

## 二、Cloudflare Pages

1. Cloudflare 控制台 → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**
2. 选刚才那个仓库
3. 构建设置：
   - Framework preset: **None**
   - Build command: **留空**
   - Build output directory: **`/`**
4. Save and Deploy

首次部署约 30 秒，会给你一个 `xxx.pages.dev` 的临时地址，先验一遍。

## 三、绑自己的域名

`draft.run` 是根域，**必须把 DNS 托管到 Cloudflare**（根域不能设 CNAME）。

1. Cloudflare → **Add a site** → 输入 `draft.run` → 选 **Free**
2. 它扫描现有 DNS 并导入。**先自己导一份原记录备份**，重点确认 MX（邮件）和 TXT（验证记录）
3. 拿到两个 NS，形如 `xxx.ns.cloudflare.com`
4. 去域名注册商改 NS
5. 等状态变 **Active**（几分钟到 24 小时）
6. Pages 项目 → **Custom domains** → 添加 `draft.run` 和 `www.draft.run`
7. SSL/TLS 模式确认是 **Full (strict)**

## 四、上线后检查

- [ ] `https://draft.run/` 能开
- [ ] `https://www.draft.run/` 能开（或在 Rules 里设 301 跳到裸域）
- [ ] `https://draft.run/rss.xml` 返回 XML，不是 404
- [ ] `https://draft.run/不存在` 返回 404 页面
- [ ] 手机开一次，暗色模式切一下
- [ ] 用 [PageSpeed Insights](https://pagespeed.web.dev/) 跑一次

## 五、每次更新

```bash
git add .
git commit -m "新文章"
git push
```

Cloudflare 自动构建，几十秒后生效。

## 注意

- **`.run` 能不能备案**：需要查工信部批复列表。如果不在列表里，就和我之前说的 `.dev` 一样，只能海外托管，国内访问会绕路。
- **缓存**：`_headers` 里 CSS 设了 `immutable` + 一年。**以后改 `style.css` 必须改文件名**（比如 `style.v2.css`），否则老访客会一直用旧缓存。改文件名记得同步改两个 HTML 里的 `<link>`。
- **换域名**：如果以后换域名，要改的地方是 `rss.xml`（4 处）、两个页面的 `canonical` 和 `og:url`、`sitemap.xml`、`robots.txt`，共 5 个文件。