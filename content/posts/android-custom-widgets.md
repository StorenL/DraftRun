---
title: 从 onMeasure 到 MeasurePolicy：Android 自定义控件的两条路
dek: View 与 Compose 两套测量模型的对照
excerpt: 一个评分环，先用 ProgressBar 改，两天后 QA 提了三个问题。文章从这次返工出发，把 View 的 onMeasure 与 Compose 的 MeasurePolicy 两套测量模型摊开对比，再走一遍绘制、触摸、无障碍和两者互操作。
date: 2026-11-23
time: 08:16
minutes: 70
tag: Android
hot: true
note: 文中代码是教学示例，省略了 import 与日志；版本口径见文末。
---
![封面：从 onMeasure 到 MeasurePolicy](/images/android-custom-widgets/00-cover.webp)

设计评审结束，PR 最终停在两个文件上：`RingProgressView.kt` 和 `ring_progress.xml`。

需求不复杂。一个 120dp 的评分环：底环浅灰，进度是从青到紫的渐变，末端圆头；环心放分数；手指按住环拖动，分数跟着走，松手后弹回最近的整数。设计稿旁边只有三行标注，但它对实现提了三个不轻的要求：尺寸要能跟着容器变，进度要能用手改，读数要能被无障碍服务念出来。

中间那版是拿 `ProgressBar` 改的：`progressDrawable` 换成一个 `layer-list`，外面套一个旋转的 `View` 当圆头，中间盖一个 `TextView`。代码很短，短到评审时没人细看。两天后 QA 提了三个问题：

- 横屏下，环被拉成椭圆；
- TalkBack 划过去只念 “ProgressBar”，不念分数；
- 列表滑出去再滑回来，分数变成 0。

三个问题分别对应三份契约：**测量**、**语义**、**状态**。原来那套实现靠系统控件履行这三份契约，只换了它的皮；皮下面的结构一旦对不上，问题就从这三处冒出来。

这也是“自定义控件”最容易被误解的地方。它经常被当成一个画图问题——会不会 `Canvas.drawArc`，会不会算路径。画当然要会，但画只是四个动作里的一个。一个控件从被创建到被手指点中，要依次回答四个问题：我该多大、我该在哪、我长什么样、这一下算谁的。Android 把这四个问题拆成 `measure`、`layout`、`draw` 和触摸分发；Compose 把它们拆成组合、布局、绘制和 `pointerInput`。两套体系的名字不同，问题的形状是同一个。

下面用一个圆环和一个流式标签布局，把这两套体系各走一遍。每个动作先讲 View 侧怎么发生、代码怎么写，再讲 Compose 侧对应的机制，最后把两边的概念摆在一张桌子上对照。中间会顺带聊 XML 属性、主题、无障碍、动画、性能、测试，以及一个在真实项目里绕不开的问题：两套世界怎么接在一起。

代码是 Kotlin，省略 import 和日志。View 侧的基线是 API 26 以上（Android 8.0），Compose 侧是 1.13（BOM 2026.11.01，minSdk 23，要求 Kotlin 2.2+ 和 compileSdk 37.1）；涉及版本差异的地方会单独标出来。文中“View 体系”包括 `android.view.View`、`ViewGroup`、AppCompat 和 Material Components；`Compose` 指 Jetpack Compose 的 UI、Foundation、Material3。

## 1. “自定义”有六个档位，它们不是一条难度阶梯

“我要一个自定义控件”这句话，在不同的人嘴里指的是完全不同的事。有人指的是把 `ProgressBar` 的颜色改掉，有人指的是从 `View` 派生出四五百行。把这两件事都叫“自定义”，沟通就会失真。

我更愿意把它分成六档。从最便宜到最贵，每一档接手的东西不一样：

| 档位 | 做法 | 典型例子 | 你额外要负责的 |
|---|---|---|---|
| 一、配置 | style、theme、`declare-styleable` 改写 | 改主题色、圆角、字号 | 几乎没有 |
| 二、组合 | `include` / `merge` / 组合封装成一个小控件 | 头像 + 昵称 + 徽标 | 状态同步、内边距传染 |
| 三、继承 | 继承现有控件，补 `onDraw`、`onTouchEvent`、`onMeasure` | 带清除按钮的输入框 | 父类行为、绘制顺序、点击区域 |
| 四、自绘 | 继承 `View`，自己完成测量和绘制 | 评分环、图表、刻度尺 | 测量语义、绘制、无障碍、状态保存 |
| 五、自定义布局 | 继承 `ViewGroup`，自己完成子 View 的测量和摆放 | 流式标签、瀑布流、环绕布局 | 子 View 的尺寸约束、RTL、margin、动画 |
| 六、自定义交互 | 自己处理触摸、手势、嵌套滚动 | 拖拽排序、双指缩放、惯性滑动 | 事件竞争、触摸阈值、无障碍的替代操作 |

这张表最上面三档是“借用系统的语义”，最下面三档是“自己定义语义”。档位越高，代码不一定越多，但你需要知道的事情明显增长。一个自绘控件如果只画不摸、不存状态、不读屏，四五十行就能写完；一旦要满足一个合格控件的全部要求，同样的视觉可能膨胀到三四百行，而多出来的部分几乎都跟画图无关。

所以动手之前，先问三个问题，顺序不要颠倒：

第一，**现有控件加上配置和组合，能不能在不动测量和绘制的前提下表达这个需求？** 能，就停在前两档。很多“自定义控件”最后做成了主题样式和几个 `include`，这不是偷懒，是正确的最小解。

第二，**需要改变的是“外观”还是“尺寸与位置”？** 只改外观，继承并重写 `onDraw` 就够；一旦元素的尺寸或相对位置依赖兄弟节点、可用空间、内容长度，测量和布局就得自己接管，这就是第四、五档的分界线。

第三，**需要改变的是“输入”吗？** 手指落在控件上之后发生什么，和控件长什么样是两套逻辑。很多控件只在交互层需要定制，视觉完全可以复用现成组件。

还有一个常被忽略的维度：这个控件是**设计系统里的一份子**，还是**一次性的视觉需求**？前者会被几十个页面复用、被设计师反复改、被 QA 反复回归、被无障碍扫描覆盖，值得认真写；后者如果只出现一次，用组合 + 少量自绘拼出来，通常比造一个新控件更划算。控件是会长大的代码——第一版只管画，第二版加动画，第三版加拖拽，第四版发现要支持 RTL 和深色模式。写之前想清楚它会不会活到第四版，能省下很多返工。

![自定义控件的六个档位与成本](/images/android-custom-widgets/01-levels.webp)

> 图把六个档位按“系统语义 → 自定义语义”排成两行，每张卡片标了做法、例子和“额外负责”的部分；每张卡片右侧的六格是“接手程度”，格数越多，系统替你做的越少。图不是难度阶梯，而是责任清单。

### 这篇文章的两个样本

只讲概念会飘，所以准备两个体量可控、但能把四个动作都跑一遍的控件。

**`RingProgressView`**：自绘 View。它管自己的测量（希望是个正方形，但父容器给的约束优先）、自己的绘制（轨道弧、渐变进度弧、圆头、中心分数）、自己的触摸（按角度拖动）、自己的动画（松手回弹），还要在 XML 里可配置（颜色、线宽、起始角度）。Compose 侧的对应实现是 `RingProgress`，用 `Canvas` 画、用 `pointerInput` 接手势。

**`TagFlowView`**：自定义 ViewGroup。它管子 View 的测量（每个标签按内容宽度测量）、换行、行高、行内间距，以及“最后一行不满时左对齐还是两端对齐”这类细节。Compose 侧对应的是 `TagFlow`，用 `Layout` + `MeasurePolicy` 实现同一个布局规则。

选这两个，是因为它们分别落在“自绘”和“自定义布局”两档，而且都是真实项目里反复出现的东西——评分环是仪表盘、健康、问卷类应用的常客；流式标签在筛选、搜索历史、话题列表里到处都是。更重要的是，它们的需求足够小，可以在一篇文章里写完整，又足够真实，能暴露那些文档里不写的坑。

## 2. 一帧里的四次心跳

先看 View 体系。屏幕上的每一次变化，最后都落到 `ViewRootImpl.performTraversals()` 里。这个方法由 `Choreographer` 在 vsync 信号到来时回调，依次做三件事：测量、布局、绘制。加上触摸分发，就是控件在一帧里的四次心跳。

调用链大致是：

```text
VSYNC
  → Choreographer.doFrame()
    → ViewRootImpl.mTraversalRunnable
      → ViewRootImpl.performTraversals()
        → performMeasure(childWidthMeasureSpec, childHeightMeasureSpec)
        → performLayout(lp, mWidth, mHeight)
        → performDraw()
```

`performMeasure()` 调用根 View 的 `measure()`，`measure()` 又调用 `onMeasure()`；`performLayout()` 调用根 View 的 `layout()`，`layout()` 再调用 `onLayout()`；`performDraw()` 从根 View 开始，沿着视图树把每一层画到 `Canvas` 上。自定义控件重写的，就是这条链上留给你的那几个钩子：`onMeasure`、`onLayout`、`onDraw`、`dispatchTouchEvent` 或 `onTouchEvent`。

这里有个容易混淆的点：`measure()` 和 `layout()` 在 `View` 里是 `final` 的，你重写不了，也不该重写。它们是框架维护的入口，内部要处理缓存、标志位、光学边界、RTL 等一堆事情；`onMeasure()` 和 `onLayout()` 才是留给子类的空位。`draw()` 不是 `final`，但也不建议直接重写——它负责背景、内容、前景、滚动条的先后顺序，重写容易漏掉其中一层，正确的位置是 `onDraw()`（以及 `dispatchDraw()`，如果你想控制子 View 的绘制）。

### `requestLayout` 和 `invalidate` 不是一回事

控件在运行中改变自己，无非两条路：告诉父容器“我要重新量一遍”，或者“我这一块脏了，重画一下”。对应两个方法，很多人用混。

`requestLayout()` 给当前 View 打上 `PFLAG_FORCE_LAYOUT` 标志，然后沿着 `mParent` 一路往上冒泡，直到 `ViewRootImpl`，最终 `scheduleTraversals()` 安排一次新的遍历。它的语义是：**尺寸或位置可能变了，从我这棵子树开始重新测量、重新布局**。下一次 `measure()` 会忽略测量缓存，强制调用 `onMeasure()`。

`invalidate()` 给当前 View 打上脏标记，然后沿父链向上传递。硬件加速下走的是 `onDescendantInvalidated` 快路径，一路传到 `ViewRootImpl`，由它把窗口脏区并集记下来、安排一次 traversal；而真正“重录 display list”发生在绘制阶段，由每个 View 自己的 `RenderNode` 承担。所以“冒泡的终点”和“重绘发生的地方”不是同一个东西，写自定义 ViewGroup 时别把这两件事混在一起。

两者的实际差别可以用一张表说清楚：

| | `requestLayout()` | `invalidate()` |
|---|---|---|
| 触发的阶段 | measure + layout（+ draw） | draw |
| 冒泡终点 | `ViewRootImpl`（记录窗口脏区） | `ViewRootImpl`（同样记录脏区） |
| 测量缓存 | 清空，强制重测 | 不影响 |
| 典型场景 | 改 padding、改字号、增删子 View、内容宽度变化 | 改颜色、改进度、动画位移、重绘 |
| 每帧调用 | 尽量避免 | 动画里很正常 |
| 跨线程 | 必须主线程 | 可 `postInvalidate()` |

一个常见的 bug 是：动画里每一帧都调 `requestLayout()`。比如用 `ValueAnimator` 改一个影响宽高的属性，然后每帧请求重新布局。测量和布局的代价远大于绘制，动画应当是绘制阶段的事——改 `translationX`、`scaleX`、`alpha` 这类属性只触发 `invalidate`，帧率会好很多。反过来，在 `onDraw()` 里修改会影响尺寸的状态（比如根据文字长度调整自身宽度），则是在绘制阶段干了测量的活，轻则画面延迟一帧，重则触发“测量中请求布局”的警告和一次多余的遍历。

`invalidate` 的冒泡链再补一句。现代 Android 上，硬件加速的 `ViewGroup` 走的是一条叫 `onDescendantInvalidated` 的快路径：子 View 把脏标记交给 `ViewGroup`，`ViewGroup` 继续往上传，直到 `ViewRootImpl` 把窗口脏区并集记下来并安排一次 traversal。这条入口是 **API 26** 才加入的；`ViewGroup.invalidateChild()` 和 `invalidateChildInParent()` 在较新的源码里已经被标记为弃用。自己写 ViewGroup 时不要重写这两个方法，想影响脏区就在子 View 上正常 `invalidate()`。

跨线程刷新有两个容易混的 API：`postInvalidate()` 只是把一次 invalidate 投递到主线程消息队列，不保证对齐 vsync，可能落在一帧的中间；`postInvalidateOnAnimation()` 则把 invalidate 挂到 `Choreographer` 的动画回调上，下一帧开始时一定能看到脏区。自定义动画、手指跟随、惯性滑动这类“每帧都要”的场景，用后者。

### 一次遍历里，并不是每个控件都会走完四步

`performTraversals()` 很忙，但也很会省。它只在需要的时候测量：窗口尺寸变了、`requestLayout` 被调用过、可见性变了、第一次显示。布局同理，只有测量结果或布局请求发生变化才重新摆放。绘制更省——如果一棵子树没有任何脏区域，它的 `draw()` 可能根本不会被调用。

这也是为什么 `onDraw()` 里不能假设“每帧都会来”。有人在 `onDraw()` 里递减动画计数器，结果页面静止时动画也停了；有人把状态初始化写在 `onDraw()` 里，第一次显示时正常，第二次显示就错乱。`onDraw()` 是“画”的函数，不是“更新”的函数。更新应该发生在状态变化的地方：`setter`、动画回调、`onTouchEvent`。

`ViewRootImpl` 也不是每帧都从头跑一遍三阶段。首帧会强制走完整流程；之后 measure 只在窗口尺寸、insets、配置变化或 `mLayoutRequested` 等条件成立时才进入；draw 是否真正执行还要看这一帧有没有脏区，`performDraw()` 返回 `false` 就表示这帧没画。理解这一点，才能解释为什么“我明明改了属性，怎么没触发重绘”——属性改了但没标脏，或者脏区被判定为不需要重画。

### 测量的缓存与 `layout` 的 `changed` 参数

`View.measure()` 里有两层“不测”的逻辑，经常被混成一句“有缓存”。第一层是早退：如果 `PFLAG_FORCE_LAYOUT` 没有置位，并且这次的 `widthMeasureSpec`、`heightMeasureSpec` 与上次完全相同，`measure()` 会直接返回，连 `onMeasure()` 都不调用。第二层才是缓存：spec 和上次不同，但恰好等于更早测过的某一组，`measure()` 会从 `mMeasureCache` 里回填尺寸；同时置一个 `PFLAG3_MEASURE_NEEDED_BEFORE_LAYOUT`，到 `layout()` 开头再用那组 spec 补一次 `onMeasure()`。

这解释了为什么你在 `onMeasure()` 里打的日志有时只出现一次——大多数帧连早退都走不到。它也是优化测量性能的基础：只要约束没变、没有强制布局，测量就是免费的。

`layout(l, t, r, b)` 里也有类似的判断。它先比较新旧边界，得到 `changed`；然后看 `PFLAG_LAYOUT_REQUIRED` 有没有置位。只要两者之一成立，就调用 `onLayout(changed, l, t, r, b)`。所以 `onLayout` 可能在一棵子树里频繁触发，也可能因为边界没变、标志位没置而跳过。`changed` 参数不是装饰：对 `TextView` 这类控件，边界没变时可以跳过重新排版；对自定义 ViewGroup，它可以用来决定要不要重算子 View 的位置缓存。但要记住，`changed == false` 时 `PFLAG_LAYOUT_REQUIRED` 仍可能置位，所以“changed 为 false 就不摆子 View”是错的——正确的做法是每次都按当前参数摆一遍，省掉的是重复计算，不是整套布局。

### 测量的重复：为什么 `onMeasure` 会被调用两次

`onMeasure()` 被调用多次是正常的，不是 bug。常见来源有这么几类：

- **`LinearLayout` 的权重**。带 `layout_weight` 且 `layout_height="0dp"` 的子 View，在第一轮测量里会被跳过，等父容器算完剩余空间，再拿到一个 `EXACTLY` 的 spec 做第二次测量。从 targetSdk P 开始，带权重的子 View 会无条件重测，不再看“有没有剩余空间”。
- **窗口级的试测**。窗口 `wrap_content` 时，`ViewRootImpl.measureHierarchy()` 会先按 `config_prefDialogWidth` 试测一次；如果结果偏小（带 `MEASURED_STATE_TOO_SMALL`），拿 `(baseSize + desiredWidth) / 2` 再测一次；还不行才用窗口尺寸。这是同一个 `onMeasure()` 被调 2 到 3 次的框架级来源。`WindowManager.LayoutParams` 里配了 `horizontalWeight` / `verticalWeight` 时，`performTraversals()` 还会用 `EXACTLY` 再测一遍根 View（源码注释里写着 “And hey let's measure once more”）。
- **`requestLayout()` 出现在布局过程中**。`ViewRootImpl.requestLayoutDuringLayout()` 会记录请求者：在同一帧的第一次 layout pass 里，允许再补一次 measure + layout；如果第二次 pass 里又来请求，就把它 post 到下一帧，并在日志里留下 “requestLayout() improperly called by ... during second layout pass”。看到这行日志，基本可以确定某处布局逻辑在自我触发。
- **测量缓存的回填路径**。上面的早退/缓存逻辑会把测量“挪”到布局入口：`layout()` 开头发现 `PFLAG3_MEASURE_NEEDED_BEFORE_LAYOUT`，会补一次 `onMeasure()`。所以日志里看到 `onMeasure` 在 `onLayout` 前后各冒一次，不一定是布局代码有问题。
- **`RelativeLayout` 的依赖排序**。它在 `mDirtyHierarchy` 时先按依赖关系给子 View 排序，再测量；复杂依赖下开销明显。它不一定必然测两次，但“按依赖图测量”这件事本身就比线性布局贵。
- **窗口或 insets 变化**。`ViewRootImpl` 在窗口尺寸、系统栏、IME 变化时会重新测量整棵树。

正确的心态是：`onMeasure()` 必须是**幂等**的，不依赖调用次数，不产生副作用，不在里面启动动画、请求权限、发网络请求。它只回答一个问题——“在给定约束下，我想占多大”。

### Compose 侧：三个阶段，而不是四个

Compose 把同一件事拆成三个阶段：**组合（composition）→ 布局（layout）→ 绘制（draw）**。组合阶段执行可组合函数，产出 UI 树（`LayoutNode` 树）；布局阶段测量每个节点、计算位置；绘制阶段把节点画到 `Canvas` 上。没有单独的“touch 阶段”，触摸事件通过 `pointerInput` 修饰符进入协程，和布局、绘制是并行的一套机制。

这三个阶段和 View 的四次心跳最重要的区别，是**失效的传播方式**。View 体系里，一个控件改变状态，要么 `requestLayout()` 让整棵子树重测，要么 `invalidate()` 让一块区域重画，粒度由调用者决定。Compose 里，状态被读取时所在的阶段决定失效范围：

- 在**组合**阶段读 `State`，状态变了会触发重组；
- 在**布局**阶段读 `State`（比如 `Modifier.layout` 的测量块），状态变了只重新布局；
- 在**绘制**阶段读 `State`（比如 `Modifier.drawBehind` 的绘制块），状态变了只重绘。

