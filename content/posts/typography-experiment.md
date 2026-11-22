---
title: 把博客当成排版实验
dek: 这套排版的字、行、卡片与交互
excerpt: 这套博客的字号、行宽、代码块、卡片和交互，都是在这里一次一次试出来的。第一篇写的就是这套排版本身：行宽为什么定在 40 字，代码块为什么不做成插图，卡片的三种语气分别用在哪。
date: 2026-11-21
minutes: 9
tag: 排版
note: 改了三遍。发现哪里读着别扭，那多半是真的别扭。
---

每个技术博客的第一篇都有个标准开法：写一段 Hello World，再解释一下为什么终于决定开始写。我不打算写那个。一个博客最难的部分从来不是内容，是**排版**——内容你总有一天会写完，排版却要在每一篇文章里反复兑现。

## 行宽是被眼睛决定的 {#measure}

阅读时眼睛不是匀速扫过去的，而是一次抓四五个字，跳到下一个落点，读完行尾再回扫到下一行开头。行越长，回扫距离越大，越容易串行。这不是审美偏好，是生理限制——Bringhurst 给的经典区间是英文 45–75 个字符，中文换成 30–40 字。

<div class="demo" data-demo>
              <div class="demo__bar">
                每行字数：<b data-out>34</b> 字 · 拖动下面的滑块试试
              </div>
              <div class="demo__stage">
                <p data-stage>
                  排版的目标不是让页面好看，而是让阅读不费力。行宽决定眼睛要回扫多远，行高决定视线跳下行的落差，字号决定这两件事的绝对值。
                </p>
              </div>
              <div class="demo__controls">
                <input type="range" min="20" max="52" value="34" data-in aria-label="每行字数" />
              </div>
            </div>

把滑块推到 50 以上，你会开始“读丢”——不是读不懂，是眼睛找不到下一行的开头。这就是为什么把正文铺满 4K 显示器是个坏主意。

> Typography is the craft of endowing human language with a durable visual form.
>
> — Robert Bringhurst

## 代码块不是插图 {#code}

技术博客的代码块是正文的一部分。读者常常跳过解释、直接从代码开始读，所以它得能站住：有文件名、有语言、能一键复制。

```css css/tokens.css
:root {
  --measure: 74ch;    /* 约 40 个汉字 */
  --line-height: 1.7; /* 中文要比英文松 */
}

.prose p {
  max-width: var(--measure);
  line-height: var(--line-height);
  text-wrap: pretty;
}
```

<div class="callout callout--warn">
              <span class="callout__badge" aria-hidden="true">⚠️</span>
              <p>
                <strong>长行：滚动还是换行？</strong>
                代码块我选横向滚动。换行会把一行逻辑拆成两行，缩进全乱，
                读者复制出去还得手动拼回去。
              </p>
            </div>

## 卡片的三种语气 {#cards}

同一段话，用不同颜色的框包起来，读者对它的信任程度会不一样。这是有用的，也是危险的——如果一篇里出现三个以上，语气就贬值了。

表：元素的使用条件
| 元素 | 什么时候用 | 什么时候别用 |
|---|---|---|
| 代码块 | 有可复制的实现 | 只有一行命令 |
| 卡片 | 三条以上并列信息 | 只有一条 |
| 交互 | 参数变化会改变结论 | 结论是固定的 |

这张表还没写完。写到第十篇的时候，我大概会想删掉其中的一半——那说明它起作用了。