这条规则是 Compose 性能模型的核心，也是写自定义绘制时最值得利用的东西：把“只影响外观”的状态读取放进绘制 lambda，它就不会带着整棵组件树一起重组。后面第 5 节会用这个特性改写评分环的进度。

![一帧里的四次心跳：View 与 Compose 的阶段对照](/images/android-custom-widgets/02-traversal.webp)

> 图左边是 View 体系的一次 `performTraversals`：VSYNC 之后依次测量、布局、绘制，触摸事件从窗口分发进来，和绘制是两条独立的路径。右边是 Compose 的三个阶段：组合、布局、绘制。两边的阶段配色一一对应；底部的注释点出同一个 vsync 下两套模型的差异——View 是回调链，Compose 是阶段化快照。

## 3. Measure：父与子之间的一场谈判

`onMeasure()` 是自定义控件里最容易被写错的方法。原因不在于它难，而在于很多人把它理解成“设置自己的大小”，而它实际做的事是“在父容器给的约束下，回答自己想要多大”。

`View.measure(int widthMeasureSpec, int heightMeasureSpec)` 的两个参数叫 `MeasureSpec`。它们不是尺寸，是约束。一个 `MeasureSpec` 用一个 32 位整数打包了两样东西：高 2 位是模式，低 30 位是尺寸。三种模式：

| 模式 | 含义 | 子 View 应该怎么做 |
|---|---|---|
| `EXACTLY` | 父容器已经决定了尺寸 | 必须使用这个尺寸 |
| `AT_MOST` | 父容器给出上限，子 View 可以更小 | 返回一个不超过上限的尺寸 |
| `UNSPECIFIED` | 父容器不限制，子 View 自己看着办 | 返回自己想要的大小 |

`getSize()` 在 `AT_MOST` 和 `EXACTLY` 下是上限或确定值，在 `UNSPECIFIED` 下没有意义（通常是 0，但不要依赖这一点）。`MeasureSpec.getMode()` 和 `getSize()` 的位运算在 `MeasureSpec` 内部完成，你只需要记得：**约束是父给的，尺寸是自己选的，两者都可能被父容器再次改写**。

### 父容器怎么算出给子 View 的 spec

自己写 ViewGroup 时，绕不开 `getChildMeasureSpec(int spec, int padding, int childDimension)`。它的规则很死板，但值得背下来，因为几乎所有“为什么我的控件大小不对”的问题都能在这张表里找到答案：

| 父 spec 模式 | 子 `layout_width` | 交给子的 spec |
|---|---|---|
| `EXACTLY(size)` | 固定 `48dp` | `EXACTLY(48dp)` |
| `EXACTLY(size)` | `match_parent` | `EXACTLY(size - padding)` |
| `EXACTLY(size)` | `wrap_content` | `AT_MOST(size - padding)` |
| `AT_MOST(size)` | 固定 `48dp` | `EXACTLY(48dp)` |
| `AT_MOST(size)` | `match_parent` | `AT_MOST(size - padding)` |
| `AT_MOST(size)` | `wrap_content` | `AT_MOST(size - padding)` |
| `UNSPECIFIED` | 固定 `48dp` | `EXACTLY(48dp)` |
| `UNSPECIFIED` | `match_parent` | `UNSPECIFIED(0)` |
| `UNSPECIFIED` | `wrap_content` | `UNSPECIFIED(0)` |

注意第三行和第九行：`wrap_content` 在父容器有界时拿到的是 `AT_MOST`，在父容器无界时拿到的是 `UNSPECIFIED`。所谓“无界容器里的 0 尺寸塌缩”，就是父容器处在 `UNSPECIFIED` 的环境里（`ScrollView` 的垂直方向、`HorizontalScrollView` 的水平方向、`RecyclerView` 的滚动方向），子 View 的 `match_parent`（以及 `wrap_content`）被翻译成 `UNSPECIFIED(0)`；如果自定义控件只看 `getSize()` 就返回 0，屏幕上的东西就消失了。

表里的 `UNSPECIFIED(0)` 有一个兼容性细节：这个 `size` 不是绝对保证为 0，框架对老的 `targetSdkVersion` 保留了更宽松的透传行为。写控件时不要依赖“这里一定是 0”，把它当成“没有上限也没有下限”即可。

还有一个更隐蔽的默认行为：如果自定义 View **完全不重写** `onMeasure()`，`View` 的默认实现会把 `AT_MOST` 和 `EXACTLY` 都当成 `EXACTLY` 来处理——`getDefaultSize()` 在两种模式下都返回 `specSize`。结果就是一个只重写了 `onDraw()` 的控件，`layout_width="wrap_content"` 会占满父容器给的上限，看起来像 `match_parent`。这就是另一种常被叫作“`wrap_content` 塌缩”的情况，名字和上一段的“0 尺寸塌缩”不是一回事：那个是父容器无界导致的，这个是控件没实现测量语义导致的。它也不是框架的 bug，而是“默认实现不知道你想要多大”的合理选择。自绘控件只要在乎 `wrap_content`，就必须重写 `onMeasure()`。

![MeasureSpec 的谈判：父容器怎么把约束递给子 View](/images/android-custom-widgets/03-measure-spec.webp)

> 图里把一次测量画成三栏：左边是父容器拿到的 spec，中间是 `getChildMeasureSpec` 的翻译规则，右边是子 View 实际收到的 spec 和它的选择权。底部那行小字是“无界父容器下 0 尺寸塌缩”的现场：父容器无界 → 子 View 收到 `UNSPECIFIED` → 自己不知道要多大 → 返回 0。

### 自绘 View 的 `onMeasure`

`RingProgressView` 的希望是“尽量方，尽量大，但不超出父容器”。这是自绘控件里最典型的一种测量逻辑：

```kotlin
class RingProgressView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = R.attr.ringProgressStyle,
) : View(context, attrs, defStyleAttr) {

    private val density = resources.displayMetrics.density
    private var ringWidth = 12f * density
    private var defaultDiameter = 120f * density

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        // 想要的边长：直径 + 两侧描边。描边是居中画的，会往外溢出一半。
        val desired = (defaultDiameter + ringWidth).roundToInt()

        // resolveSizeAndState 会把“想要的值”和“父容器的约束”揉在一起，
        // 并在测量结果太小的时候，把状态位一起返回。
        val measuredWidth = resolveSizeAndState(desired, widthMeasureSpec, 0)
        val measuredHeight = resolveSizeAndState(desired, heightMeasureSpec, 0)

        setMeasuredDimension(measuredWidth, measuredHeight)
    }
}
```

`resolveSizeAndState()` 的行为可以用一句话概括：`EXACTLY` 时听父容器的，`AT_MOST` 时取 `min(想要的值, 上限)`，`UNSPECIFIED` 时用想要的值；如果父容器给的尺寸比想要的还小，它会把 `MEASURED_STATE_TOO_SMALL` 这个状态位拼进返回值的高位。父容器读到这个状态位，就知道“这孩子被挤扁了”，可以决定要不要显示省略号、要不要允许滚动。`TextView` 的 `ellipsize`、`HorizontalScrollView` 的滚动条都依赖这套状态位。

`setMeasuredDimension()` 必须调用。没有调用就离开 `onMeasure()`，`measure()` 在收尾时发现 `PFLAG_MEASURED_DIMENSION_SET` 没有被置位，会立刻抛 `IllegalStateException`，消息里写着 “View with id … did not call setMeasuredDimension()”。这不是可以忽略的警告，是硬性契约。

还有两个小方法经常和它一起出现：

- `getSuggestedMinimumWidth()`：返回 `max(背景最小宽度, android:minWidth)`。自定义控件如果不打算支持 `minWidth`，可以不管；打算支持，就在 `resolveSizeAndState` 前把 desired 和它取个最大值。
- `combineMeasuredStates()`：ViewGroup 汇总所有子 View 的测量状态位时用，把每个子 View 的 `MEASURED_STATE_TOO_SMALL` 合并到自己的结果里。

### 自定义 ViewGroup 的测量：把所有孩子量一遍，然后决定自己多大

`TagFlowView` 的测量比单个 View 复杂：它要先把每个子 View 量出来，按可用宽度决定换不换行，最后算出整块内容的高度。下面这段是测量部分的核心，把行信息存进成员变量，供 `onLayout` 直接使用：

```kotlin
class TagFlowView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : ViewGroup(context, attrs, defStyleAttr) {

    private class Row {
        var used = 0      // 这一行已经占掉的宽度（含间距）
        var height = 0    // 这一行最高的子 View（含 margin）
    }

    private val rows = mutableListOf<Row>()
    private var childX = IntArray(0)
    private var childY = IntArray(0)

    var horizontalGap = dp(8f)
    var verticalGap = dp(8f)

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val widthMode = MeasureSpec.getMode(widthMeasureSpec)
        val widthSize = MeasureSpec.getSize(widthMeasureSpec)
        val contentWidth = (widthSize - paddingLeft - paddingRight).coerceAtLeast(0)

        // 父容器无界时，不换行，让内容一路排下去
        val maxRowWidth = if (widthMode == MeasureSpec.UNSPECIFIED) {
            Int.MAX_VALUE
        } else {
            contentWidth
        }

        rows.clear()
        rows += Row()
        if (childX.size < childCount) {
            childX = IntArray(childCount)
            childY = IntArray(childCount)
        }

        var y = 0
        var widestRow = 0

        for (i in 0 until childCount) {
            val child = getChildAt(i)
            if (child.visibility == GONE) continue

            // 用父容器给的 spec 和 margin，把约束翻译成子 View 的 spec
            measureChildWithMargins(child, widthMeasureSpec, 0, heightMeasureSpec, 0)

            val lp = child.layoutParams as MarginLayoutParams
            val childWidth = child.measuredWidth + lp.leftMargin + lp.rightMargin
            val childHeight = child.measuredHeight + lp.topMargin + lp.bottomMargin

            var row = rows.last()
            // 这一行已经有东西了，再放一个会超宽，就换行
            if (row.used > 0 && row.used + horizontalGap + childWidth > maxRowWidth) {
                y += row.height + verticalGap
                row = Row()
                rows += row
            }

            val left = if (row.used == 0) 0 else row.used + horizontalGap
            childX[i] = left + lp.leftMargin
            childY[i] = y + lp.topMargin

            row.used = left + childWidth
            row.height = max(row.height, childHeight)
            widestRow = max(widestRow, row.used)
        }

        val contentHeight = y + rows.last().height
        val measuredWidth = resolveSize(paddingLeft + paddingRight + widestRow, widthMeasureSpec)
        val measuredHeight = resolveSize(paddingTop + paddingBottom + contentHeight, heightMeasureSpec)
        setMeasuredDimension(measuredWidth, measuredHeight)
    }

    private fun dp(value: Float) = (value * resources.displayMetrics.density).roundToInt()
}
```

这段代码只写了测量，还差四个布局参数钩子。少了它们，XML 里写的 `layout_margin` 会在 `measureChildWithMargins()` 里直接抛 `ClassCastException`——因为框架拿到的还是默认的 `ViewGroup.LayoutParams`，转不成 `MarginLayoutParams`：

```kotlin
override fun generateDefaultLayoutParams(): LayoutParams =
    MarginLayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT)

override fun generateLayoutParams(attrs: AttributeSet): LayoutParams =
    MarginLayoutParams(context, attrs)

override fun generateLayoutParams(p: LayoutParams): LayoutParams = MarginLayoutParams(p)

override fun checkLayoutParams(p: LayoutParams): Boolean = p is MarginLayoutParams
```

这四个方法各管一条路径：`generateDefaultLayoutParams()` 管 `addView(child)` 不带 params 的情况；`generateLayoutParams(AttributeSet)` 管 XML inflate；`generateLayoutParams(LayoutParams)` 管从别的父容器转过来；`checkLayoutParams()` 管 `addView` / `updateViewLayout` 时的校验。四个都实现，`TagFlowView` 才算真正支持 margin。

回到测量那段代码，有几个决定需要单独说明。

**第一，子 View 的测量用 `measureChildWithMargins`，而不是 `measureChild`。** 前者会把 `margin` 算进约束里，后者不会；一个支持 `layout_margin` 的 ViewGroup 应该用自己的 `MarginLayoutParams`，并在 `generateLayoutParams()` 里返回它。少了这一步，子 View 的 margin 在测量阶段就会被忽略，布局阶段再用 margin 就变成“先量后挪”，很容易溢出。

**第二，`match_parent` 在这个布局里是半支持的。** 子 View 声明 `layout_width="match_parent"` 时，`measureChildWithMargins` 会把父容器的可用宽度翻译成它的 `AT_MOST` 上限，它会尽量撑满一整行。从视觉上看，它确实独占了一行；但如果你希望它“和父容器一样宽”，在某些父约束下会差一个 padding。流式布局通常建议子项用 `wrap_content`，这不是限制，是语义上的诚实：一个标签的宽度本来就该由内容决定。

**第三，`row.used` 和 `widestRow` 的单位是“内容宽度”。** 最后 `setMeasuredDimension` 时再加上 padding。很多自定义 ViewGroup 的 bug 就出在这里：测量时用了包含 padding 的宽度，布局时又加了一遍 padding，结果右边永远多出一截，或者最后一行被过早挤到新行。

**第四，测量阶段存下来的行信息，在 `onLayout` 里只能当参考。** 如果测量和布局之间父容器改了尺寸，`onLayout` 会拿到新的边界，而 `childX/childY` 还是旧的。稳妥的做法是在 `onLayout` 里用“行索引 + 行内游标”重新摆放，或者像 `TagFlowView` 这样，把 `childX/childY` 当作“内容坐标”，只在 padding 基础上平移。更严谨的版本可以在 `onSizeChanged()` 里清掉缓存，强制下一次重新测量。

### 文字也是要测量的

自绘控件里画文字，很容易忽略“文字也是一个有尺寸的对象”。`Paint.measureText()` 返回的是**前进宽度**（advance），不包含字形左右两侧的“墨迹”溢出；`Paint.getTextBounds()` 返回的是**墨迹边界**（ink bounds），它的 `top` 可能是负数。想要“把一句话水平居中在圆环里”，通常用 `measureText` 算宽度；想要“让文字的最高点和最低点刚好落在某个盒子里”，得用 `FontMetrics`：

```kotlin
val fm = textPaint.fontMetrics
val baseline = centerY - (fm.ascent + fm.descent) / 2f
canvas.drawText(scoreText, centerX, baseline, textPaint)
```

多行文本别自己算换行。用 `StaticLayout.Builder.obtain(...).setWidth(width).build()`，它处理了断行、双向文本、emoji、行距。`TextView` 内部也是这套东西。自己写文字布局，最后往往会卡在“中英文混排的断行位置和系统不一致”上。

### 几个在 `onMeasure` 里反复出现的错误

- **在 `onMeasure` 里调 `requestLayout()`。** 这是在测量阶段请求重新布局，框架会安排下一次遍历，于是你得到两次测量、两次布局，动画里还会抖。需要重新测量，应该修改状态后让父容器在合适的时机触发，而不是在测量回调里自己踢自己。
- **把 `MeasureSpec` 存到成员变量里跨帧使用。** `MeasureSpec` 只在本次测量有效，下一帧父容器的约束可能已经变了。缓存“我的尺寸”可以，缓存“父容器给的 spec”不行。
- **`wrap_content` 时返回 `MATCH_PARENT` 式的值。** 父容器给 `AT_MOST(1080)`，你返回 1080，看起来没错；但你的 View 可能只有 200 的内容，剩下的 880 是空白。`wrap_content` 的语义是“内容多大我多大”，不是“能占多少占多少”。
- **忽略 `padding`。** 自定义 View 绘制内容时通常要扣掉 `paddingLeft/Right`，否则内容会压到背景的边缘。如果控件支持 `padding`，测量、布局、绘制三处都要扣，漏一处就不一致。
- **以为 `onMeasure` 只会来一次。** 前面说过，带权重的 `LinearLayout`、`RelativeLayout`、窗口变化都会导致多次测量。`onMeasure` 必须是纯函数：给定 spec，返回尺寸，不改外部状态。

### Compose 侧：`Constraints` 和 `Modifier.layout`

Compose 的测量模型和 View 很像，只是名字换了。父节点传给子节点的是一个 `Constraints`，包含四个值：`minWidth`、`maxWidth`、`minHeight`、`maxHeight`。和 `MeasureSpec` 的三种模式相比，`Constraints` 用区间表达同样的意思：

| View 的 `MeasureSpec` | Compose 的 `Constraints` |
|---|---|
| `EXACTLY(n)` | `minWidth == maxWidth == n` |
| `AT_MOST(n)` | `minWidth == 0, maxWidth == n` |
| `UNSPECIFIED` | `maxWidth == Constraints.Infinity` |
| 子 View 可以用更小的尺寸 | 只要 `minWidth <= width <= maxWidth` |

Compose 里没有“给自己的 View 设置测量结果”的 API，测量结果由布局阶段返回。想只影响一个元素的测量，用 `Modifier.layout`：

```kotlin
fun Modifier.atMostOneThirdWidth(): Modifier = layout { measurable, constraints ->
    val maxWidth = (constraints.maxWidth / 3f).roundToInt()
    // 把新的上限交给下一层
    val placeable = measurable.measure(constraints.copy(maxWidth = maxWidth))
    // 返回自己的尺寸，并把子元素放在左上角
    layout(placeable.width, placeable.height) {
        placeable.place(0, 0)
    }
}
```

`Modifier.layout` 的 lambda 接收 `Measurable` 和 `Constraints`，返回一个 `MeasureResult`。`layout(width, height) { ... }` 这个辅助函数既返回结果，又负责校验尺寸在约束范围内。注意 `constraints.copy(maxWidth = ...)` 不会自动把 `minWidth` 压到新上限以下，如果原来的 `minWidth` 比新的 `maxWidth` 大，需要自己处理，否则 `Constraints` 会抛异常。

在这个阶段读 `State`（比如 `Modifier.layout` 里根据某个动画值算偏移），Compose 只会重新布局，不会重组。这是 View 体系里没有的精确度——View 的 `requestLayout` 至少要让整棵子树的测量标志位走一遍。

## 4. Layout：把矩形摆到该在的位置

测量回答“多大”，布局回答“在哪”。`onLayout(changed, left, top, right, bottom)` 的四个参数是父容器已经给你定好的位置，你的任务是：给每一个子 View 算出 `left/top/right/bottom`，然后调用 `child.layout(...)`。

```kotlin
override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {
    val left = paddingLeft
    val top = paddingTop

    for (i in 0 until childCount) {
        val child = getChildAt(i)
        if (child.visibility == GONE) continue
        val x = left + childX[i]
        val y = top + childY[i]
        child.layout(x, y, x + child.measuredWidth, y + child.measuredHeight)
    }
}
```

这段代码每一行都有讲究。

`child.measuredWidth` 是测量阶段的结果，不是 `child.width`。后者的含义是“上一次布局后的宽度”，在 `onLayout` 里还没更新。用错了不会立刻报错，但会出现“子 View 尺寸慢一帧”的诡异现象。

`childX[i]` 存的是内容坐标（不含 padding），加上 `paddingLeft` 才是父容器坐标系里的位置。前面测量代码里已经加过 `margin`，所以这里不再加。如果测量和布局对 margin、padding 的理解不一致，就会出现“测量时留了边距，布局时又留了一遍”的双重边距。

`child.layout()` 会触发子 View 自己的 `onLayout`（如果是 ViewGroup）和 `invalidate`。它内部先比较新旧边界，边界变了或者 `PFLAG_LAYOUT_REQUIRED` 置位，才继续往下走。自定义 ViewGroup 不要在 `onLayout` 里调用 `requestLayout()`，理由和 `onMeasure` 里一样：你正在布局，别再要求布局。

### 行信息从测量传到布局

回到 `TagFlowView`。测量阶段已经把每个子 View 的目标左上角算好，存在 `childX/childY` 里，`onLayout` 只负责平移 padding 并调用 `child.layout()`。这个写法能成立，前提是“一次测量对应一次布局”。如果父容器在测量和布局之间改了尺寸（比如软键盘弹出导致窗口高度变化），`onLayout` 拿到的边界和测量时的假设不一致，缓存的行位置可能就过期了。

更稳妥的写法是在 `onLayout` 里根据行索引重算。但重算需要知道每一行从哪个子 View 开始、宽度多少、行高多少，也就是把测量阶段的行信息完整保留下来，而不是只保留每个子 View 的坐标。两种写法没有绝对优劣：前者快但依赖“测量后立即布局”，后者多几行代码但更抗变化。对于会被放进 `RecyclerView`、会随窗口变化的控件，我倾向于后者；一次性静态页面，前者足够。

![TagFlow 的测量与布局：一行是怎么被摆出来的](/images/android-custom-widgets/04-layout-flow.webp)

> 图里把 `TagFlowView` 的一次 `onMeasure`/`onLayout` 拆成两趟：第一趟从左到右累加宽度，超宽就换行，记录每一行的高度和每个子项的“内容坐标”；第二趟按行把 `childX/childY` 平移 padding 后调用 `child.layout()`。左栏底部的三张便签是三个边界：`GONE` 的子项要跳过、最后一行的行高要参与总高度、`match_parent` 的子项会独占一行。

### `changed` 参数不是噪音

`onLayout` 的 `changed` 表示“这个 View 的边界相对上一次变没变”。`TextView` 会用它在边界不变时跳过重排；自定义 ViewGroup 可以用它来跳过“重新计算子 View 位置缓存”这种开销较大的工作。但要注意，`changed == false` 不代表子 View 不需要重新布局——子 View 自己的内容变了（比如文字变了），可能仍然需要重新摆放。所以很多控件选择忽略 `changed`，每次都完整走一遍 `onLayout`。这个选择在子 View 数量不多时完全没问题，在几百个子 View 的列表里才需要认真优化。

### RTL：`layout` 不知道“方向”

`View.layout(left, top, right, bottom)` 的坐标系永远是“绝对左”。从右到左的语言里，一个“图标在左、文字在右”的布局要整个镜像过来，但 `layout()` 不会帮你做这件事。需要你根据 `layoutDirection` 自己算。

```kotlin
override fun onRtlPropertiesChanged(layoutDirection: Int) {
    super.onRtlPropertiesChanged(layoutDirection)
    requestLayout()
}
```

几个配套 API：

- `View.getLayoutDirection()` 返回 `LAYOUT_DIRECTION_LTR` 或 `LAYOUT_DIRECTION_RTL`；
- `paddingLeft/paddingRight` 是绝对边距，`paddingStart/paddingEnd` 是逻辑边距；写自定义控件时优先读 `paddingStart/paddingEnd`，再用 `ViewCompat` 兼容旧版本；
- `MarginLayoutParams.leftMargin/rightMargin` 是绝对值，`marginStart/marginEnd` 是逻辑值；
- 在 `onLayout` 里，如果希望子 View 的起点和文字方向一致，用 `layoutDirection` 判断，然后决定从左边还是右边开始摆。

Compose 的 `placeRelative(x, y)` 会自动处理 RTL。这是两套体系的一个明显差异：View 的 `layout()` 没有“相对方向”的概念，`layoutDirection` 只是一个属性，需要你自己消费；Compose 把“相对摆放”做成了布局 API 的一部分。

### 基线、层叠与“谁盖住谁”

`onLayout` 只决定平面上的位置。谁盖住谁由另外两样东西决定：**子 View 的添加顺序**和**Z 轴**。后添加的子 View 默认画在上面；`View.setZ()` / `elevation` 会把 View 提升到更高的绘制层，同时带阴影。自定义 ViewGroup 如果要做“重叠卡片”效果，除了算坐标，还要想清楚索引顺序和 `clipChildren`：父容器的 `clipChildren` 默认是 `true`，超出边界的子 View 会被裁掉，做环绕布局或者放大的动画时，通常要 `clipChildren = false`。

### Compose 侧：`Layout` 与 `MeasurePolicy`

Compose 里写自定义布局，入口是 `Layout` 可组合函数。最常用的单 content 重载（1.0.0 起）长这样：

```kotlin
@Composable
@UiComposable
inline fun Layout(
    content: @Composable @UiComposable () -> Unit,
    modifier: Modifier = Modifier,
    measurePolicy: MeasurePolicy,
)
```

从 1.0 到 1.13，`Layout` 一共有三个重载：1.0 的单 content 版本、1.1 的零子节点版本、1.10 的多 content 版本（配合 `MultiContentMeasurePolicy`，一次布局多组内容）。平时写的 `Layout(content) { measurables, constraints -> ... }` 用的是单 content 版本，尾随 lambda 通过 `MeasurePolicy` 这个函数式接口转换而来，不是 `Layout` 自己的函数类型。这一点和 View 侧“重写一个方法”的直觉不太一样：在 Compose 里，布局策略是一个对象，`Layout` 只是把它接到组合树上。

`MeasurePolicy` 只有一个必须实现的方法 `measure`，另外四个是可选的内禀尺寸方法：

```kotlin
fun interface MeasurePolicy {
    fun MeasureScope.measure(
        measurables: List<Measurable>,
        constraints: Constraints,
    ): MeasureResult

    fun IntrinsicMeasureScope.minIntrinsicWidth(
        measurables: List<IntrinsicMeasurable>, height: Int,
    ): Int = ...
    // maxIntrinsicWidth / minIntrinsicHeight / maxIntrinsicHeight 同理
}
```

用 `Layout` 把 `TagFlow` 写出来：

```kotlin
@Composable
fun TagFlow(
    modifier: Modifier = Modifier,
    horizontalGap: Dp = 8.dp,
    verticalGap: Dp = 8.dp,
    content: @Composable () -> Unit,
) {
    Layout(content = content, modifier = modifier) { measurables, constraints ->
        val hGap = horizontalGap.roundToPx()
        val vGap = verticalGap.roundToPx()
        val maxWidth = if (constraints.hasBoundedWidth) constraints.maxWidth else Constraints.Infinity

        // 先量一遍：每个子项拿到同样的宽度上限
        val placeables = measurables.map { measurable ->
            measurable.measure(Constraints(maxWidth = maxWidth))
        }

        // 再决定换行：这里只算位置，不再测量
        val rows = mutableListOf<MutableList<Int>>()   // 每行放 placeable 的下标
        val rowWidths = mutableListOf<Int>()
        val rowHeights = mutableListOf<Int>()
        var rowWidth = 0
        var rowHeight = 0

        placeables.forEachIndexed { index, placeable ->
            val gap = if (rowWidth == 0) 0 else hGap
            if (rowWidth > 0 && rowWidth + gap + placeable.width > maxWidth) {
                rowWidths += rowWidth
                rowHeights += rowHeight
                rows += mutableListOf<Int>()
                rowWidth = 0
                rowHeight = 0
            }
            val effectiveGap = if (rowWidth == 0) 0 else hGap
            if (rows.isEmpty()) rows += mutableListOf()
            rows.last() += index
            rowWidth += effectiveGap + placeable.width
            rowHeight = max(rowHeight, placeable.height)
        }
        if (rowWidth > 0) {
            rowWidths += rowWidth
            rowHeights += rowHeight
        }

        val contentWidth = rowWidths.maxOrNull() ?: 0
        val contentHeight = rowHeights.sum() + vGap * (rowHeights.size - 1).coerceAtLeast(0)

        layout(
            width = constraints.constrainWidth(contentWidth),
            height = constraints.constrainHeight(contentHeight),
        ) {
            var y = 0
            rows.forEachIndexed { rowIndex, indices ->
                var x = 0
                indices.forEach { index ->
                    val placeable = placeables[index]
                    placeable.placeRelative(x, y)
                    x += placeable.width + hGap
                }
                y += rowHeights[rowIndex] + vGap
            }
        }
    }
}
```

这段代码比 View 版本短，但信息量不小。

**`measurables.map { }` 会触发子项的测量。** 每个 `Measurable` 只能被测量一次，重复测量同一个 `Measurable` 在 Compose 里是错误。要测两次，必须用 `SubcomposeLayout` 重新组合出新的 `Measurable`，或者在外层先测量再传递结果。

**`placeRelative` 而不是 `place`。** 前者会读取 `LayoutDirection`，在 RTL 下自动镜像 x 坐标。如果布局有方向语义（比如“第一个标签在右边”），用 `placeRelative`；如果坐标是物理的（比如画布内部的绝对坐标），用 `place`。

**`constraints.constrainWidth/Height` 是把结果夹回约束范围。** 虽然 `layout()` 不会在运行时强制检查（不同版本行为略有差异），但超出约束会导致子元素被父容器裁剪或者布局异常。养成习惯，永远夹一下。

**内禀尺寸（intrinsics）默认是按“理想情况”估的。** 如果调用方用了 `Modifier.width(IntrinsicSize.Max)` 或者 `IntrinsicSize.Min`，框架会调用 `MeasurePolicy` 的 `maxIntrinsicWidth` 等方法。默认实现对所有子项取最大值（对宽度）或求和（对高度），对流式布局不完全准确——单行时宽度是求和，多行时又受父宽限制。需要精确支持的话，得重写这四个方法。不重写不会崩，但 `IntrinsicSize` 的调用方会得到偏差。

![Compose 自定义布局：从 MeasurePolicy 到 place](/images/android-custom-widgets/05-compose-layout.webp)

> 图里把一次 Compose 布局拆成三段：`measurables` 拿着父约束去测量、`MeasurePolicy` 计算行列、`placementBlock` 把 `Placeable` 放到坐标上。注意 `MeasureResult` 不只是宽高，它还携带基线（`alignmentLines`）和摆放逻辑；`layout(width, height) { ... }` 只是最常用的语法糖。

### `Layout` 和 `Modifier.layout` 的分工

初学者容易把这两个混在一起。区别在于“谁负责容器”：

- `Layout` 是一个**容器**：它接收一组子元素，负责测量和摆放所有子元素；
- `Modifier.layout` 是一个**修饰符**：它只作用于被修饰的那一个元素，改变它拿到的约束或它在父容器里的位置。

写 `TagFlow` 这种“我有多个孩子”的控件，用 `Layout`；写“这个元素最多占三分之一宽度”这种单元素规则，用 `Modifier.layout`。两者可以组合：`Layout` 内部的子元素也可以带 `Modifier.layout`。

### `SubcomposeLayout`：当“要组合什么”取决于测量结果

有些布局在测量之前不知道自己需要哪些子元素。最典型的是 `LazyColumn`：要显示哪些 item，取决于滚动位置和可用高度；而可用高度要到测量阶段才知道。这种场景用 `SubcomposeLayout`：它在测量阶段才执行子组合，拿到 `Measurable` 再测量。

```kotlin
SubcomposeLayout { constraints ->
    val content = subcompose("content") {
        Text("这行文字要等宽度确定后才能决定怎么排")
    }
    val placeable = content.first().measure(constraints)
    layout(placeable.width, placeable.height) { placeable.place(0, 0) }
}
```

`SubcomposeLayout` 很强，但代价也真实：子组合发生在测量阶段，会带来额外的组合开销；如果slot 不稳定，还会反复组合。官方基础组件里，`LazyColumn`、`TabRow`、`Scaffold` 的某些实现都用了它。自己写业务控件时，能不用就不用；用之前先确认“真的必须在测量阶段才知道要组合什么”。

### Compose 里没有 `changed` 参数，但有 `LookaheadScope`

Compose 的布局函数没有“上一次的边界”这种入参。需要“测量前先知道最终位置”的场景（比如共享元素动画、基于目标尺寸做过渡），用 `LookaheadScope`：它在真正布局之前先跑一遍“预布局”，把每个节点的目标位置算出来，动画可以读取这个目标位置。

这个能力在 View 体系里没有直接对应物。View 里做类似的事情，通常要在 `preDraw` 里手动算一遍目标位置，或者用 `ViewTreeObserver` 监听布局完成。Compose 把它做进了框架。

### 一张对照表

| View 体系 | Compose | 说明 |
|---|---|---|
| `MeasureSpec` + 三种模式 | `Constraints`（min/max 区间） | 都是父给子的约束 |
| `onMeasure()` | `MeasurePolicy.measure()` | 测量入口 |
| `setMeasuredDimension()` | `layout(width, height)` | 提交自己的尺寸 |
| `onLayout()` | `MeasureResult.placementBlock` | 摆放子元素 |
| `child.layout(l, t, r, b)` | `Placeable.place(x, y)` / `placeRelative` | 摆放一个子元素 |
| `MarginLayoutParams` | `Modifier.padding` / `offset` / `LayoutModifierNode` | 边距与位置修正 |
| `getBaseline()` | `alignmentLines` | 基线对齐 |
| `getChildMeasureSpec()` | `Constraints.copy()` / `constrain*()` | 约束翻译 |
| `requestLayout()` | 状态变化 → 重新布局 | 失效机制 |
| `ViewGroup` | `Layout` + `MeasurePolicy` | 自定义容器的两种写法 |
| `RelativeLayout` 的依赖测量 | 无直接对应 | 前者是“子项已知、按依赖排序多趟测量”；后者是“测量期才知道要组合什么”，问题不同 |
| 内禀尺寸（`View` 无直接对应） | `IntrinsicMeasureScope` + `IntrinsicSize` | Compose 独有的提前测量能力 |

到这里，控件的“骨架”就立起来了：它知道自己多大、孩子在哪。接下来是让它好看——画。

## 5. Draw：把像素画到该在的地方

测量和布局解决“多大、在哪”，绘制解决“长什么样”。`View.draw(Canvas)` 负责的是一条固定的流水线：

```text
View.draw(canvas)
  → drawBackground()      背景
  → onDraw()              自己画内容
  → dispatchDraw()        画子 View（ViewGroup）
  → onDrawForeground()    前景、滚动条、焦点高亮
```

自定义控件通常只重写 `onDraw()`。这是对的：背景、前景、滚动条、焦点高亮各有各的语义，把它们揉进 `onDraw()` 里，后面加个 `foreground` 就会发现被自己的内容盖住。需要控制子 View 的绘制顺序，重写 `dispatchDraw()`；需要在所有内容之上画东西（比如水印、遮罩），重写 `onDrawForeground()`。

`onDraw()` 不是每次都会调用。`View` 只有在“脏了”的时候才重画，`ViewGroup` 默认还会设置 `setWillNotDraw(true)`——它假设自己只负责摆孩子，不负责画自己。这个优化对大多数容器是对的，但会给自定义 ViewGroup 带来一个经典 bug：你在 `onDraw()` 里画了分割线，结果一条都不出现。原因就是 `WILL_NOT_DRAW` 标志位。解决方案有两个：调用 `setWillNotDraw(false)`，或者把绘制放进 `dispatchDraw()`。前者更符合直觉，后者更省一次遍历（不用额外调度自己的 `onDraw`）。分隔线、背景网格这类“容器自身的装饰”，我一般用 `dispatchDraw()` 画在子 View 之前；需要盖住子 View 的才用 `onDrawForeground()`。

### 坐标、画笔与几何

`onDraw()` 拿到的 `Canvas` 原点在 View 的左上角，x 向右、y 向下，单位是像素。所有尺寸都应该从 `width`/`height`/`paddingXxx` 推导，不要写死 dp 转像素后的常量——除非你确定控件只在一个密度下工作。

画一个圆环，几何上有两个容易踩的点：

一是**描边会往外扩**。`Paint.Style.STROKE` 的描边以路径为中心向两侧各扩 `strokeWidth / 2`。如果用一个刚好填满 `width` 的矩形去画弧，圆头会超出边界被裁掉。正确的半径是 `min(width, height) / 2 - strokeWidth / 2`。

二是**渐变要对齐弧的起点**。`SweepGradient` 的 0° 在三点钟方向，而评分环通常从十二点钟开始画。不处理的话，渐变最亮的颜色会落在弧的中间而不是起点。做法是在 `onSizeChanged()` 里创建 shader，并给它一个绕圆心的旋转矩阵：

```kotlin
private var ringRect = RectF()
private var progressPaint = Paint(Paint.ANTI_ALIAS_FLAG)
private var trackPaint = Paint(Paint.ANTI_ALIAS_FLAG)

override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
    super.onSizeChanged(w, h, oldw, oldh)

    val radius = min(w, h) / 2f - ringWidth / 2f
    val cx = w / 2f
    val cy = h / 2f
    ringRect.set(cx - radius, cy - radius, cx + radius, cy + radius)

    // 轨道：一条完整的弧
    trackPaint.apply {
        style = Paint.Style.STROKE
        strokeWidth = ringWidth
        strokeCap = Paint.Cap.ROUND
        color = trackColor
    }

    // 进度：扫掠渐变。SweepGradient 的 0° 在三点钟，
    // 旋转 startAngle 度之后，色带起点就和 drawArc 的起点对齐了。
    progressPaint.apply {
        style = Paint.Style.STROKE
        strokeWidth = ringWidth
        strokeCap = Paint.Cap.ROUND
        shader = SweepGradient(cx, cy, progressColors, null).apply {
            setLocalMatrix(Matrix().apply { postRotate(startAngle, cx, cy) })
        }
    }
}
```

`onSizeChanged()` 是创建这些几何对象的好地方：尺寸变了才重建，`onDraw()` 里只管用。这也是绘制性能的第一条纪律——**不要在 `onDraw()` 里分配对象**。`RectF`、`Path`、`Paint`、`Matrix`、字符串拼接，每帧都分配会让 GC 在动画期间频繁介入，掉帧往往不是画得慢，是分配得太多。

上面用到的 `ringWidth`、`startAngle`、`trackColor`、`scoreText` 都是构造器里从 XML 属性解析出来的字段；`progressColors` 由 `progressStartColor` 和 `progressEndColor` 拼成 `IntArray`；`scorePaint` 则是构造器里按 `scoreTextSize` 配好的 `Paint`。第 8 节会补上解析过程，这里先看绘制本身。

`onDraw()` 本身可以写得很短：

```kotlin
override fun onDraw(canvas: Canvas) {
    super.onDraw(canvas)

    // 轨道
    canvas.drawArc(ringRect, startAngle, 360f, false, trackPaint)

    // 进度，从起点扫过 progress * 360°
    if (progress > 0f) {
        canvas.drawArc(ringRect, startAngle, 360f * progress, false, progressPaint)
    }

    // 中心的分数。基线要用 FontMetrics 算，不能直接 y = centerY。
    val fm = scorePaint.fontMetrics
    val baseline = height / 2f - (fm.ascent + fm.descent) / 2f
    val scoreX = width / 2f - scorePaint.measureText(scoreText) / 2f
    canvas.drawText(scoreText, scoreX, baseline, scorePaint)
}
```

注意 `super.onDraw(canvas)` 这一行。`View.onDraw()` 默认是空实现，调不调都一样；但习惯上加上，将来继承层次变化时不会漏掉父类逻辑。真正不能漏的是 `ViewGroup` 里的 `super.dispatchDraw(canvas)`——漏了子 View 就全都不见了。

这段代码还有一处刻意的省略：它没有消费 `padding`，测量和绘制都按整块区域算。如果控件要支持 `padding`，测量时要把 padding 算进 desired size，绘制时用 `paddingLeft/Top` 修正圆心和半径，三处都要改；漏一处就会出现“内容整体偏了一截”的怪现象。

### 硬件加速：看不见的那一层

从 Android 3.0 开始，应用默认开启硬件加速。`onDraw()` 里的 `Canvas` 并不是直接往屏幕上画像素，它记录的是**绘制命令**（display list）。这些命令被提交给 `RenderNode`，再由 RenderThread 在后台线程翻译成 GPU 指令，最后 `SurfaceFlinger` 合成上屏。

```text
UI 线程
  onDraw(Canvas)  →  记录 display list  →  提交 RenderNode
                                              ↓
RenderThread
  重放 display list  →  GPU 绘制  →  Buffer
                                              ↓
SurfaceFlinger
  合成各层  →  送屏幕
```

这层抽象带来两个后果，写自定义绘制时一定要知道。

第一，**不是所有 Canvas 操作在硬件加速下都一样快**。`drawRect`、`drawArc`、`drawPath` 这类简单几何很快；`saveLayer()`、`setLayerType()`、频繁的 `clipPath()`、大范围的 `alpha` 混合会触发离屏渲染，代价高一个量级。`clipPath` 有一个容易踩的点：裁剪路径在硬件加速与软件渲染下的边缘表现可能不同，想要平滑边缘时不要只依赖抗锯齿，建议用遮罩或离屏合成，并在目标机型上实测；带 `Region.Op` 参数的重载从 **API 28** 起只保留 `INTERSECT` 和 `DIFFERENCE` 两种语义，别再用它做“扩大裁剪区”之类的操作。阴影、圆角裁剪、渐变遮罩都容易踩到这一类坑。

第二，**“重绘”可能什么都没重绘**。如果 View 只是改了个颜色，`invalidate()` 之后 UI 线程重建 display list，RenderThread 重新提交；但如果只是平移、缩放、透明度变化，`RenderNode` 上的变换属性可以直接改，不需要重放 display list。这也是为什么动画优先用 `translationX`、`scaleX`、`alpha`，而不是在 `onDraw()` 里改坐标：前者只改属性，后者每帧重建。

`android.graphics.RenderNode` 作为公开 API 是 **API 29** 才有的；在更早的版本里它是框架内部实现，你只能通过 `View` 间接使用。需要自己预录制一段复杂绘制、反复重用时，可以创建 `RenderNode`，用 `beginRecording()` / `endRecording()` 录制，再在 `onDraw()` 里 `drawRenderNode`。

需要给一个 View 单独开硬件层时，用 `setLayerType(View.LAYER_TYPE_HARDWARE, null)`。典型场景是“动画期间做复杂裁剪或透明度变化”，把它变成一张纹理再做变换，避免每帧离屏。但别常驻开启：一张全屏纹理占内存，而且纹理更新本身有成本。动画结束要恢复 `LAYER_TYPE_NONE`。从 Android 6.0 起，框架在检测到 View 有 alpha/变换动画时，也可能自动给它加硬件层——所以你以为没开，实际上可能已经开了；性能分析时以 `dumpsys gfxinfo` 和 Perfetto 的实际数据为准。软件层 `LAYER_TYPE_SOFTWARE` 只适合明确依赖软件渲染的特性（某些 `setShadowLayer` 效果、`Canvas` 的特定混合模式），开了它等于放弃硬件加速。

阴影也一样。`elevation` 的阴影由系统根据 `ViewOutlineProvider` 生成，跟随 View 的轮廓；`setShadowLayer()` 是画笔级阴影，在硬件加速下有版本和场景限制，复杂形状建议用 `ViewOutlineProvider` 或者干脆画一层模糊。自定义 View 的轮廓用 `setOutlineProvider` 给出来，系统才能正确计算阴影和裁剪：

```kotlin
outlineProvider = object : ViewOutlineProvider() {
    override fun getOutline(view: View, outline: Outline) {
        outline.setOval(0, 0, view.width, view.height)
    }
}
```

![环形控件的几何：半径、描边、渐变起点与文字基线](/images/android-custom-widgets/06-draw-ring.webp)

> 图里标出了 `onDraw` 里真正要紧的几个量：`radius = min(w, h) / 2 - strokeWidth / 2`、`startAngle = -90°`、`SweepGradient` 的 0° 与弧起点的旋转对齐、以及用 `FontMetrics` 求出的文字基线。环的顶部标出了“描边溢出”：用 `width / 2` 当半径时，圆头会被 View 边界裁掉。

### 局部刷新和绘制缓存

`invalidate()` 不带参数会重绘整个 View。早期 Android 还提供带脏矩形的重载 `invalidate(left, top, right, bottom)`，但在较新的 API 上这些重载已经被标记为弃用——框架从 API 21 起就自己在内部计算脏区域，外部传的矩形收益很有限。写自绘控件时，比起手工算脏区，更值得做的是减少每帧重建的绘制内容。

更彻底的优化是“把不变的部分缓存成 `Bitmap` 或 `RenderNode`”。比如图表控件的网格线，每次重绘都画一遍几百条线；如果网格只随尺寸变化，可以画进一个离屏 `Bitmap`，`onDraw()` 里直接 `drawBitmap`。代价是内存和首次绘制的成本，尺寸变化时要重建缓存。是否值得，看“不变的部分”和“变化的部分”的比例。

### Compose 侧：`DrawScope` 与 `Brush`

Compose 的绘制入口是 `Canvas` 可组合函数，或者 `Modifier.drawBehind` / `drawWithContent` / `drawWithCache`。核心抽象是 `DrawScope`：它把 `Canvas` 和 `Paint` 的大部分能力包成了扩展函数，尺寸信息直接以 `size: Size` 的形式提供。

`Canvas` 有一个和 View 不一样的约束：它的 `modifier` 是**必填参数**，没有默认值，而且你必须在 modifier 链里给出尺寸（`size`、`fillMaxSize`、`weight` 之类）。`Canvas` 自己不知道要多大，它只是“在给定的绘制区域里画”。另外 `Canvas` 有一个带 `contentDescription` 的重载，自绘内容有明确语义时优先用它，而不是事后再补 `Modifier.semantics`。

```kotlin
@Composable
fun RingProgress(
    progress: Float,
    modifier: Modifier = Modifier,
    ringWidth: Dp = 12.dp,
    trackColor: Color = Color(0xFFE2E8F0),
    progressColors: List<Color> = listOf(Color(0xFF22D3EE), Color(0xFF818CF8)),
) {
    Canvas(modifier) {
        val stroke = ringWidth.toPx()
        val diameter = size.minDimension - stroke
        val topLeft = Offset(
            x = (size.width - diameter) / 2f,
            y = (size.height - diameter) / 2f,
        )
        val arcSize = Size(diameter, diameter)

        // 轨道
        drawArc(
            color = trackColor,
            startAngle = -90f,
            sweepAngle = 360f,
            useCenter = false,
            topLeft = topLeft,
            size = arcSize,
            style = Stroke(width = stroke, cap = StrokeCap.Round),
        )

        // 进度。色带从三点钟开始，所以把画布旋转 -90°，
        // 让弧和渐变一起从十二点钟开始。
        rotate(degrees = -90f, pivot = center) {
            drawArc(
                brush = Brush.sweepGradient(progressColors, center),
                startAngle = 0f,
                sweepAngle = 360f * progress,
                useCenter = false,
                // 绕圆心旋转不改变圆的位置，坐标照旧
                topLeft = topLeft,
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round),
            )
        }
    }
}
```

上面这段代码的旋转处理有点绕，原因和 View 侧一样：`drawArc` 的 `startAngle` 和 `sweepGradient` 的 0° 不是同一个参照系。另一个写法是把颜色数组的顺序调整好，不旋转画布——但那样在进度很小的时候渐变会从错误的颜色开始，视觉上更怪。评分类控件通常选择旋转画布，然后所有绘制都在“12 点为 0°”的坐标系里思考。

`DrawScope` 提供的常用能力：

| 能力 | View 侧 | Compose 侧 |
|---|---|---|
| 矩形/圆/线/弧 | `Canvas.drawRect/drawCircle/drawLine/drawArc` | `drawRect/drawCircle/drawLine/drawArc` |
| 路径 | `Path` + `Canvas.drawPath` | `Path` + `drawPath` |
| 画笔 | `Paint` | `DrawStyle`（`Fill` / `Stroke`）+ `Color` / `Brush` |
| 变换 | `Canvas.translate/rotate/scale/save/restore` | `translate/rotate/scale/withTransform` |
| 裁剪 | `Canvas.clipRect/clipPath` | `clipRect/clipPath` |
| 文字 | `Canvas.drawText` | `drawText(textMeasurer, ...)` |
| 渐变 | `LinearGradient/SweepGradient/RadialGradient` + `Paint.shader` | `Brush.linearGradient/sweepGradient/radialGradient` |
| 图层 | `saveLayer` / `setLayerType` | `graphicsLayer` / `Modifier.drawWithContent` |
| 绘制阶段 | `onDraw` | `drawBehind` / `drawWithContent` / `drawWithCache` |

`DrawScope` 没有 `Paint` 对象，`DrawStyle` 负责描述“怎么描边”，`Brush` 负责描述“用什么填”。需要 `Paint` 级别的控制（比如 `PathEffect`、混合模式）时，用 `drawIntoCanvas { it.nativeCanvas.drawPath(...) }` 取回原生 `Canvas`，自己管理 `Paint`。这是逃生通道，不是日常路径；一旦用上，就要自己处理 Compose 的绘制状态和原生 Canvas 的状态栈。

### 在绘制阶段读状态

Compose 绘制相对于 View 的一个结构性优势，是“绘制阶段读状态只触发重绘”。把评分环的进度改成从一个 `MutableState` 读取，并且只在 `drawBehind` 里读：

```kotlin
val progress = remember { mutableFloatStateOf(0f) }

Box(
    modifier = Modifier
        .size(160.dp)
        .drawBehind {
            drawScoreRing(progress.floatValue)  // 只在绘制阶段读
        }
)
```

外部改 `progress.floatValue`，Compose 的重组器会发现这次读取发生在绘制阶段，于是只把绘制标记为脏，不重组、不重新布局。对于动画这种每帧都变的量，这能省掉大量工作。反过来，如果在组合阶段读同一个 `State`（比如传给一个 `Text`），整块内容都会重组——不是错，但要知道代价在哪里。

`Modifier.drawWithCache` 是把“不变的几何”缓存起来的标准做法：它在尺寸或读取的状态变化时才重建缓存，绘制时直接复用：

```kotlin
Modifier.drawWithCache {
    val path = Path().apply { /* 根据 size 构建一次 */ }
    val brush = Brush.linearGradient(colors, start = Offset.Zero, end = Offset(size.width, 0f))
    onDrawBehind {
        drawPath(path, brush)
    }
}
```

`Modifier.graphicsLayer` 则把绘制和布局解耦：旋转、缩放、透明度、阴影、裁剪都可以在图层属性上完成，不会触发重绘。动画里经常和 `animateFloatAsState` 搭配：

```kotlin
Modifier.graphicsLayer {
    scaleX = scale
    scaleY = scale
    alpha = alpha
}
```

![View 与 Compose 的绘制栈](/images/android-custom-widgets/07-draw-stack.webp)

> 图左边是 View 的绘制路径：`onDraw` 记录 display list，`RenderNode` 交给 RenderThread，最后 SurfaceFlinger 合成。右边是 Compose：`DrawScope` 的绘制命令在 `LayoutNode` 的绘制阶段被记录下来，同样交给 RenderNode。两套体系的底层是同一个渲染管线，区别在于“谁来决定重绘”和“重绘的单位是什么”。

## 6. Touch：一次按下的旅行

到这里，控件已经知道自己多大、在哪、长什么样。最后一道关口是：手指按下来的时候，这件事算谁的。

Android 的触摸事件分发是一条自上而下的链：

```text
Window / DecorView
  → ViewGroup.dispatchTouchEvent(event)
      → onInterceptTouchEvent(event)      // 我拦不拦？
          拦 → 自己的 onTouchEvent(event)
          不拦 → 子 View.dispatchTouchEvent(event)
                    → 子 View.onTouchEvent(event)
      → 都没人处理 → 自己的 onTouchEvent(event)
```

`dispatchTouchEvent()` 的返回值表示“这个事件我处理了没有”。这个布尔值很关键：`ACTION_DOWN` 返回 `false`，后续的 `ACTION_MOVE`、`ACTION_UP` 就不会再发给这个 View，手势在第一步就断了。所以自定义控件如果想吃完整的手势，`ACTION_DOWN` 必须返回 `true`。

`ViewGroup` 的拦截发生在 `onInterceptTouchEvent()`。返回 `true` 表示“从下一个事件开始，由我自己处理”，子 View 会收到一个 `ACTION_CANCEL`，它之前积累的手势状态要在这里清掉。`requestDisallowInterceptTouchEvent(true)` 是反方向的控制：子 View 告诉父容器“这段时间别拦我”，典型场景是 `ViewPager2` 里的横向滑动控件——子控件开始拖拽时请求父容器别抢。这个请求会沿着父链递归传递；父容器在本次手势里就不再被询问 `onInterceptTouchEvent()`，只有系统返回手势进行中是例外。

还有一个常被忽略的顺序问题：`ViewGroup` 找子 View 时是**逆序**遍历的——后添加的、视觉上更上层的子 View 先拿到事件。如果你自己写 ViewGroup 并重写了 `dispatchTouchEvent()`，要维持这个顺序，否则会出现“点最上面的 View，触发的是下面那个”的错位。

事件分发的细节很多，写自定义控件时真正需要记住的是四条：

1. **`ACTION_DOWN` 必须接住**，否则没有后续。
2. **父容器可以随时抢走手势**，子 View 必须在 `ACTION_CANCEL` 里把手势状态复位，否则会“按着按着突然开始滑动”。
3. **坐标要用对**：`event.x/y` 是相对当前 View 的，`event.rawX/rawY` 是相对屏幕的；多点触控要用 `pointerId` 找 `pointerIndex`，不能直接用 `actionIndex`。
4. **点击要调 `performClick()`**：它同时负责回调 `OnClickListener`、播放点击音效、发送无障碍点击事件。自己在 `onTouchEvent` 里写一套“按下再抬起”的判定却不调它，监听器可能不触发，自动化测试和无障碍服务也拿不到一次真正的点击。

### 给评分环加上拖拽

评分环的交互是“按住圆环拖动，分数跟着手指的角度走”。这件事不需要 `onInterceptTouchEvent`，因为控件没有子 View；直接在 `onTouchEvent` 里处理：

```kotlin
private var lastAngle = 0f
private var dragging = false

/** 只在用户拖动时触发；程序里直接改 progress 不会触发。 */
var onProgressChanged: ((Float) -> Unit)? = null

override fun onTouchEvent(event: MotionEvent): Boolean {
    if (!isEnabled) return false

    when (event.actionMasked) {
        MotionEvent.ACTION_DOWN -> {
            dragging = true
            isPressed = true
            lastAngle = angleOf(event.x, event.y)
            // 告诉父容器：这一小段时间别抢我的事件
            parent?.requestDisallowInterceptTouchEvent(true)
            return true
        }

        MotionEvent.ACTION_MOVE -> {
            if (!dragging) return false
            val angle = angleOf(event.x, event.y)
            var delta = angle - lastAngle
            // 处理 0°/360° 的跳跃
            if (delta > 180f) delta -= 360f
            if (delta < -180f) delta += 360f
            lastAngle = angle

            progress = (progress + delta / 360f).coerceIn(0f, 1f)
            onProgressChanged?.invoke(progress)
            return true
        }

        MotionEvent.ACTION_UP -> {
            dragging = false
            isPressed = false
            parent?.requestDisallowInterceptTouchEvent(false)
            performClick()          // 无障碍和 OnClickListener 都靠它
            return true
        }

        MotionEvent.ACTION_CANCEL -> {
            dragging = false
            isPressed = false
            parent?.requestDisallowInterceptTouchEvent(false)
            // 取消时可以把进度回滚到按下前的值，看产品需求
            return true
        }
    }
    return super.onTouchEvent(event)
}

override fun performClick(): Boolean {
    super.performClick()
    // Android 16 起 announceForAccessibility() 被标记为弃用（受 flag 控制）；
    // 新代码用 stateDescription / accessibilityLiveRegion 让状态变化自然播报。
    ViewCompat.setStateDescription(
        this,
        "当前分数 ${(progress * 100).roundToInt()} 分",
    )
    return true
}

/** 把屏幕坐标换算成“12 点钟为 0°、顺时针为正”的角度。 */
private fun angleOf(x: Float, y: Float): Float {
    val dx = x - width / 2f
    val dy = y - height / 2f
    val degrees = Math.toDegrees(atan2(dy, dx).toDouble()).toFloat()
    return (degrees + 90f + 360f) % 360f
}
```

`angleOf` 里 `atan2` 的结果是 -180° 到 180°，0° 在三点钟、顺时针为正（因为屏幕 y 轴向下）。加 90° 再把负值归一到 0–360°，就得到“12 点钟为 0°”的角度。拖动时用角度差累加，而不是直接把手指角度当进度——后者会让手指一按下去，进度瞬间跳到手指所在的角度。前者的手感是“从当前值开始拧”，后者是“跳到我的手指”。两种交互都有产品在用，选哪种要写进验收标准。

`onProgressChanged` 这个回调有一个刻意的设计：它只在 `ACTION_MOVE`（用户拖动）时触发，程序里直接给 `progress` 赋值不会触发。这条规则是后面第 9 节“单向数据流”能成立的前提——如果 setter 里也回调，View 和状态之间就会互相触发。

`requestDisallowInterceptTouchEvent(true)` 只在按下时调用一次，抬起或取消时恢复。框架会在下一次 `ACTION_DOWN` 时重置这个标志位，所以漏掉恢复通常不会“永久”废掉父容器；但在本次手势剩下的时间里，父容器确实失去了拦截机会，一旦子控件又把手势让回来，页面就会卡住。把它当成必须成对出现的调用，比依赖框架的兜底更省心。

### 触摸阈值、惯性滑动与嵌套滚动

不是所有手势都像评分类控件这么直接。只要涉及“点击还是拖动”“松手后要不要继续滑”，就要引入几个系统工具：

- `ViewConfiguration.get(context).scaledTouchSlop`：系统认为“手指移动超过这个距离才算拖动”的阈值，单位是像素，已经按密度缩放。自己判断拖动时用它，而不是写死 10px。
- `VelocityTracker`：记录手指移动速度，`addMovement()` 采集，`computeCurrentVelocity()` 后 `getXVelocity()` 取速度。惯性滑动的起点速度来自它。
- `OverScroller` / `Scroller`：根据起始速度做衰减动画，`fling()` 之后每帧调 `computeScrollOffset()` 读取当前位置，直到 `isFinished()`。`computeScroll()` 里调用 `postInvalidateOnAnimation()` 驱动下一帧。
- `NestedScrollingChild3` / `NestedScrollingParent3`：让子控件把滚动量分发给父容器，实现“子控件先滚、滚到头父容器接着滚”的协作。`NestedScrollingChildHelper` 和 `NestedScrollingParentHelper` 把样板代码封装了大半。

嵌套滚动单独说一句，因为它决定了你的控件能不能和 `CoordinatorLayout`、`RecyclerView`、`SwipeRefreshLayout` 和平共处。自己实现一套嵌套滚动，最小骨架是：

```kotlin
class DragSheet @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs), NestedScrollingChild3 {

    private val childHelper = NestedScrollingChildHelper(this).apply {
        isNestedScrollingEnabled = true
    }

    override fun dispatchNestedPreScroll(
        dx: Int, dy: Int, consumed: IntArray?, offsetInWindow: IntArray?,
    ): Boolean = childHelper.dispatchNestedPreScroll(dx, dy, consumed, offsetInWindow)

    override fun dispatchNestedScroll(
        dxConsumed: Int, dyConsumed: Int,
        dxUnconsumed: Int, dyUnconsumed: Int,
        offsetInWindow: IntArray?, type: Int, consumed: IntArray,
    ) {
        childHelper.dispatchNestedScroll(
            dxConsumed, dyConsumed, dxUnconsumed, dyUnconsumed, offsetInWindow, type, consumed,
        )
    }

    // startNestedScroll / stopNestedScroll / dispatchNestedFling 同理转发给 helper
}
```

接口方法不少，但真正要自己写的逻辑只有一处：在 `onTouchEvent` 里把“我吃掉的滚动量”和“没吃掉的滚动量”分别交给 `dispatchNestedScroll`。其余都是转发。上面的骨架省略了带 `type` 参数的各个重载、`hasNestedScrollingParent` 之类的查询方法，以及 fling 相关的两个入口；实际实现时把它们一并交给 helper，或者用 IDE 生成接口骨架再填。

### Compose 侧：手势是一段协程

Compose 没有 `dispatchTouchEvent` 的继承链，触摸通过 `Modifier.pointerInput` 进入一个协程。最小的写法：

```kotlin
Modifier.pointerInput(Unit) {
    detectDragGestures { change, dragAmount ->
        change.consume()          // 标记事件已消费，避免父级继续处理
        onDrag(dragAmount)
    }
}
```

`detectDragGestures` 是封装好的常用手势检测器，内部已经处理了触摸阈值、多指、取消。需要更细的控制时，用 `awaitEachGesture` 自己写循环：

```kotlin
Modifier.pointerInput(Unit) {
    awaitEachGesture {
        val down = awaitFirstDown(requireUnconsumed = false)
        var lastAngle = angleOf(down.position)   // 同 View 侧的 angleOf
        var event: PointerEvent
        do {
            event = awaitPointerEvent()
            val change = event.changes.firstOrNull { it.id == down.id } ?: break
            if (change.pressed) {
                val angle = angleOf(change.position)
                var delta = angle - lastAngle
                if (delta > 180f) delta -= 360f
                if (delta < -180f) delta += 360f
                lastAngle = angle
                progress = (progress + delta / 360f).coerceIn(0f, 1f)
                change.consume()
            }
        } while (event.changes.any { it.pressed })
    }
}
```

Compose 的事件分发有三个 pass：`PointerEventPass.Initial`、`Main`、`Final`。`Initial` 从根往叶子走，`Main` 从叶子往根走，`Final` 再从根往叶子走一遍。这个设计是为了解决“父容器和子控件都想处理同一个事件”的竞争：子控件在 `Main` 里消费，父容器在 `Initial` 里有先看一眼的机会，或者用 `Final` 做兜底。`consume()` 只是打一个“已消费”标记，并不强制阻止后续 pass 的传递，是否尊重这个标记由各个手势检测器自己决定。

`Modifier.pointerInput` 的第一个参数是 key。key 变了，协程会被取消并重启，正在进行的手势会被中断。常见错误是把一个频繁变化的值当 key（比如每帧都变的进度），结果拖动到一半手势被重启。需要读取最新状态又不重启协程，用 `rememberUpdatedState`，或者把状态放进 `pointerInput` 内部持有的可变引用。

从 Compose 1.8 起，带 key 的重载成为标准写法，无 key 的 `pointerInput(block)` 被标记为弃用；同时 block 的静态类型换成了 `PointerInputEventHandler`。旧代码里的 lambda 写法在源码层兼容，但新代码最好显式给 key——`Unit` 表示“永不重启”，具体状态表示“这个状态变化时重启手势”。

嵌套滚动在 Compose 里对应 `Modifier.nestedScroll` + `NestedScrollConnection`：

```kotlin
Modifier.nestedScroll(object : NestedScrollConnection {
    override fun onPreScroll(available: Offset, source: NestedScrollSource): Offset {
        // 子控件滚动之前，父容器先消费
        return Offset.Zero
    }

    override fun onPostScroll(
        consumed: Offset, available: Offset, source: NestedScrollSource,
    ): Offset {
        // 子控件滚动之后，父容器再消费剩下的
        return Offset.Zero
    }
})
```

`onPreScroll` / `onPostScroll` / `onPreFling` / `onPostFling` 四个回调，语义和 View 的 `NestedScrollingParent3` 一一对应。`Modifier.scrollable`、`Modifier.draggable` 这些高层手势 API 内部已经接好了这套机制，自己写自定义手势时要注意别破坏它。

![一次按下的旅行：View 的事件分发与 Compose 的 pointerInput](/images/android-custom-widgets/08-touch.webp)

> 图左边是 View 的分发链：`dispatchTouchEvent` 先问 `onInterceptTouchEvent`，再问子 View，最后回到自己的 `onTouchEvent`；`ACTION_CANCEL` 是父容器抢走手势时发给子 View 的“清场信号”。右边是 Compose 的 `pointerInput`：一次手势是一段协程，`awaitEachGesture` 负责按下、移动、抬起的生命周期，`consume()` 标记消费，三个 pass 决定谁先看到事件。底部的圆环标了评分环专用的 `atan2` 角度换算，以及 0°/360° 跳变的补偿。

## 7. Animation：谁在推动下一帧

控件会动了之后，下一个问题就是“谁来推动下一帧”。View 体系里的答案是 `Choreographer`，Compose 的答案是 `MonotonicFrameClock`；两者背后是同一个 vsync 信号，只是暴露给你的接口不同。

### View：`ValueAnimator` 与属性动画

最直接的动画写法是 `ValueAnimator`：

```kotlin
private fun springTo(target: Float) {
    ValueAnimator.ofFloat(progress, target).apply {
        duration = 220
        interpolator = OvershootInterpolator(1.6f)
        addUpdateListener { animator ->
            progress = animator.animatedValue as Float   // setter 里 invalidate
        }
        start()
    }
}
```

`ValueAnimator` 本身不碰 View，它只在每一帧算出一个值，调用监听器。真正让画面动起来的是 `progress` 的 setter 里的 `invalidate()`。`ObjectAnimator` 把这两步合在一起：它通过反射或 `PropertyValuesHolder` 直接调用目标对象的 setter，setter 里负责重绘。`ViewPropertyAnimator`（`view.animate()`）更省事，它把 `translationX`、`alpha`、`rotation` 这些属性打包成一个动画，并且尽量避免每帧都走一遍完整的 View 属性设置。

用哪种取决于要动什么：

- 动**自绘内容**（进度、弧长、颜色）→ `ValueAnimator`，在监听器里改状态并 `invalidate()`；
- 动**View 的通用属性**（位置、透明度、缩放、旋转）→ `view.animate()` 或 `ObjectAnimator`；
- 动**多个属性且需要编排**→ `AnimatorSet`，或者用一个 `ValueAnimator` 驱动所有属性（更可控，也更容易做物理动画）。

动画的帧时钟是 `Choreographer`。`ValueAnimator` 内部通过 `Choreographer.postFrameCallback()` 注册帧回调，每次 vsync 到来时推进一次。所以“动画卡了”通常不是动画本身慢，是主线程在某一帧里做了别的事（布局、IO、锁等待），错过了 vsync，下一帧直接跳过去。这也是为什么动画期间要避免 `requestLayout()`：布局可能让一帧的工作量翻倍，翻倍之后就掉帧。

物理动画（弹簧、惯性）在 View 侧通常靠 `androidx.dynamicanimation` 的 `SpringAnimation` / `FlingAnimation`，或者自己用 `OverScroller`。它们比 `OvershootInterpolator` 更自然，因为参数是刚度、阻尼、初始速度，而不是拍脑袋的时长和曲线。`SpringAnimation` 也需要一个 `FloatPropertyCompat` 来读写目标值，写法和 `ObjectAnimator` 类似。

### Compose：动画就是对状态的插值

Compose 把动画统一成“对一个值做插值”。最常用的是 `animate*AsState`：

```kotlin
val animatedProgress by animateFloatAsState(
    targetValue = progress,
    animationSpec = spring(dampingRatio = 0.6f, stiffness = Spring.StiffnessMediumLow),
    label = "progress",
)
```

每次 `progress` 变了，`animatedProgress` 会在接下来的若干帧里追上去。它内部是一个 `Animatable`，每帧写入一个新的 `State`。这也意味着：**读 `animatedProgress` 的地方会重组**。如果只是画一条弧，这个重组其实可以避免——把读取放进绘制阶段：

```kotlin
// 方案一：组合阶段读，动画期间每帧重组
Canvas(Modifier.size(160.dp)) {
    drawRing(animatedProgress)
}

// 方案二：绘制阶段读，动画期间只重绘
val progressState = remember { mutableFloatStateOf(0f) }
Canvas(Modifier.size(160.dp).drawBehind { drawRing(progressState.floatValue) }) { }
```

方案二需要自己驱动 `progressState`。可以用 `Animatable` 在协程里 `animateTo`，把每一帧的值写进 `mutableFloatStateOf`；也可以直接用 `Modifier.graphicsLayer` 的 lambda 版本读取动画值（`graphicsLayer` 的 lambda 在绘制/图层更新阶段求值，不触发重组）：

```kotlin
val scale = remember { Animatable(1f) }
Box(
    Modifier.graphicsLayer {
        scaleX = scale.value
        scaleY = scale.value
    }
)
```

注意 `graphicsLayer(scaleX = scale.value)` 这种“参数版本”会在组合阶段读取 `scale.value`，每帧重组；`graphicsLayer { }` 的 lambda 版本在绘制阶段读取，只更新图层属性。两者名字一样，代价差一个数量级。这个细节在 Compose 性能文档里被反复强调，但实际代码里仍然经常写错。

需要更复杂的编排时，用 `updateTransition`：

```kotlin
val transition = updateTransition(targetState = expanded, label = "expand")
val height by transition.animateDp(label = "height") { if (it) 240.dp else 48.dp }
val alpha by transition.animateFloat(label = "alpha") { if (it) 1f else 0.6f }
```

`updateTransition` 把多个动画绑定到同一个状态机，它们共享时长和进度，天然同步。`Animatable` 则适合“由手势驱动的、可以被打断的”动画：`snapTo()`、`animateTo()` 都是挂起函数，可以被新的手势取消。

还有 `rememberInfiniteTransition`，用来做呼吸灯、加载指示器这类无限循环动画。它和 `Animatable` 的区别是“不需要终点”，内部用 `infiniteRepeatable` 的 `AnimationSpec`。

### 动画的验收标准

动画最容易在评审时被一句“感觉有点硬”打回，因为“手感”很难写成需求。能落地的验收项通常是这些：

- **打断行为**：动画进行到一半时再次点击，是重启、反向、还是从当前值继续？评分环的拖拽应该从当前值继续，不该跳回起点。
- **取消行为**：`ACTION_CANCEL`、页面退出、`RecyclerView` 复用时，动画要停，不能继续在后台跑。View 侧在 `onDetachedFromWindow()` 里 `cancel()`；Compose 侧协程会随组合销毁自动取消，但 `Animatable` 如果跑在外部 `CoroutineScope` 里，需要自己管。
- **初始状态**：第一次组合/第一次 `onDraw` 时不能闪一下。Compose 里常见的是初始值 0 然后动画到目标值，页面一进来就“从无到有”；如果不想要这个入场，用 `snapTo` 设初值或者 `remember` 时直接给目标值。
- **无障碍**：系统开启“移除动画”后，动画应该降级为直接跳变。View 侧用 `ValueAnimator.areAnimatorsEnabled()`（API 26）判断，返回 `false` 时直接设置终值；Compose 侧不用自己读开关，`animate*AsState`、`Animatable` 等动画 API 会通过内部的 `MotionDurationScale` 自动尊重系统的“移除动画”设置。至少不要因为动画导致内容无法阅读。
- **性能**：动画期间不分配对象、不触发 `requestLayout`、不重建 `Path`。这三条守住，大多数动画都不会掉帧。

## 8. XML 的那一半：属性、主题、样式与预览

写到这里，控件在代码里已经能跑。但 Android 的 View 不是只活在代码里——它还要能在 XML 里被声明、被主题覆盖、被设计师在预览里看到、被其他模块复用。这一节讲“声明式的那一半”。

### `declare-styleable`：给控件定义可配置项

自定义 View 的属性写在 `res/values/attrs.xml` 里：

```xml
<resources>
    <!-- 自定义 View 自己的属性 -->
    <declare-styleable name="RingProgressView">
        <attr name="ringWidth" format="dimension" />
        <attr name="startAngle" format="float" />
        <attr name="trackColor" format="color" />
        <attr name="progressStartColor" format="color" />
        <attr name="progressEndColor" format="color" />
        <attr name="scoreTextSize" format="dimension" />
    </declare-styleable>

    <!-- 一个“样式引用”属性，让主题可以整体换肤 -->
    <attr name="ringProgressStyle" format="reference" />
</resources>
```

`format` 决定这个属性接受什么类型的值：`dimension`（带单位的尺寸）、`color`、`float`、`integer`、`boolean`、`reference`（资源引用）、`string`、`enum`、`flags`，也可以组合，比如 `format="reference|color"`。`enum` 和 `flags` 要在 `attr` 里再声明取值：

```xml
<attr name="ringCap">
    <enum name="butt" value="0" />
    <enum name="round" value="1" />
</attr>
```

XML 里的使用方式：

```xml
<com.example.widget.RingProgressView
    android:id="@+id/score_ring"
    android:layout_width="160dp"
    android:layout_height="160dp"
    android:contentDescription="@string/score_ring_desc"
    app:ringWidth="12dp"
    app:startAngle="-90"
    app:trackColor="?attr/colorSurfaceVariant"
    app:progressStartColor="@color/ring_start"
    app:progressEndColor="@color/ring_end"
    style="@style/Widget.App.RingProgress.Compact" />
```

`app:` 是自定义命名空间（`xmlns:app="http://schemas.android.com/apk/res-auto"`），`android:` 是系统命名空间。自定义属性必须用 `app:`；系统属性（比如 `android:padding`）用 `android:`。两者混用是新手最常见的编译错误之一。

这里有个常见的误解：**不要在 View 标签上写 `app:ringProgressStyle="@style/..."` 来指望它当 `defStyleAttr`**。`defStyleAttr` 是构造器参数，`obtainStyledAttributes()` 会拿它去**当前主题**里查值，而不是从这个 View 的 `AttributeSet` 里读。想按实例覆盖整套默认值，用标准的 `style="@style/..."`（它是 `AttributeSet` 里的 style 引用，优先级高于 defStyle）；想全局换一套默认值，在主题里配 `ringProgressStyle`，下一节会写。

### 在构造器里读取属性

`View` 的构造器有三个参数：`Context`、`AttributeSet`（XML 里的属性集合）、`defStyleAttr`（主题里的默认样式引用）。自定义 View 通常提供三个重载，Java 调用方需要 `@JvmOverloads`：

```kotlin
class RingProgressView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = R.attr.ringProgressStyle,
) : View(context, attrs, defStyleAttr) {

    private var ringWidth = 12f * resources.displayMetrics.density
    private var startAngle = -90f
    private var trackColor = Color.LTGRAY
    private var progressStartColor = Color.CYAN
    private var progressEndColor = Color.BLUE
    private var scoreTextSize = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_SP,
        32f,
        resources.displayMetrics,
    )

    init {
        val ta = context.obtainStyledAttributes(
            attrs,
            R.styleable.RingProgressView,
            defStyleAttr,
            R.style.Widget_App_RingProgress,   // defStyleRes：兜底样式
        )
        try {
            ringWidth = ta.getDimension(R.styleable.RingProgressView_ringWidth, ringWidth)
            startAngle = ta.getFloat(R.styleable.RingProgressView_startAngle, startAngle)
            trackColor = ta.getColor(R.styleable.RingProgressView_trackColor, trackColor)
            progressStartColor = ta.getColor(
                R.styleable.RingProgressView_progressStartColor, progressStartColor,
            )
            progressEndColor = ta.getColor(
                R.styleable.RingProgressView_progressEndColor, progressEndColor,
            )
            scoreTextSize = ta.getDimension(
                R.styleable.RingProgressView_scoreTextSize, scoreTextSize,
            )
        } finally {
            ta.recycle()
        }
    }
}
```

`obtainStyledAttributes()` 的四个参数决定了属性值的优先级。官方 javadoc 给的顺序是四层：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1（最高） | `AttributeSet` 里显式写的属性 | 布局文件直接指定 `app:ringWidth="16dp"` |
| 2 | XML 里的 `style="..."` | 单个标签上引用的样式，上面的属性覆盖下面的默认值 |
| 3 | `defStyleAttr` 指向的样式 / `defStyleRes` 指定的样式 | 主题里的 `ringProgressStyle`，以及构造器传入的兜底样式 |
| 4（最低） | 当前主题的基准值 | `?attr/colorPrimary` 这类引用最终解析到的值 |

两个容易记混的细节：`defStyleRes` 只在 `defStyleAttr` 为 0、或者该属性在主题里找不到时才生效；`View(Context, AttributeSet, int, int)` 这个四参数构造器是 **API 21** 才有的，三参数构造器从 API 1 就有。接老项目时如果 minSdk 很低，用四参构造器要自己处理兼容。

理解这个顺序，才能解释“为什么我在主题里改了颜色，但某个页面没变”——那个页面的 XML 里显式写死了 `app:trackColor`。设计系统里的控件，通常把 XML 当作“一次性覆盖”，把主题样式当作“全局默认”，两者都保留。

关于 `TypedArray.recycle()`，经常能听到“现在不用了”的说法，这不太准确。在 Android 16 的源码里它**没有被标记弃用**，类注释和 `obtainStyledAttributes()` 的文档仍然写着“用完调用 recycle”；它的实现是把对象还回 `Resources` 里的池子（容量为 5 的 `SynchronizedPool`），重复 recycle 会抛 “recycled twice”，recycle 后再访问会抛 “Cannot make calls to a recycled instance”。变化在于：从 **API 31** 起 `TypedArray` 实现了 `AutoCloseable`，`close()` 就是 `recycle()`，所以可以用 Kotlin 的 `use { }` 或 Java 的 try-with-resources 自动回收；API 30 及以下仍然要手动调。工程上保持“申请了就归还”的习惯，Lint 也会少报一堆 `Recycle`。

还有一组和主题相关的常用 API：`getColorStateList()` 比 `getColor()` 更适合按钮、开关这类会随状态变色的控件，因为它保留了 pressed / disabled 状态；`ColorStateList` 可以通过 `ColorStateList.valueOf()` 或 XML `<selector>` 创建。自定义 View 想跟随 `pressed`/`selected` 换色，除了在 `drawableStateChanged()` 里 `invalidate()`，还要重写 `onCreateDrawableState()` 并用 `mergeDrawableStates()` 把自定义状态合并进去，否则状态根本传不到这个 View。

### 主题、样式和多套皮肤

自定义 View 的“换肤”通常有三条路：

- **XML 里直接写颜色**：灵活，但改一次要动所有布局；
- **主题属性**：在 `themes.xml` 里定义 `<item name="ringTrackColor">@color/...`，XML 里写 `app:trackColor="?attr/ringTrackColor"`，换主题就换皮肤；
- **defStyleAttr + 样式**：把一整套默认值放进 `styles.xml`，主题里只引用一个 `ringProgressStyle`，最干净，也最适合放进设计系统。

第三种做法要在主题里声明：

```xml
<style name="Theme.App" parent="Theme.Material3.DayNight.NoActionBar">
    <item name="ringProgressStyle">@style/Widget.App.RingProgress</item>
</style>

<style name="Widget.App.RingProgress" parent="">
    <item name="ringWidth">12dp</item>
    <item name="startAngle">-90</item>
    <item name="trackColor">?attr/colorSurfaceVariant</item>
    <item name="progressStartColor">?attr/colorPrimary</item>
    <item name="progressEndColor">?attr/colorTertiary</item>
</style>
```

注意 `Widget.App.RingProgress` 的 `parent=""`：它不继承任何系统样式，免得被 Material 组件的默认值污染。`?attr/` 引用的是当前主题里的值，这样深色模式和动态取色都能自动生效。

### 预览：`isInEditMode()` 与 `tools:` 命名空间

Android Studio 的布局预览不会执行真实的业务逻辑，很多自定义控件在预览里会白屏或者崩溃。两个常用手段：

- `isInEditMode()`：在预览环境里返回 `true`，用来给控件一个安全的默认值，避免读取 `ViewModel`、网络、数据库；
- `tools:` 命名空间：`tools:ringWidth="12dp"` 这类属性只在预览里生效，不会打包进 APK；`tools:visibility="visible"` 可以在预览里强制显示默认隐藏的控件。

```kotlin
if (isInEditMode) {
    progress = 0.76f
}
```

一个控件如果设计团队要反复看，值得把预览体验做好：默认值要像模像样、文字要能看清、深色/浅色都要能切。

### Compose 的“声明”去哪了

Compose 没有 XML，但“声明式配置”并没有消失，只是换了位置：

| View 体系 | Compose | 说明 |
|---|---|---|
| 构造器参数 | 可组合函数参数 | 控件的配置入口 |
| `declare-styleable` / `attrs.xml` | 参数类型 + 默认值 | 类型检查由编译器完成 |
| `defStyleAttr` / `defStyleRes` | 参数默认值 + `MaterialTheme` | 默认值来自主题对象 |
| `?attr/colorPrimary` | `MaterialTheme.colorScheme.primary` | 主题 token |
| `android:padding` | `Modifier.padding()` | 布局属性变成修饰符 |
| `style="@style/..."` | 具名可组合函数 / `CompositionLocal` | 复用一组配置 |
| `tools:` 预览属性 | `@Preview` + 预览参数 | 预览是独立函数 |

可组合函数的参数就是 Compose 的“XML”。这个说法不完全是比喻：参数列表决定了这个控件能配置什么、默认值是什么、类型约束是什么；`Modifier` 参数决定了它在布局体系里的位置；`@Preview` 决定了它怎么被看到。区别在于，这些都在 Kotlin 里，可以被重构、被 IDE 跳转、被单元测试覆盖，也天然支持“一个控件多种变体”。

```kotlin
@Composable
fun RingProgress(
    progress: Float,
    modifier: Modifier = Modifier,
    ringWidth: Dp = 12.dp,
    startAngle: Float = -90f,
    trackColor: Color = MaterialTheme.colorScheme.surfaceVariant,
    progressColors: List<Color> = listOf(
        MaterialTheme.colorScheme.primary,
        MaterialTheme.colorScheme.tertiary,
    ),
)
```

主题在 Compose 里通过 `CompositionLocal` 传递。`MaterialTheme` 本身就是一个 `CompositionLocalProvider`，把颜色、字体、形状塞进三个 `CompositionLocal`。自定义控件要读主题，直接读 `MaterialTheme.colorScheme`；要支持“局部覆盖”，用 `CompositionLocalProvider(LocalContentColor provides ...)` 包一层。这套机制比 XML 的主题继承更显式，也更难“意外继承”——某个值从哪来，沿着可组合函数的调用栈就能找到。

`CompositionLocal` 不是万能配置通道。它适合“整棵树都需要的环境值”（主题、密度、布局方向、语言），不适合“某个控件自己的业务参数”。把业务参数塞进 `CompositionLocal`，会让数据流变得不可追踪，测试也难写。

![XML 属性解析链与主题优先级](/images/android-custom-widgets/09-xml-attrs.webp)

> 左半部分是一次属性读取的路径：布局 XML → `AttributeSet` → `obtainStyledAttributes` → 主题/样式兜底 → `TypedArray`；右半部分是四个来源的优先级；底部是 Compose 的对应物：函数参数、默认值、`MaterialTheme`、`CompositionLocal`。两边的目标是一样的——让“默认值”和“一次性覆盖”各归其位。

## 9. 两套世界怎么接：`AndroidView` 与 `ComposeView`

现实项目里很少“全 View”或“全 Compose”。更常见的是一个页面里两种实现共存：老页面是 XML，新设计系统是 Compose；或者反过来，Compose 页面里要嵌一个历史悠久的图表控件。两套世界必须能互相嵌入，否则任何迁移都只能“一刀切”，而一刀切在存量项目里基本等于不迁移。

AndroidX 提供了两个方向的桥：

- **在 Compose 里放 View**：`AndroidView`；
- **在 View 里放 Compose**：`ComposeView`（以及它的基类 `AbstractComposeView`）。

### `AndroidView`：把 View 包进组合

```kotlin
@Composable
fun RingProgressFromView(
    progress: Float,
    onProgressChange: (Float) -> Unit,
    modifier: Modifier = Modifier,
) {
    AndroidView(
        modifier = modifier,
        factory = { context ->
            RingProgressView(context).apply {
                // 只在这里做一次性的初始化：设置监听器、默认值
                // 注意：直接捕获 onProgressChange 可能拿到陈旧值，正确写法见下文
                onProgressChanged = onProgressChange
            }
        },
        update = { view ->
            // 每次重组都会调用，适合把状态同步给 View
            view.progress = progress
        },
        onReset = { view ->
            // 这个 View 实例被复用时调用（比如放进 RecyclerView）
            view.progress = 0f
        },
        onRelease = { view ->
            // 这个 View 实例不再被使用时调用
            view.onProgressChanged = null
        },
    )
}
```

`AndroidView` 的回调分工明确，但很多人只用了前两个：

- `factory`：创建 View，**只调用一次**（更准确地说，每个组合实例一次）。不要在这里读取会变化的 `State`，它不会因为状态变化而重新执行。
- `update`：每次重组都调用，把 Compose 状态同步给 View。这里应该只做“赋值”和“轻量同步”，不要注册监听器、不要创建对象、不要触发异步任务。
- `onReset`：View 被复用（从父容器移除又加回来，或放进 `RecyclerView` 的缓存池）时调用，把 View 恢复到初始状态。
- `onRelease`：View 不再使用时调用，释放监听器、动画、资源。

这里有个容易漏掉的开关：**只有传入非 null 的 `onReset`，`AndroidView` 才会启用 View 复用**。不传 `onReset` 时，`AndroidView` 走的是“不复用”的重载；在 `LazyColumn` 这类可复用容器里，View 实例会被丢弃重建，即使所在的组合组没有变化。官方文档建议在可复用容器里尽量 opt-in。反过来说，一旦传了 `onReset`，就必须把它写对——它负责把上一个数据绑定的痕迹清干净，否则会出现“复用了旧状态”的脏数据。

一个常见的写法是在 `update` 里设置监听器：

```kotlin
update = { view ->
    view.onProgressChanged = { /* ... */ }  // 每次重组都新建一个 lambda
}
```

`onProgressChanged` 是普通 setter，旧值会被覆盖，所以不会像 `addListener` 那样累积；真正的代价是每次重组都新建一个闭包，以及把“状态同步”和“事件注册”混进了一条每帧都会执行的路径。监听器应该在 `factory` 里注册一次，回调里通过 `rememberUpdatedState` 拿到最新的 lambda：

```kotlin
val currentOnChange by rememberUpdatedState(onProgressChange)

AndroidView(
    factory = { context ->
        RingProgressView(context).apply {
            onProgressChanged = { value -> currentOnChange(value) }
        }
    },
    update = { it.progress = progress },
)
```

`AndroidView` 不负责测量策略。它把父容器给的 `Constraints` 翻译成 View 的 `MeasureSpec`，然后让 View 自己测量；如果 View 在 `wrap_content` 下返回 0，`AndroidView` 也会是 0。这种情况下用 `Modifier.size()`、`Modifier.height(IntrinsicSize.Min)` 或者给 View 设置 `minimumWidth/minimumHeight`。反过来，如果 View 的 `onMeasure` 会吃掉所有可用空间（比如内部有个 `match_parent` 的子 View），`AndroidView` 也会跟着撑满，`Modifier.wrapContentSize()` 压不住它。测量的控制权在 View 手里，Compose 只能给约束。

### `ComposeView`：把 Compose 塞进 View

```kotlin
class RingProgressHostView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : FrameLayout(context, attrs) {

    private val composeView = ComposeView(context).apply {
        setViewCompositionStrategy(
            ViewCompositionStrategy.DisposeOnViewTreeLifecycleDestroyed,
        )
        setContent {
            MaterialTheme {
                RingProgress(progress = 0.76f)
            }
        }
    }

    init {
        addView(composeView)
    }
}
```

也可以直接在 XML 里写：

```xml
<androidx.compose.ui.platform.ComposeView
    android:id="@+id/compose_view"
    android:layout_width="match_parent"
    android:layout_height="wrap_content" />
```

然后在代码里 `setContent { ... }`。

`ComposeView` 本质上是一个 `AbstractComposeView`，它在 `onAttachedToWindow()` 时挂载组合，在 `onDetachedFromWindow()` 时销毁组合（具体行为取决于 `ViewCompositionStrategy`）。它需要从 `View` 的上下文里找到 `ViewTreeLifecycleOwner` 和 `ViewTreeSavedStateRegistryOwner` 才能工作。`ComponentActivity`、`Fragment`、`AppCompatActivity` 的视图树里这两个 owner 是自动提供的；在 `Dialog`、`PopupWindow`、`RecyclerView` 的 `ViewHolder`、`WindowManager` 直接添加的 View 里，它们可能不存在，于是出现经典的崩溃：

```text
java.lang.IllegalStateException: ViewTreeLifecycleOwner not found from DecorView@...
```

解决办法是在挂载 Compose 的根 View 上手动设置：

```kotlin
composeView.setViewTreeLifecycleOwner(lifecycleOwner)
composeView.setViewTreeSavedStateRegistryOwner(savedStateRegistryOwner)
```

或者在创建 `ComposeView` 之前，把 owner 挂到它的父 View 上。这个错误的难点不在于修复，而在于定位：崩溃堆栈指向 `ComposeView`，但问题出在“谁把 View 放进了一个没有 Lifecycle 的容器”。

`ViewCompositionStrategy` 的几个取值要记住：

| 策略 | 行为 | 适用场景 |
|---|---|---|
| `DisposeOnDetachedFromWindow` | View 从窗口分离时销毁组合 | 普通页面 |
| `DisposeOnDetachedFromWindowOrReleasedFromPool` | 分离或从池中释放时销毁（**`Default` 当前等价于此**） | 大多数场景，包括 `RecyclerView` |
| `DisposeOnViewTreeLifecycleDestroyed` | 等 ViewTree 的 Lifecycle 销毁时才销毁 | `Fragment`、有转场动画的页面 |
| `DisposeOnLifecycleDestroyed(lifecycle)` | 自定义 Lifecycle 销毁时销毁 | 特定生命周期管理 |

默认策略本身就考虑了池化容器：普通容器在 detach 时销毁组合；`RecyclerView` 这类池化容器则等到池真正释放 item（或容器本身 detach）时才销毁。因此“把 `ComposeView` 放进 `RecyclerView`”本身不需要改策略，item 复用时状态重置是复用的正常语义。真正需要显式改策略的是 `Fragment`、带转场动画的页面、以及手动管理生命周期的容器——这时 `DisposeOnViewTreeLifecycleDestroyed` 更合适，它让组合跟着宿主的 `ViewTreeLifecycleOwner` 走。

还有一个官方特意留下的注脚：`ViewCompositionStrategy.Default` **当前**等价于 `DisposeOnDetachedFromWindowOrReleasedFromPool`，但 reference 注明这是实现细节、可能变化。代码里如果要依赖某个具体行为，显式写出策略名，别依赖默认值。

### 状态同步：方向要单一

View 和 Compose 各有自己的状态模型。把它们接在一起时，最容易写出的 bug 是**双向绑定形成回环**：

```kotlin
// 错误示范：view 的回调写回 state，state 的变化又通过 update 写回 view
val progress = remember { mutableFloatStateOf(0f) }
AndroidView(
    factory = { ctx ->
        RingProgressView(ctx).apply {
            onProgressChanged = { progress.floatValue = it }
        }
    },
    update = { it.progress = progress.floatValue },
)
```

这段代码看似合理，实际上：用户拖动 → View 回调 → state 更新 → 重组 → `update` 把 state 写回 View → 如果 View 的 setter 里也回调，就又回到第二步。是否形成死循环，取决于 setter 有没有“值没变就不通知”的保护——很多控件正是在 setter 里通知监听器，然后栽在这里。`RingProgressView` 把回调放在 `ACTION_MOVE` 的用户操作分支、而不是 setter 里，就是为了从源头断掉这个环。正确的做法是让数据流单向：**Compose 状态是唯一事实来源，View 是它的投影**；View 内部的用户操作通过回调把“意图”上报，由状态持有者决定要不要接受。

```kotlin
val progress = remember { mutableFloatStateOf(0.76f) }

AndroidView(
    factory = { ctx ->
        RingProgressView(ctx).apply {
            onProgressChanged = { newValue ->
                // 只在“用户操作”时上报，且不在 setter 里回调
                progress.floatValue = newValue
            }
        }
    },
    update = { view ->
        if (view.progress != progress.floatValue) {
            view.progress = progress.floatValue
        }
    },
)
```

`if (view.progress != progress.floatValue)` 这层判断很重要：它把“状态同步”变成幂等的操作，避免无意义的重绘。View 的 setter 里也应该有同样的判断（`if (field == value) return`），这是自定义控件的基本修养。

### 迁移策略：不要在同一棵树里打两场仗

如果项目正在从 View 迁到 Compose，比较务实的路径是：

1. **新页面直接用 Compose**，老页面不动。两者共享设计 token（颜色、间距、字号），避免视觉分叉。
2. **老页面里用 `ComposeView` 嵌入新组件**，从“一个新模块”开始，而不是从“整页重写”开始。
3. **Compose 页面里用 `AndroidView` 包老控件**，尤其是地图、图表、播放器这类重资产组件，短期不值得重写。
4. **不要在一个自定义控件的内部混用两套渲染**。比如一个 `View` 在 `onDraw` 里画 Compose 的内容，或者一个 `Composable` 里用 `AndroidView` 包一个自己刚写的 `View`。这种“套娃”会让测量、绘制、生命周期的责任变得不可追踪，性能也很难分析。
5. **先迁“状态”，再迁“视图”**。把数据流、状态管理、设计 token 先抽出来，视图层换实现时才有对照物。

![View 与 Compose 的互操作矩阵](/images/android-custom-widgets/10-interop.webp)

> 图里四个象限分别是“View 里放 View”“Compose 里放 Compose”“Compose 里放 View”“View 里放 Compose”。每个象限标出了入口 API、生命周期责任方和最常见的坑。中间那圈虚线是“状态同步”的边界：跨过这条线的每一次数据流动，都要有明确的单一事实来源。

## 10. 合格线：无障碍、RTL、状态与焦点

一个控件“能看、能点”只是及格线。真正的合格线在另一组能力上：看不见的用户能不能用，从右往左的语言里会不会镜像，转屏之后状态还在不在，键盘能不能操作。这些能力在 View 和 Compose 里各有各的 API，但要求是同一套。

### 无障碍：自绘控件最容易欠的债

`RingProgressView` 用 `Canvas` 画了一个环，对 TalkBack 来说，它只是一个“没有文字的 View”。它不知道这是个评分控件，也不知道当前分数是多少，更不知道用户可以拖动它。补齐这件事有三层：

**第一层，给控件一个可读的名字和状态。**

```kotlin
contentDescription = "评分环，当前 ${(progress * 100).roundToInt()} 分"
```

`contentDescription` 是最低成本的接入方式。每次 `progress` 变化时更新它，TalkBack 聚焦时会读出来。但它只描述“是什么”，不描述“能做什么”。

**第二层，让控件可操作。**

```kotlin
override fun performClick(): Boolean {
    super.performClick()
    return true
}
```

TalkBack 的双击会走无障碍的 `ACTION_CLICK`，最终落到 `performClick()`（前提是控件声明了可点击、并提供能被读到的语义）。这也是为什么自定义 View 的点击逻辑应该收敛到 `performClick()`，而不是只在 `onTouchEvent` 里写一套；否则触摸能用、读屏和自动化测试却找不到入口。

如果控件有多个可操作区域（比如一个评分环可以点五个分段），需要把它们做成**虚拟子节点**，用 `ExploreByTouchHelper`：

```kotlin
private class RingAccessibilityHelper(host: RingProgressView) :
    ExploreByTouchHelper(host) {

    override fun getVirtualViewAt(x: Float, y: Float): Int {
        // 返回手指落在哪个虚拟节点上，HOST_ID 表示宿主自己
        return host.virtualNodeAt(x, y)
    }

    override fun getVisibleVirtualViews(virtualViewIds: MutableList<Int>) {
        // 把当前所有可聚焦的虚拟节点 id 加进来
        repeat(host.segmentCount) { virtualViewIds.add(it) }
    }

    override fun onPopulateNodeForVirtualView(
        virtualViewId: Int,
        node: AccessibilityNodeInfoCompat,
    ) {
        node.contentDescription = host.segmentDescription(virtualViewId)
        node.setBoundsInParent(host.segmentBounds(virtualViewId))
        node.addAction(AccessibilityNodeInfoCompat.ACTION_CLICK)
        node.isSelected = host.isSegmentSelected(virtualViewId)
    }

    override fun onPerformActionForVirtualView(
        virtualViewId: Int, action: Int, arguments: Bundle?,
    ): Boolean {
        if (action == AccessibilityNodeInfoCompat.ACTION_CLICK) {
            host.selectSegment(virtualViewId)
            return true
        }
        return false
    }
}

// 在 init 里：
private val accessibilityHelper = RingAccessibilityHelper(this).also {
    ViewCompat.setAccessibilityDelegate(this, it)
}
```

`ExploreByTouchHelper` 还要配合 `dispatchHoverEvent()` 转发悬停事件，否则部分设备上的“触摸探索”不工作：

```kotlin
override fun dispatchHoverEvent(event: MotionEvent): Boolean =
    accessibilityHelper.dispatchHoverEvent(event) || super.dispatchHoverEvent(event)
```

**第三层，让状态变化被播报。**

```kotlin
ViewCompat.setStateDescription(this, "分数已调整为 78 分")
```

`announceForAccessibility()` 是这类需求最直接的 API，但它在 Android 16 上已经被标记为弃用（受 flag 门控，API 37 的最终状态还没定），官方的方向是“用语义描述代替强制播报”。新代码可以用 `ViewCompat.setStateDescription()`（原生方法 API 30 起，ViewCompat 兼容旧版本）配合 `accessibilityLiveRegion`，让无障碍服务自己决定要不要读；需要立即播报时再用 announcement，并接受“部分服务可能忽略”的现实。

Compose 侧对应 `Modifier.semantics`：

```kotlin
Modifier.semantics(mergeDescendants = true) {
    contentDescription = "评分环，当前 $score 分"
    stateDescription = "$score 分"
    progressBarRangeInfo = ProgressBarRangeInfo(score / 100f, 0f..1f)
    customActions = listOf(
        CustomAccessibilityAction("增加五分") { score = (score + 5).coerceAtMost(100); true },
        CustomAccessibilityAction("减少五分") { score = (score - 5).coerceAtLeast(0); true },
    )
    onClick(label = "重置") { score = 0; true }
}
```

进度类控件不需要再手写 `role`：`progressBarRangeInfo` 会被无障碍层映射成进度条角色，读屏会按“百分比/当前值”来播报。这段语义同时服务 TalkBack 和测试框架——Compose 测试里的 `assertSemantics` 读的是同一棵语义树。

`mergeDescendants = true` 会把子节点的语义合并到当前节点，适合“一个整体控件”的场景。`clearAndSetSemantics` 则相反，用于“子节点不该被单独读出来”的情况。焦点、点击、状态、自定义操作这四类语义，Compose 都提供了标准 API；View 侧要用 `AccessibilityNodeInfo` 手写，`ExploreByTouchHelper` 是主要的辅助类。

### RTL：不只是“镜像一下”

RTL 适配的经典 bug 列表：

- 用 `layout_marginLeft/Right` 而不是 `layout_marginStart/End`；
- 用 `paddingLeft/Right` 算位置，而不是 `paddingStart/End`；
- 图标没有镜像（返回箭头、播放进度、方向性图标）；
- 自定义 `onDraw` 里的坐标写死了从左到右；
- 文字长度变化导致布局溢出（阿拉伯语、希伯来语通常更长）。

View 侧的处理方式前面提过：在 `onRtlPropertiesChanged` 里 `requestLayout()`，在测量和布局时用 `layoutDirection` 决定方向。自绘控件的绘制如果包含方向语义（比如一个进度条从右往左涨），要在 `onDraw` 里读 `layoutDirection`，或者把方向转换放在一个统一的坐标变换里，避免每个绘制函数都判断一次。

Compose 侧，`LocalLayoutDirection` 提供了当前方向，`placeRelative` 会自动镜像。需要手动判断时：

```kotlin
val isRtl = LocalLayoutDirection.current == LayoutDirection.Rtl
```

注意：**不是所有东西都该镜像**。数字、时钟、媒体播放按钮、品牌 Logo 通常不镜像；带方向含义的图标（返回、前进、列表缩进）才镜像。这个判断是产品级的，不是技术级的；控件只负责提供“可镜像”的结构，具体哪些内容镜像由使用方决定。

### 状态保存：转屏、进程重建与列表复用

用户转屏、切换深色模式、从后台回来，Activity 可能重建；`RecyclerView` 里的 item 可能被回收复用；进程可能被系统杀掉再恢复。控件的状态要能活过这些场景。

View 侧的标准写法是 `onSaveInstanceState()` + `BaseSavedState`：

```kotlin
override fun onSaveInstanceState(): Parcelable {
    val superState = super.onSaveInstanceState()
    return SavedState(superState).apply { progress = this@RingProgressView.progress }
}

override fun onRestoreInstanceState(state: Parcelable?) {
    if (state is SavedState) {
        super.onRestoreInstanceState(state.superState)
        progress = state.progress
    } else {
        super.onRestoreInstanceState(state)
    }
}

private class SavedState : BaseSavedState {
    var progress = 0f

    constructor(superState: Parcelable?) : super(superState)

    private constructor(source: Parcel, loader: ClassLoader?) : super(source, loader) {
        progress = source.readFloat()
    }

    override fun writeToParcel(out: Parcel, flags: Int) {
        super.writeToParcel(out, flags)
        out.writeFloat(progress)
    }

    companion object {
        @JvmField
        val CREATOR: Parcelable.ClassLoaderCreator<SavedState> =
            object : Parcelable.ClassLoaderCreator<SavedState> {
                override fun createFromParcel(source: Parcel, loader: ClassLoader?) =
                    SavedState(source, loader)

                override fun createFromParcel(source: Parcel) = SavedState(source, null)

                override fun newArray(size: Int) = arrayOfNulls<SavedState>(size)
            }
    }
}
```

`BaseSavedState` 必须处理 `superState`，否则父类的状态（比如 `View` 自己的可见性、焦点）会丢。`onSaveInstanceState()` 和 `onRestoreInstanceState()` 都是 `@CallSuper`，没调 super 会抛 `IllegalStateException`。`CREATOR` 需要是对外可见的静态字段——Kotlin 里放在 `companion object` 并加 `@JvmField`，默认是 public；嵌套类本身可以是 `private`，但别把 `CREATOR` 也跟着藏起来，否则恢复时反射找不到它。用 `ClassLoaderCreator` 而不是普通 `Creator`，是因为跨进程恢复时需要一个正确的 `ClassLoader`，`BaseSavedState(Parcel, ClassLoader)` 这个构造器从 API 24 起才有。

还有一个前置条件经常被忽略：**View 的实例状态只对“有 id、且 `android:saveEnabled` 不为 false”的 View 生效**。没有 `android:id` 的控件不会进入 `saveHierarchyState()` 收集的 SparseArray，`onSaveInstanceState()` 永远不会被调用。写自定义控件时，要么在文档里要求使用方给 id，要么让使用方通过 `ViewModel` 管状态。

如果控件需要保存的状态很多，或者需要跨进程恢复，考虑用 `ViewModel` + `SavedStateHandle`，而不是把所有东西塞进 `Parcelable`。控件只负责“把自己的视觉状态序列化”，业务状态归业务层。

Compose 侧，`rememberSaveable` 负责同样的事：

```kotlin
var progress by rememberSaveable { mutableFloatStateOf(0.76f) }

// 自定义 Saver
val ringSaver = mapSaver(
    save = { mapOf("progress" to it) },
    restore = { it["progress"] as Float },
)
var savedProgress by rememberSaveable(stateSaver = ringSaver) {
    mutableFloatStateOf(0.76f)
}
```

`rememberSaveable` 能保存 `Bundle` 支持的类型；自定义类型要给 `Saver`。它在配置变更和进程重建后恢复，但不能替代 `ViewModel` 的业务状态管理。两者分工和 View 侧一样：控件管视觉状态，业务管数据。

### 焦点、键盘与遥控器

手机用户很少用键盘，但平板、车机、电视、无障碍开关控制都依赖焦点系统。自定义控件至少要保证：

- `isFocusable = true`（或通过 `android:focusable` 声明），否则键盘 Tab 永远跳不到它；
- 有明确的焦点视觉反馈（`onFocusChanged` 里改绘制，或者用 `state_focused` 的 `ColorStateList`）；
- 方向键能操作（如果控件有内部选项，重写 `onKeyDown` 处理方向键和确认键）；
- `nextFocusForward` 等属性有合理默认值，让焦点顺序符合阅读顺序。

Compose 侧用 `Modifier.focusable()`、`FocusRequester`、`onFocusChanged`；焦点顺序默认按组合顺序，需要自定义时用 `focusProperties { next = ... }`。

### 一份可以照着勾的合格线清单

| 能力 | View 侧要做的事 | Compose 侧要做的事 |
|---|---|---|
| 可读 | `contentDescription`、`AccessibilityNodeInfo` | `Modifier.semantics`、`contentDescription` |
| 可操作 | `performClick()`、`ExploreByTouchHelper`、`dispatchHoverEvent` | `semantics { onClick }`、`customActions`、`focusable` |
| 可感知状态 | `setStateDescription`、`accessibilityLiveRegion`（`announceForAccessibility` 在 Android 16 起弃用） | `stateDescription`、`progressBarRangeInfo` |
| 键盘/焦点 | `focusable`、`onKeyDown`、`nextFocus*` | `focusable`、`FocusRequester`、`focusProperties` |
| 方向 | `layoutDirection`、`start/end`、`onRtlPropertiesChanged` | `LocalLayoutDirection`、`placeRelative` |
| 状态保存 | `onSaveInstanceState` + `BaseSavedState` | `rememberSaveable` + `Saver` |
| 主题 | `ColorStateList`、`uiMode`、主题属性 | `MaterialTheme`、`isSystemInDarkTheme` |
| 本地化 | `getString`、`getQuantityString`、`R.plurals` | `stringResource`、`pluralStringResource` |
| 预览 | `isInEditMode()`、`tools:` | `@Preview`、预览参数 |
| 可测 | `contentDescription`/`testTag`、可注入的时钟 | `testTag`、`semantics`、可控时钟 |

## 11. 性能：自定义控件最容易埋的四类雷

自定义控件的性能问题，很少是“算法太慢”。更多时候是四类结构性浪费：在错误的阶段分配对象、让失效范围扩散、把布局当成动画、以及把测量当成免费操作。下面按“症状—原因—工具”的顺序过一遍，两边体系都覆盖。

### 第一类：在测量、布局、绘制里分配对象

`onDraw()` 里写 `Paint()`、`RectF()`、`String.format()`、`listOf()`，每帧都产生垃圾。绘制本身可能只要几毫秒，一次 GC 暂停却能把它翻倍；用户看到的是卡顿，trace 里却找不到“慢方法”。

View 侧的做法是把几何对象提到成员变量，在 `onSizeChanged()` 或构造器里创建：

```kotlin
// 反例：每帧分配
override fun onDraw(canvas: Canvas) {
    val rect = RectF(0f, 0f, width.toFloat(), height.toFloat())
    val paint = Paint().apply { color = Color.RED }
    canvas.drawArc(rect, 0f, 180f, false, paint)
    canvas.drawText("${(progress * 100).toInt()} 分", x, y, Paint())
}

// 正例：对象复用，onDraw 只做绘制
private val ringRect = RectF()
private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG)
private val scorePaint = Paint(Paint.ANTI_ALIAS_FLAG)
private val scoreBuffer = StringBuilder(8)
```

Compose 侧没有“每帧分配 Paint”的问题——`DrawScope` 把画笔封装了，但 `Path`、`Brush`、`TextLayoutResult` 仍然会分配。用 `Modifier.drawWithCache` 把与尺寸相关的对象缓存起来：

```kotlin
Modifier.drawWithCache {
    val path = Path().apply { /* 只随 size 变 */ }
    val brush = Brush.linearGradient(colors, Offset.Zero, Offset(size.width, 0f))
    onDrawBehind { drawPath(path, brush) }
}
```

`drawWithCache` 的 `CacheDrawScope` 在尺寸变化或读取的状态变化时重建缓存，其余时候复用。它和 View 的 `onSizeChanged` 思路一样，只是 API 更明确。

### 第二类：失效范围扩散

View 体系里，`requestLayout()` 会一路冒泡到 `ViewRootImpl`，触发整棵树的测量；`invalidate()` 的范围则取决于父容器是否愿意接受局部脏区。Compose 里，状态被读取的位置决定失效范围。

两边常见的错误正好相反：

- View 侧：为了改一个颜色调 `requestLayout()`，或者动画里改宽高。测量布局的代价是绘制的几倍，能只 `invalidate()` 就别 `requestLayout()`。
- Compose 侧：把只影响绘制的状态放在组合阶段读（比如在 `Canvas` 的参数里读 `animatedProgress`），导致动画期间每帧重组整块 UI。把读取下沉到 `drawBehind` / `graphicsLayer { }` / `drawWithCache` 里，失效范围就缩小到绘制。

排查 Compose 的重组范围，Android Studio 的 Layout Inspector 有 “Recomposition Counts” 视图；代码里可以在 `Modifier.drawWithContent` 或可组合函数里加计数器。要更底层，看 Perfetto 里 `Recomposer` 的 trace slice。

### 第三类：把布局当动画

前面提过，但它在真实项目里太常见，单独拎出来说：用 `ValueAnimator` 改 `layoutParams.height`，每帧 `requestLayout()`；或者用 `AnimatedContent` 让整个页面重新组合。布局动画不是不能做，但要用对工具：

- 只改“画在哪”而不是“占多大”→ `translationX/Y`、`scaleX/Y`、`alpha`，View 侧走 `invalidate`，Compose 侧走 `graphicsLayer`；
- 确实要改尺寸 → `TransitionManager` / `ChangeBounds`（View）或 `animateContentSize`（Compose），它们内部做了优化，但仍然比属性动画贵；
- 列表尺寸变化 → 用 `RecyclerView` 的 `ItemAnimator` 或者 Compose 的 `animateItem()`，不要自己每帧重新布局整个列表。

### 第四类：把测量当成免费操作

自定义 ViewGroup 的测量是递归的。父容器测量子 View，子 View 又测量它的子 View；`onMeasure` 里的每一行代码都会乘上子 View 的数量。尤其是“为了知道子 View 多大，先测一遍，换行后再测一遍”的写法，在标签数量多的时候会把测量成本翻倍。

能省的测量：

- 同一个子 View 不要测量两次：`measureChildWithMargins` 一次拿到结果，换行判断用已有结果，别为了“看看多宽”再测一遍；框架的测量缓存只覆盖 spec 完全相同的场景，救不了这种主动重复。
- 用 `measureChildWithMargins` 而不是自己拼 `MeasureSpec`：前者把 padding、margin、父约束一起算对，后者漏一项就会出现“测量和布局对不上”。
- 避免在测量阶段做文本排版；文本的 `StaticLayout` 放到 `onSizeChanged` 或内容变化时创建。
- 对 `GONE` 的子 View 直接跳过。
- 对固定尺寸的 ViewGroup 子项，缓存它上次的测量结果，spec 没变就直接复用；父容器尺寸变化时再清掉缓存。

Compose 侧的测量成本模型不同，但同样有“测量次数”问题：一个 `Measurable` 只能测一次；`SubcomposeLayout` 在测量阶段执行子组合，用不好会反复组合+测量；`LazyLayout` 的复用机制则是把“只测量可见项”做成了框架能力。写自定义布局时，尽量让“测量”和“摆放”分成两个清晰的循环，不要在一次循环里边测边摆。

### 症状、原因、工具

| 症状 | 先怀疑什么 | 用什么看 |
|---|---|---|
| 动画掉帧、滑动不跟手 | `onDraw` 分配、display list 重建、布局每帧执行 | `dumpsys gfxinfo`、Perfetto、Layout Inspector |
| 页面首帧慢 | 测量层级深、`onMeasure` 重、字体/图片首次加载 | Perfetto 的 `measure`/`layout` slice、Macrobenchmark |
| 列表滑动卡 | item 布局复杂、`onBindViewHolder` 里做重活、Compose 重组范围大 | Layout Inspector、Perfetto、Compose 编译器报告 |
| 内存抖动、GC 频繁 | 绘制/测量里分配对象、字符串拼接、lambda 捕获 | Android Studio Memory Profiler、Perfetto 的 `heap` 事件 |
| 过度绘制 | 背景叠背景、半透明遮罩、不必要的 `foreground` | 开发者选项 “Debug GPU overdraw”、`dumpsys gfxinfo` |
| Compose 页面发热 | 每帧重组、`SubcomposeLayout` 滥用、无限动画 | Layout Inspector 重组计数、Perfetto `Recomposer` |
| 自定义 ViewGroup 卡 | 测量次数多、`requestLayout` 风暴、`onLayout` 里做计算 | Perfetto、`adb shell dumpsys gfxinfo framestats` |

几个常用命令：

```bash
# 每帧各阶段耗时（Draw / Prepare / Process / Execute）
adb shell dumpsys gfxinfo com.example.app

# 更细的 framestats：每一帧的时间戳，可以算 jank
adb shell dumpsys gfxinfo com.example.app framestats

# 无 GUI 时查看当前 Activity 的视图层级（GUI 工具是 Layout Inspector）
adb shell dumpsys activity top

# 打开过度绘制调试（部分设备）
adb shell setprop debug.hwui.overdraw show
```

如果要进 CI 做回归，用 Macrobenchmark 的 `FrameTimingMetric` 比手工读 `gfxinfo` 可靠：它按 `CompilationMode` 固定编译状态、按 `StartupTimingMetric` / `FrameTimingMetric` 输出 P50/P90/P99。启动优化那篇文章里详细讲过这套工具，这里只强调一点：**自定义控件的性能回归，最好用“同一台设备、同一段脚本、同一编译模式”的 Macrobenchmark 来守**，靠人眼看动画是不够的。

![自定义控件的性能视角：阶段、失效范围与工具](/images/android-custom-widgets/11-perf.webp)

> 图里把一帧拆成三个阶段，每个阶段标出“常见浪费”和“该看的工具”。下半部分那条“失效范围”的刻度尺是核心：View 的 `requestLayout` 会推到整棵树，`invalidate` 只重录脏区的 display list 但仍要走一次 traversal；Compose 的失效范围由状态读取的阶段决定。写自定义控件时，先问“我这个变化应该影响多大范围”，再选 API。

## 12. 测试与验收：怎么证明它没坏

控件的测试和业务的测试不太一样。业务测试关心“点了按钮之后接口有没有发出去”，控件测试关心“在 0、无穷大、RTL、深色、字体放大两倍这些边界下，它还是不是那个控件”。很多控件在正常尺寸下完美，一到边界就露馅。

### View 侧：Robolectric、Espresso 与截图测试

Robolectric 可以在 JVM 上跑 `measure`/`layout`/`draw`，适合测测量和布局规则：

```kotlin
@Test
fun `ring fills at most constraint but never overflows`() {
    val context = RuntimeEnvironment.getApplication()
    val view = RingProgressView(context)

    val atMost = View.MeasureSpec.makeMeasureSpec(100, View.MeasureSpec.AT_MOST)
    view.measure(atMost, atMost)
    assertEquals(100, view.measuredWidth)
    assertEquals(100, view.measuredHeight)
}

@Test
fun `ring obeys exact constraint`() {
    val context = RuntimeEnvironment.getApplication()
    val view = RingProgressView(context)

    view.measure(
        View.MeasureSpec.makeMeasureSpec(200, View.MeasureSpec.EXACTLY),
        View.MeasureSpec.makeMeasureSpec(200, View.MeasureSpec.EXACTLY),
    )

    assertEquals(200, view.measuredWidth)
    assertEquals(200, view.measuredHeight)
}
```

Robolectric 的局限是：在 legacy graphics 模式下，它不跑真实的渲染管线，`Canvas` 的操作被记录而不是真的光栅化，硬件加速、阴影、部分 `Path` 行为测不出来；开启 native graphics 模式后会用真实 Skia 渲染，但和真机仍有差异（硬件加速、模糊、阴影）。所以它适合测“测量和布局的数值”，不适合单独承担“看起来对不对”的验收。

“看起来对不对”要靠截图测试。Paparazzi 在 JVM 上跑 LayoutLib，能渲染出接近真机的 PNG，适合放进 CI 做视觉回归：

```kotlin
class RingProgressScreenshotTest {
    @get:Rule
    val paparazzi = Paparazzi(deviceConfig = DeviceConfig.PIXEL_5)

    @Test
    fun ring_default() {
        paparazzi.snapshot(
            RingProgressView(paparazzi.context).apply { progress = 0.76f },
        )
    }
}
```

Paparazzi 跑的是渲染，不是真实设备，所以字体、阴影、部分 GPU 效果仍然可能有差异。它最大的价值是“同一份代码，改之前和改之后的两张图 diff”，而不是“和设计师的 Figma 逐像素对齐”。

Espresso 用来测交互：

```kotlin
@Test
fun dragRing_changesProgress() {
    onView(withId(R.id.score_ring))
        .perform(swipeLeft())   // 或者自定义 ViewAction 模拟拖动
    onView(withId(R.id.score_ring))
        .check(matches(hasProgress(0.5f, tolerance = 0.1f)))
}
```

自定义 View 的 Espresso 断言通常要写一个 `TypeSafeMatcher` 和一个 `ViewAction`。这层投入是一次性的，之后所有手势相关的回归都能复用。

### Compose 侧：`ComposeTestRule` 与手势

Compose 的测试 API 天生能访问语义树，断言尺寸、位置、手势都更直接：

```kotlin
class RingProgressTest {
    @get:Rule
    val rule = createComposeRule()

    @Test
    fun ring_hasExpectedSize() {
        rule.setContent {
            RingProgress(
                progress = 0.5f,
                modifier = Modifier
                    .testTag("ring")
                    .size(160.dp),
            )
        }

        rule.onNodeWithTag("ring")
            .assertWidthIsEqualTo(160.dp)
            .assertHeightIsEqualTo(160.dp)
            .assertIsDisplayed()
    }

    @Test
    fun drag_updatesProgress() {
        var progress by mutableFloatStateOf(0f)
        rule.setContent {
            RingProgress(
                progress = progress,
                modifier = Modifier
                    .testTag("ring")
                    .size(160.dp)
                    .pointerInput(Unit) {
                        detectDragGestures { change, dragAmount ->
                            progress = (progress + dragAmount.x / size.width)
                                .coerceIn(0f, 1f)
                            change.consume()
                        }
                    },
            )
        }

        rule.onNodeWithTag("ring").performTouchInput {
            swipe(start = center, end = center + Offset(40f, 0f))
        }

        assertTrue(progress > 0f)
    }
}
```

几个 Compose 测试特有的点：

- **`mainClock`**：动画和 `LaunchedEffect` 的协程由测试时钟驱动。默认 `autoAdvance = true`，测试会等动画跑完；需要逐帧断言时 `mainClock.autoAdvance = false`，手动 `advanceTimeBy()`。
- **语义断言**：`assertContentDescriptionEquals`、`assertSemantics`、`assertIsNotEnabled`、`assertIsSelected` 都是直接读语义树。控件如果没写语义，测试也测不出无障碍问题——语义是测试和 TalkBack 的共同接口。
- **`performTouchInput`** 可以模拟 `click`、`swipe`、`pinch`、`longClick`，坐标基于节点边界。写手势测试时，最好用 `center`、`topLeft` 这些相对坐标，别写死像素。
- **截图测试**：Roborazzi 是 Compose 生态里常用的截图测试库，基于 Robolectric + 真实渲染；Paparazzi 也支持 Compose。两者都能在 JVM 上跑，适合 CI。

### 应该测哪些边界

控件的测试用例可以按“输入空间的边界”来设计，而不是按代码分支：

| 维度 | 边界 | 期望 |
|---|---|---|
| 尺寸 | `0dp`、`wrap_content`、`match_parent`、`EXACTLY(1px)` | 不崩溃、不返回负数、内容不溢出 |
| 父约束 | `UNSPECIFIED`、`AT_MOST(0)`、超大尺寸 | 有合理默认值，不返回 `Int.MAX_VALUE` |
| 内容 | 空字符串、超长文本、emoji、从右到左的文字 | 不裁剪、不重叠、可换行 |
| 方向 | LTR / RTL | 镜像正确，图标/数字按规则处理 |
| 主题 | 深色 / 浅色 / 动态取色 | 对比度达标，不出现黑底黑字 |
| 字号 | 系统字体放大 1.3x / 2.0x | 布局不破，可滚动或省略 |
| 状态 | 转屏、进程重建、列表复用 | 状态恢复或按设计重置 |
| 交互 | 快速点击、长按、拖出边界、多指 | 不崩溃，有 `ACTION_CANCEL` 处理 |
| 无障碍 | TalkBack 聚焦、双击、滑动浏览 | 可读、可操作、有状态播报 |

这张表可以直接变成测试清单。很多团队的做法是：单元测试覆盖前两行，截图测试覆盖主题和字号，手工测试覆盖无障碍和手势，剩下的交给灰度。

### 验收时别只看“像不像”

控件验收有一个常见误区：只对设计稿。设计稿是静态的，控件是动态的。验收时至少要过一遍：

1. **三档字体**（系统默认、1.3x、2.0x）；
2. **两种方向**（LTR、RTL）；
3. **两套主题**（浅色、深色）；
4. **键盘/遥控器**走一遍焦点；
5. **TalkBack** 打开，闭眼操作一遍；
6. **低端机**滑动一遍，看有没有掉帧；
7. **转屏、后台回来、进程重建**各一次。

这七条走完，大部分“上线后才发现”的问题都能提前暴露。

## 13. 尾声：控件是一份契约，不是一张画

回到开头那个 PR。最终的 `RingProgressView` 大概三百行，其中真正画圆环的不到四十行，剩下的都在处理测量、状态、无障碍、RTL 和动画。QA 提的三个问题分别这样收场：

- 横屏下被拉成椭圆：`onMeasure` 用 `resolveSizeAndState` 尊重父约束，`onDraw` 用 `min(width, height)` 算半径，非正方形时居中画圆，不再依赖“父容器一定会给我正方形”这个假设；
- TalkBack 读不出分数：`contentDescription` + `setStateDescription` + `performClick()`；按下时读名字，拖动结束时更新状态描述（Android 16 起 announcement 类 API 已弃用）；
- 列表回来变成 0：`onSaveInstanceState` + `BaseSavedState`，把进度存进状态，恢复时还原。

这三个修复没有一个是“画”的问题。它们对应的是控件对外的三份契约：尺寸、语义、状态。这也是我越来越觉得“自定义控件”应该被叫作“自定义契约”的原因——画法可以换，View 可以换成 Compose，契约不能丢。

### 一个控件需要具备的六种能力

如果把整篇文章压缩成一张清单，一个合格的自定义控件需要具备六种能力：

| 能力 | View 体系的落点 | Compose 的落点 |
|---|---|---|
| **量**：在父约束下给出尺寸 | `onMeasure` + `resolveSizeAndState` | `MeasurePolicy.measure` + `Constraints` |
| **摆**：把子元素放到正确的位置 | `onLayout` + `layout()` | `placementBlock` + `placeRelative` |
| **画**：把状态渲染成像素 | `onDraw` + `Canvas`/`Paint` | `DrawScope` + `drawBehind`/`Canvas` |
| **摸**：接住并解释输入 | `onTouchEvent` / `NestedScrolling` | `pointerInput` + `nestedScroll` |
| **说**：让无障碍服务读懂它 | `AccessibilityNodeInfo` / `ExploreByTouchHelper` | `Modifier.semantics` |
| **存**：在重建和复用中保持自己 | `onSaveInstanceState` / `BaseSavedState` | `rememberSaveable` + `Saver` |

六种能力里，前三种是“看得见的”，后三种是“看不见的”。大多数人写自定义控件时只准备前三种，然后在上线后被后三种教做人。这篇文章花了很多篇幅讲后三种，原因就在这里：它们不产生视觉上的成就感，但决定了一个控件能不能被放心复用。

![一个控件的能力地图：量、摆、画、摸、说、存](/images/android-custom-widgets/12-capabilities.webp)

> 图把六种能力排成六边形，每张卡标出 View 与 Compose 的落点，中心是“合格控件”。它是一张检查表，不是评分卡；能进设计系统、被长期复用的控件，六格都不会空着。

### View 和 Compose，不是替代关系

写到这里，两套体系的对照已经铺满了全文。如果一定要给一个选择建议：

- **新项目、新页面、设计系统从零开始**：用 Compose。它的布局和绘制抽象更统一，状态驱动的心智模型更适合复杂交互，测试和预览也更方便。
- **存量项目、重资产组件、对启动/包体/渲染有硬约束的场景**：View 仍然是更稳的选择。地图、相机、播放器、复杂图表这些组件，短期内不会因为 Compose 重写而变好。
- **混合项目**：让边界清晰——Compose 页面用 `AndroidView` 包 View 组件，View 页面用 `ComposeView` 包 Compose 区块；不要在单个控件内部混用两套渲染。

需要提醒的是，Compose 并不是“另一套与 View 平行的系统”。`ComposeView` 本身就是一个 `View`，Compose 的 UI 最终也是通过 `AndroidComposeView` 的 `onDraw`/`dispatchDraw` 进入同一个渲染管线。你在 View 体系里学到的测量、布局、绘制、失效传播、硬件加速，在 Compose 里换了个名字继续存在。反过来，Compose 的“阶段化失效”“状态读取位置决定重组范围”这些概念，也在影响新一代 View 组件的设计（比如 `RecyclerView` 的 `Prefetch`、`ComposeView` 的 `ViewCompositionStrategy`）。

所以这篇文章的“两条路”，其实是同一条路上的两种走法。控件是给别人的代码用的：给它多少空间它都知道怎么活，转屏之后它还认得自己，看不见屏幕的人也能用它，滑动手势不会和页面打架。把这些做到，画成什么样都只是审美问题。

## 附：版本速查

| 能力 / API | 起始版本 | 说明 |
|---|---|---|
| `View.onMeasure` / `onLayout` / `onDraw` | API 1 | 自定义 View 的三个基本钩子 |
| `View.MeasureSpec` / `resolveSizeAndState` | API 1 / API 11 | 测量约束与状态位 |
| `ViewGroup.getChildMeasureSpec` / `measureChildWithMargins` | API 1 | 子 View 约束推导与带 margin 的测量 |
| `View.setWillNotDraw` | API 1 | ViewGroup 默认不画自身内容 |
| `View.BaseSavedState` | API 1 | 自定义 View 的状态保存基类 |
| `ViewConfiguration.getScaledTouchSlop` | API 3 | 触摸拖动阈值（实例方法） |
| `View.isInEditMode` | API 3 | 仅布局预览时返回 true |
| `View.setContentDescription` | API 4 | 无障碍描述 |
| `MotionEvent.getActionMasked` / `getPointerId` | API 8 / API 5 | 多点触控的正确读法 |
| `OverScroller` | API 9 | 带越界回弹的 fling 计算 |
| `View.setLayerType` | API 11 | 硬件层 / 软件层 |
| `Canvas.isHardwareAccelerated` | API 11 | 运行时判断是否硬件加速 |
| `LayoutInflater.Factory2` | API 11 | inflate 时替换 View 创建 |
| `View.setAccessibilityDelegate` | API 14 | 无障碍节点定制 |
| `View.setImportantForAccessibility` | API 16 | `AUTO / YES / NO / NO_HIDE_DESCENDANTS` |
| `View.postInvalidateOnAnimation` | API 16 | 对齐下一帧的 invalidate |
| `View.announceForAccessibility` | API 16 | Android 16 起弃用（受 flag 门控），推荐语义化描述 |
| `View.setPaddingRelative` / `getLayoutDirection` / `onRtlPropertiesChanged` | API 17 | RTL 适配三件套 |
| `View.setAccessibilityLiveRegion` | API 19 | 内容变化自动播报 |
| `ViewOutlineProvider` / `setElevation` | API 21 | 轮廓、阴影、裁剪 |
| `View(Context, AttributeSet, int, int)` 四参构造 | API 21 | 支持 `defStyleAttr` + `defStyleRes` |
| `ViewParent.onDescendantInvalidated` | API 26 | 现代 invalidate 冒泡链 |
| `View.setAccessibilityPaneTitle` / `setAccessibilityHeading` | API 28 | 面板标题与标题语义 |
| `android.graphics.RenderNode` | API 29 | 公开的 RenderNode API（内部实现自 API 21） |
| `View.setStateDescription` | API 30 | 状态语义（进度、开关等） |
| `TypedArray` 实现 `AutoCloseable` | API 31 | 可用 `use { }` / try-with-resources 代替手动 `recycle()` |
| `NestedScrollingChild3` / `Parent3` | androidx.core 约 1.1.0 | 带 `type` 与 `consumed` 的嵌套滚动 |
| `ExploreByTouchHelper` | androidx.customview | 自绘控件的虚拟子节点无障碍 |
| `Layout`（单 content） | Compose 1.0 | 自定义布局入口 |
| `Layout`（零子节点 / 多 content） | Compose 1.1 / 1.10 | 后者的策略是 `MultiContentMeasurePolicy` |
| `MeasurePolicy` / `Modifier.layout` | Compose 1.0 | 布局策略与单元素测量 |
| `Modifier.drawBehind` / `drawWithContent` / `drawWithCache` | Compose 1.0 | 绘制三件套 |
| `Modifier.graphicsLayer { }`（lambda） | Compose 1.0 | 图层属性更新，不触发重组 |
| `Modifier.graphicsLayer(...)` 命名参数版 | Compose 1.12 | 含 `blendMode`、`colorFilter`、`outsets` |
| `LayerOutsets` | Compose 1.12 | 扩大 layer 可视 bounds，避免 alpha/阴影被隐式裁剪 |
| `PointerEventPass`（Initial → Main → Final） | Compose 1.0 | 三 pass 的事件顺序 |
| `Modifier.pointerInput` 带 key 重载 | Compose 1.8 | 无 key 重载已弃用；block 类型为 `PointerInputEventHandler` |
| `Modifier.Node` 体系 | Compose 1.5 | 取代 `Modifier.composed` 的推荐写法（后者官方“不再推荐”，未确认 `@Deprecated`） |
| `LookaheadScope` | Compose 1.7 | lookahead pass + approach 布局 |
| `computedStateOf` | Compose 1.13 | 轻量状态推导，不缓存 |
| Compose 1.12 | 2026-08 | compileSdk 37；AGP 最低版本官方两处说法不一致（博客 9.1.2 / release notes 9.2.0） |
| Compose 1.13 / BOM 2026.11.01 | 2026-11 | 本文基线；要求 Kotlin 2.2+、compileSdk 37.1；Compose 默认 minSdk 自 1.10 起为 23 |

说明：表中 `NestedScrollingChild3/Parent3` 的 androidx 版本号来自 release notes 摘要，未逐字核对发布页；Android 17（API 37）的系统行为不在本文核实范围内，涉及系统版本的地方以 Android 8.0 ~ 16 为准。

## 参考资料

- [Android 官方：自定义 View 组件](https://developer.android.com/develop/ui/views/custom-views/create-view)
- [Android 官方：`View` 参考](https://developer.android.com/reference/android/view/View)
- [Android 官方：`View.MeasureSpec`](https://developer.android.com/reference/android/view/View.MeasureSpec)
- [Android 官方：`ViewGroup`](https://developer.android.com/reference/android/view/ViewGroup)
- [AOSP：`View.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/view/View.java)
- [AOSP：`ViewGroup.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/view/ViewGroup.java)
- [AOSP：`ViewRootImpl.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/view/ViewRootImpl.java)
- [AOSP：`Choreographer.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/view/Choreographer.java)
- [Android 官方：硬件加速](https://developer.android.com/topic/performance/hardware-accel)
- [Android 官方：`Canvas`](https://developer.android.com/reference/android/graphics/Canvas)
- [Android 官方：`ViewOutlineProvider`](https://developer.android.com/reference/android/view/ViewOutlineProvider)
- [Android 官方：`MotionEvent`](https://developer.android.com/reference/android/view/MotionEvent)
- [Android 官方：`ViewConfiguration`](https://developer.android.com/reference/android/view/ViewConfiguration)
- [Android 官方：`OverScroller`](https://developer.android.com/reference/android/widget/OverScroller)
- [Android 官方：样式资源与自定义属性](https://developer.android.com/guide/topics/resources/style-resource)
- [Android 官方：`TypedArray`](https://developer.android.com/reference/android/content/res/TypedArray)
- [Android 官方：`View.BaseSavedState`](https://developer.android.com/reference/android/view/View.BaseSavedState)
- [Android 官方：`ExploreByTouchHelper`](https://developer.android.com/reference/androidx/customview/widget/ExploreByTouchHelper)
- [Android 官方：Macrobenchmark 指标](https://developer.android.com/topic/performance/benchmarking/macrobenchmark-metrics)
- [Robolectric 官方文档](https://robolectric.org/)
- [Paparazzi 官方文档](https://cashapp.github.io/paparazzi/)
- [Android 官方：Compose 阶段（composition / layout / draw）](https://developer.android.com/develop/ui/compose/phases)
- [Android 官方：Compose 自定义布局](https://developer.android.com/develop/ui/compose/layouts/custom)
- [Android 官方：`Layout` Composable](https://developer.android.com/reference/kotlin/androidx/compose/ui/layout/Layout.composable)
- [Android 官方：`MeasurePolicy`](https://developer.android.com/reference/kotlin/androidx/compose/ui/layout/MeasurePolicy)
- [Android 官方：Compose 图形与绘制](https://developer.android.com/develop/ui/compose/graphics/draw/overview)
- [Android 官方：`DrawScope`](https://developer.android.com/reference/kotlin/androidx/compose/ui/graphics/drawscope/DrawScope)
- [Android 官方：`Modifier.graphicsLayer`](https://developer.android.com/reference/kotlin/androidx/compose/ui/graphics/graphicsLayer.modifier)
- [Android 官方：Compose 手势与 pointerInput](https://developer.android.com/develop/ui/compose/touch-input/pointer-input/understand-gestures)
- [Android 官方：`PointerEventPass`](https://developer.android.com/reference/kotlin/androidx/compose/ui/input/pointer/PointerEventPass)
- [Android 官方：`NestedScrollConnection`](https://developer.android.com/reference/kotlin/androidx/compose/ui/input/nestedscroll/NestedScrollConnection)
- [Android 官方：Compose 自定义 Modifier（`Modifier.Node`）](https://developer.android.com/develop/ui/compose/custom-modifiers)
- [Android 官方：在 View 中使用 Compose](https://developer.android.com/develop/ui/compose/migrate/interoperability-apis/compose-in-views)
- [Android 官方：`AndroidView`](https://developer.android.com/reference/kotlin/androidx/compose/ui/viewinterop/AndroidView.composable)
- [Android 官方：Compose 性能](https://developer.android.com/develop/ui/compose/performance)
- [Android 官方：Compose 测试](https://developer.android.com/develop/ui/compose/testing)
- [Android 官方：Compose 无障碍语义](https://developer.android.com/develop/ui/compose/accessibility/semantics)
- [Android 官方：Compose 状态保存](https://developer.android.com/develop/ui/compose/state-saving)
- [Android 官方博客：Jetpack Compose 2026 年 11 月版（1.13）](https://developer.android.com/blog/posts/jetpack-compose-november-2026-release)
- [Android 官方：Compose BOM 版本映射](https://developer.android.com/develop/ui/compose/bom/bom-mapping)

---

> 文中代码是教学示例，省略 import 与日志，分散在第 3～8 节的实现拼起来才是一个可编译的控件。配图由 `scripts/make_diagrams.py` 生成，图内结论与正文一一对应；调研笔记与“未核实”标注保留在 `research/` 下。版本口径以 2026 年 11 月的稳定版为准：Compose 1.13 / BOM 2026.11.01，View 侧覆盖 Android 8.0 ~ 16（API 26 ~ 36）。
