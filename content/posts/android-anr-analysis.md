---
title: 五秒、十秒、二十秒：Android ANR 的触发原理与分析流程
dek: 五类超时、一条证据链
excerpt: 结账页点了提交没反应，几秒后弹出「应用没有响应」。ANR 的超时阈值分五类，每一类记在同一个地方、判据却不同。文中把触发条件、trace 结构、主线程状态和排查顺序连成一条链。
date: 2026-11-24
time: 08:52
minutes: 35
tag: Android
hot: true
note: 文中的日志与 trace 片段是按真实格式改写的示例，不是某台设备的原始输出；超时数值以 Android 15 源码默认值为准。
---
![封面：五秒、十秒、二十秒](/images/android-anr-analysis/00-cover.webp)

先看一张很常见的工单。

用户在结账页点了“提交订单”，界面没有反应，过了几秒弹出“应用没有响应”。测试同学把手机连上电脑，抓到了这一段：

```text
ANR in com.example.shop (com.example.shop/.CheckoutActivity)
PID: 12866
Reason: com.example.shop/com.example.shop.CheckoutActivity (server) is not responding.
       Waited 5002ms for MotionEvent(action=ACTION_UP, deviceId=3, source=0x1002)
Frozen: false
```

Reason 写得清清楚楚：一次抬起事件等了 5 秒没有被处理。接下来去看 trace 里主线程停在什么地方：

```text
"main" prio=5 tid=1 Sleeping
  | group="main" sCount=1 dsq=0 obj=0x12c00c20 self=0xb400007a1e2a0000
  | sysTid=12866 nice=-10 cgrp=default sched=0/0
  | state=S schedstat=( 1892223441 22011340 317 ) utime=12345 stime=6543 core=1 HZ=100
  | held mutexes=
  at android.os.MessageQueue.nativePollOnce(Native Method)
  at android.os.MessageQueue.next(MessageQueue.java:335)
  at android.os.Looper.loop(Looper.java:214)
  at android.app.ActivityThread.main(ActivityThread.java:7710)
  at java.lang.reflect.Method.invoke(Native Method)
```

主线程在睡觉。它在等下一条消息，没有任何事情卡着它。

于是工单的结论一栏写上了“无法定位，疑似偶发”，状态改成关闭。两个版本之后，同样的反馈又来了，Reason 还是那一行。

这类工单走不下去，多半不是 trace 不够多，而是一开始对 ANR 的理解就偏了。“ANR 就是主线程卡了 5 秒”这句话太顺口，它只说中了七个定时器里的一种，而且连这一种也没说准。更准确的说法是：系统和你所在的进程之间有几份带期限的约定，ANR 是某一份约定没有按时兑现时，系统给出的判定。每一份约定都有自己的计时起点、完成标志、期限长短，还有一条“到时候算在谁头上”的规则。

把这几份约定逐个拆开，再看清超时之后系统做了什么、现场留在了哪里，开头那张工单其实是有路可走的。下面按这个顺序展开。没有特别说明时，源码指的是 Android 15（`android-15.0.0_r1`），和别的版本不一样的地方会单独标出来。

## 1. 先对齐一下：ANR 到底指什么

“ANR”这个词在日常讨论里至少指三样东西，它们并不总是同时出现。

第一样是用户看到的那个弹窗。标题是“某某没有响应”，正文问“要关闭它吗？”，下面两个选项，“等待”和“关闭应用”。如果应用在 manifest 里声明了接收错误报告的组件，还会多一个反馈入口。

第二样是系统记下来的一次事件。主日志里的 `ANR in …`，events 日志里的 `am_anr`，以及 `/data/anr` 下的一份线程快照，都属于这一样。

第三样是进程的退出原因，`ApplicationExitInfo.REASON_ANR`。

三样东西的组合方式有好几种。进程在后台时，系统判定 ANR 之后直接结束它，不弹窗，这种叫静默 ANR：有事件记录和退出原因，用户什么都没看到。用户点了“等待”之后弹窗消失，进程还活着：有事件记录，暂时没有退出原因。所以“用户没反馈”和“线上没有 ANR”之间不能画等号，统计 ANR 得看后两样。

events 日志里，一次静默 ANR 前后大概是这样的（字段依次是用户、pid、包名、flags、原因）：

```text
am_proc_start: [0,12866,10089,"com.example.shop","service","com.example.shop/.SyncService"]
am_anr: [0,12866,"com.example.shop",8094561,"executing service com.example.shop/.SyncService, waited 20003ms"]
am_kill: [0,12866,"com.example.shop",905,"bg anr",142988]
am_proc_died: [0,12866,"com.example.shop",905,5]
```

整个过程中屏幕上什么都没发生，线上却多了一条 ANR。

ANR 和崩溃经常被放在一起说，它们的机制差得很远：

表：崩溃和 ANR 的差别

| | 崩溃 | ANR |
|---|---|---|
| 谁发现的 | 应用进程自己，异常没有接住 | 系统，某个期限到了 |
| 判定时进程的状态 | 已经要退出了 | 还活着，可能还在跑 |
| 现场 | 异常栈，从抛出点到入口 | 所有线程的栈，某一时刻的快照 |
| 分析的入手点 | 看异常栈，一般能直接定位 | 先弄清是哪个期限，再对时间窗 |

崩溃的那份栈天然带着因果：从异常往上是完整的调用路径。ANR 的栈只是“dump 那一刻各个线程停在哪”，因果要自己拼。这是它难查的根本原因，后文的整套流程都是围着这一点设计的。

还有一件事容易忽略：计时的人是系统，应用自己毫不知情。输入事件由 system_server 里的 InputDispatcher 数着秒表，Service 和广播的定时器最终也以消息的形式回到 system_server 的 Handler 上。期限快到的时候不会有任何回调通知你，也没有办法在应用里问一句“我还剩几秒”。应用内那些主线程卡顿监控，靠的是另外的办法去猜，和系统真正用来判定的那把尺子不是一回事。有些卡顿监控一切正常，线上却堆着一批 ANR，原因就在这里。

## 2. 七个定时器

![七个定时器：谁在计时，什么算完成](/images/android-anr-analysis/01-timers.webp)

图里的七行就是全部入口。每行有四样东西要记住：谁来计时，从哪一刻到哪一刻，期限多长，出了事记在谁头上。后面的小节按这个顺序逐个讲。

先说一个贯穿所有行的细节。这些秒数都是源码里的默认值，运行时还要乘一个系统属性 `ro.hw_timeout_multiplier`（代码里是 `Build.HW_TIMEOUT_MULTIPLIER`），默认是 1。硬件比较慢的设备上，厂商可能把它调大，于是同样的代码在一台机器上 5 秒出事，换一台要 7 秒。拿不同机型的日志对时间的时候，记得先确认这个值。

### 2.1 输入分发：5 秒

![输入超时：按事件计时，一个事件一个期限](/images/android-anr-analysis/02-input.webp)

输入事件由 system_server 里的 InputDispatcher 分发。事件交给应用某条连接的那一刻，分发器会记下它的期限，放进一个按到期时间排序的集合（`AnrTracker`）；应用处理完这个事件、调用 `finishInputEvent` 之后，这条记录被拿掉。分发器每次醒来都看一眼最早的那个期限，过期了就判定这条连接无响应。

所以输入超时是按事件计的。图里的 A 是最常见的样子：主线程正在处理这次点击，处理了很久，这个事件迟迟没有 finish，5 秒一到，判定成立。

“处理完”的判定比直觉要晚一点。事件到了应用这边，要在 `ViewRootImpl` 里依次经过若干个 `InputStage`。和业务代码有关的那一级叫 `ViewPostImeInputStage`，它把事件分发给整棵 View 树，`onTouchEvent()`、`onClick()` 都在这一级里执行。全部走完，最后一级才调用 `finishInputEvent()`，通知分发器这个事件结束了。`onClick()` 里的同步请求、耗时计算，都算在这个事件的“处理时间”里。

判定成立之后，分发器会把这条连接上还在排队的输入事件全部取消，日志里能看到 `Canceling events for … because it is unresponsive`。应用恢复响应之后，分发器会收到通知，再恢复正常分发。

图里的 B 是另一回事，也是开头那张工单容易走偏的地方。主线程被一条别的消息占住了，这段时间用户没有碰屏幕，那么此刻没有任何事件在等 finish，`AnrTracker` 是空的，分发器不会做任何判定。用户觉得界面已经死了，弹窗却迟迟不出现。直到他忍不住点了一下，新的事件排到后面，从这一下开始数 5 秒，弹窗才出来。“卡死了但一碰才弹”，说的就是它。

反过来也成立：没有输入事件的时候，主线程卡多久都不会触发输入 ANR。一个页面在 `onCreate()` 里做了 30 秒的事，只要用户在这 30 秒里不碰它，系统不会有任何表示，启动画面一直挂着而已。AOSP 没有给 Activity 的生命周期回调单独设过定时器。

Reason 的写法有好几年没变过：从 Android 11 到 16，`InputDispatcher.cpp` 里都是下面这一句。窗口无响应时是这样：

```text
Reason: com.example.shop/com.example.shop.CheckoutActivity (server) is not responding.
       Waited 5002ms for MotionEvent(action=ACTION_UP, deviceId=3, source=0x1002)
```

`Waited` 后面的毫秒数、事件的类型，都在这一行里，排查时先看它。另一种情况是应用已经获得焦点，却还没有可用的焦点窗口，比如正在切换页面的时候用户点了下去，这时 Reason 是 `xxx does not have a focused window`。

还有两个例外。进程正在被调试器附加时，系统收到超时通知后会告诉分发器“稍后再查”，于是一直搁置下去，挂着调试器是等不到输入 ANR 的。正在跑 instrumentation、或者用 wrapper 方式启动的进程，期限放宽到 60 秒。

### 2.2 Service 回调：20 秒，后台 200 秒

![Service 的两个定时器](/images/android-anr-analysis/03-service.webp)

关于 Service 的 20 秒，流传最广的一种说法是“`startService()` 之后不调 `stopSelf()` 就会 ANR”。源码里不是这样。

系统每调度一次回调，就把这个 Service 的执行计数加一（`bumpServiceExecutingLocked`）；进程里第一次出现“执行中”的 Service 时，启动定时器。应用这边回调返回后，会主动向系统报告一次完成（`serviceDoneExecuting`），计数减一；进程里的执行计数归零时，定时器被取消。

也就是说，20 秒量的是一次回调从派发到返回。`onStartCommand()` 把活交给子线程然后马上返回，哪怕这个 Service 之后运行一整天，也不会有 Service ANR。反过来，`onStartCommand()` 在主线程上同步做了 21 秒的事，就算结尾调了 `stopSelf()`，该 ANR 还是 ANR。

被计时的回调有下面这些，每个各算一次：

表：Service 里被计时的回调

| 回调 | 系统何时开始计时 | 系统何时认为完成 |
|---|---|---|
| `onCreate()` | 调度创建这个 Service | 应用报告 `onCreate()` 返回 |
| `onStartCommand()` | 派发每一次启动 | `onStartCommand()` 返回，且排队中的写盘已完成 |
| `onBind()` / `onRebind()` | 请求绑定 | 应用发布 Binder，系统转交给客户端 |
| `onUnbind()` | 通知解绑 | 应用处理完解绑 |
| `onDestroy()` | 决定销毁 | `onDestroy()` 返回，且排队中的写盘已完成 |

表里有两行出现了“排队中的写盘”，对应左图里那一段 `QueuedWork.waitToFinish()`。它在 `onStartCommand()` 返回之后、向系统报告完成之前执行，等的是 `SharedPreferences.apply()` 排队的写盘。`apply()` 的卖点是异步，但框架在 `onStartCommand()`、`onDestroy()`，以及 Activity 的 `onPause()`、`onStop()` 结束前，会把积压的写盘同步等完。配置文件比较大、又恰好在回调里改了好几处的应用，栽在这一步上的相当多。栈顶停在 `onStartCommand()` 已经返回之后的位置，很容易看漏。

```kotlin
override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    val changed = syncEngine.tick()                         // 这一段的耗时算在 20 秒里
    prefs.edit()
        .putLong("last_tick", System.currentTimeMillis())
        .putString("cache", bigJson)
        .apply()                                            // 只是排队，写盘在后面被同步等完
    return START_NOT_STICKY
}
```

这个例子里，`tick()` 的时长和随后那次写盘的时长，都落在同一个 20 秒的区间里。

时限的长短看这次回调登记的是前台还是后台属性，前台 20 秒，后台 200 秒。定时器在进程 attach 之后才启动，fork 和启动进程的耗时不在区间内。它按进程共用一把，同一个进程里几个 Service 的回调叠在一起计时。

超时的 Reason 长这样：

```text
Reason: executing service com.example.shop/.SyncService, waited 20003ms
```

主日志里在它前面还有一行 `Timeout executing service: …`，后面跟着这个 Service 的完整状态。现场还在的话，`adb shell dumpsys activity services` 的输出开头会保留最近一次超时的 Service。

### 2.3 startForegroundService()：30 秒，再加 10 秒

同一张图的右半边。

用 `startForegroundService()` 启动的 Service 带着一个标记：必须在期限内调用 `startForeground()`。系统把启动命令派给应用时开始计时，30 秒内没有等到这个调用，系统先把这个 Service 停掉，再过 10 秒，正式报 ANR。Reason 是一句完整的话：

```text
Reason: Context.startForegroundService() did not then call Service.startForeground(): ServiceRecord{... u0 com.example.shop/.UploadService}
```

这 30 秒和 2.2 的 20 秒是两回事，互不影响。调了 `startForeground()` 只兑现了前台的承诺，`onStartCommand()` 本身仍然要在 20 秒内返回。

右图最下面还有一条分支：30 秒内应用自己把这个 Service 停掉了。此时“必须前台”的标记还没兑现，系统的处理是抛出 `ForegroundServiceDidNotStartInTimeException`。这是崩溃，不是 ANR，崩溃栈里通常能看到当初调用 `startForegroundService()` 的位置。

### 2.4 广播接收：10 秒，后台 60 秒

![广播超时：从派发到 finishReceiver()](/images/android-anr-analysis/04-broadcast.webp)

广播的计时从派发给某一个接收器开始，到系统收到 `finishReceiver()` 为止。`onReceive()` 正常返回，框架会替你调用 `PendingResult.finish()`，计时结束，这是图里的 A。如果在 `onReceive()` 里调了 `goAsync()`，返回之后框架不再代劳，计时一直持续到你手里那个 `PendingResult` 被显式 `finish()`，这是图里的 B。

B 是广播 ANR 里最典型的一种。主线程早就空闲了，某个后台任务迟迟不结束，也没人记得 finish：

```kotlin
override fun onReceive(context: Context, intent: Intent) {
    val pending = goAsync()
    executor.execute {
        val body = Api.refresh()          // 请求没有超时，卡在这里
        pending.finish()                  // 于是永远执行不到
    }
}
```

有两类接收器不参与计时。无序广播发给动态注册的接收器时，系统直接当作送达；另外少数几条系统广播被标记成了豁免，比如 `ACTION_PRE_BOOT_COMPLETED`。所以日志里看到的广播 ANR，Reason 要么是静态注册的接收器，要么是有序广播：

```text
Reason: Broadcast of Intent { act=android.intent.action.BOOT_COMPLETED flg=0x900000 cmp=com.example.shop/.BootReceiver }
```

软超时和硬超时之间有一次宽限。10 秒到点时，系统会看这个进程在这段时间里是不是“可以运行、却一直没排上 CPU”。如果是，就按被耽误的时长再宽限一次，上限同样是 10 秒，之后才正式判定。整机负载很高的时候，同一个广播的超时可能拖到十几秒才出现在日志里。

广播 ANR 的 Reason 经常带有误导性。它只说明这个定时器先到点了。开机完成时系统一口气发出几十条广播，每一条都要经过主线程；主线程被其中某一条的初始化拖住，后面那些只能排队，先到期的那个成了 Reason。真正的根因在它前面那条消息里，第 6.4 节有一个这样的例子。

### 2.5 ContentProvider：10 秒的发布期限，和一条由调用方掌握的 ANR

![Provider 的超时：一条结束进程，一条由调用方掌握](/images/android-anr-analysis/05-provider.webp)

Provider 这一块最容易读错，因为它有三条互不相同的路径，牵涉三个进程。

**发布期限，10 秒，到期是结束进程。** 客户端进程向 system_server 要一个 Provider，宿主进程还没起来的话，system_server 先把它拉起来。宿主 attach 之后，系统给它 10 秒的发布期限；10 秒内没有调用 `publishContentProviders()`，系统直接结束宿主进程。这一次不是 ANR，退出原因是 `REASON_INITIALIZATION_FAILURE`，杀进程的理由是 `timeout publishing content providers`。

**等待期限，20 秒，到期是客户端不再等。** 客户端这边另有一条 20 秒的期限，从它发起请求时算起，包含宿主进程启动的时间。等不到 Provider 发布，系统通知客户端“没发布”，客户端的 `acquireProvider()` 不再等待，拿到的是空，随后通常是一次 `Unknown URL` 开头的异常，应用自己的日志里会多一行 `Failed to find provider info for <authority>`。这一步本身不产生 ANR，系统日志里只会多一句 `Timeout waiting for provider … caller=<客户端>`。但客户端的主线程已经白等了 20 秒，只要用户在这期间碰过屏幕，客户端自己就会先来一次输入 ANR。

两个期限的起点不同，先后顺序取决于宿主启动花多久。启动很快、卡在 Provider 的 `onCreate()` 里，先到的是 10 秒那一档，宿主被结束。启动本身就慢，先到的往往是 20 秒那一档，客户端拿着空结果往下走，宿主随后再被结束。

**`ContentProvider not responding`，来自调用方打开的一个开关。** 这个 Reason 对应的 ANR，和上面两个期限都没有关系。调用方拿到 `ContentProviderClient` 之后，可以调 `setDetectNotResponding(时长)` 打开无响应检测；此后每一次调用超过设定的时长，调用方就会把 Provider 所在的进程报上去，系统判它 ANR。这个接口是 SystemApi，还要求 `REMOVE_TASKS` 权限，普通应用用不了。所以在自己的日志里看到这个 Reason，说明调用方是一个系统组件，或者 system_server 自己。

排查它的时候，宿主进程里要看的是名字以 `binder:` 开头的线程：它们在处理调用方发来的请求。第 6.5 节有一个例子。

### 2.6 进程启动：15 秒

Android 15 给 system_server 加了一个新的期限。`bindApplication` 下发给新进程之后开始计时，15 秒内没有等到进程调用 `finishAttachApplication()`，就会报一次 ANR，Reason 是 `Process … failed to complete startup`。这个调用发生在应用 `handleBindApplication()` 的末尾，所以它量的是这个方法的全程：安装所有 ContentProvider、跑完 `Application.onCreate()`。它同样有广播那样的软、硬两段。

Android 14 及之前的版本里没有这个定时器，启动再慢也只是慢。

### 2.7 被跳过和被放宽的情况

把前面散落的例外集中列一下，对照日志时省得回头翻：

表：定时器被跳过或放宽的几种情况

| 情况 | 结果 | 适用于 |
|---|---|---|
| 进程正被调试器附加 | 不判 ANR | 全部 |
| 进程在跑 instrumentation，或以 wrapper 启动 | 输入超时放宽到 60 秒 | 输入 |
| 系统正在关机 | 不判 ANR | 全部 |
| 同一进程的 ANR 已经在处理或排队 | 后来者直接丢弃 | 全部 |
| 进程已被冻结或等待冻结 | 部分 Service 回调的定时器推迟到解冻后 | Service |
| 进程可运行，但没排上 CPU | 软超时之后再宽限一次 | 广播、进程启动 |
| `ro.hw_timeout_multiplier` 大于 1 | 所有时限等比例放大 | 全部 |

最后一行的意思是，某些机型上的秒数比图里的大，对照的时候不要照着 5、10、20 去卡。

### 2.8 按源码推演，自己触发一遍

读源码得来的结论，最好亲手验证一次。下面几段代码是我按上面各节的计时规则写出来的，预期现象写在每一段后面。它们没有在真机上跑过，机型和系统版本不同，现象可能有出入；真跑出来和预期不一样，以真机为准。

```kotlin
// 1) 输入：处理一次点击用了 6 秒
binding.submit.setOnClickListener { Thread.sleep(6_000) }

// 2) Service 回调：onStartCommand() 用了 21 秒
class SlowService : Service() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Thread.sleep(21_000)
        return START_NOT_STICKY
    }
}

// 3) 广播：有序广播的接收器用了 11 秒
class SlowReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Thread.sleep(11_000)
    }
}
context.sendOrderedBroadcast(Intent(context, SlowReceiver::class.java), null)

// 4) 前台服务：拉起了服务，却没调 startForeground()
class ForgetfulService : Service() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_NOT_STICKY
}
ContextCompat.startForegroundService(context, Intent(context, ForgetfulService::class.java))
```

第 1 段：点一下按钮，手离开屏幕。5 秒多一点之后弹窗出现，Reason 里有 `is not responding. Waited 5` 开头的一串。

第 2 段：应用在前台时调用 `startService()` 启动它。约 20 秒后 Reason 是 `executing service …/.SlowService, waited 200xxms`。

第 3 段：约 10 秒后 Reason 是 `Broadcast of Intent { … cmp=…/.SlowReceiver }`。

第 4 段（Android 13 及以后）：30 秒时这个 Service 被停掉，再过 10 秒，Reason 是 `Context.startForegroundService() did not then call Service.startForeground()`。

第 2、3 段期间手不要碰屏幕。主线程被占着，一旦有输入事件在等，5 秒的输入超时会先到，Reason 就换成输入那一行了。这本身也是一个佐证：几个定时器同时在走的时候，先到期的那个决定 Reason 的内容。

Android 15 及以后的设备上，还可以在 `Application.onCreate()` 里睡 16 秒，验证进程启动的期限。

## 3. 超时之后，system_server 做了什么

![超时之后：system_server 里的一条流水线](/images/android-anr-analysis/06-pipeline.webp)

七个定时器的入口各不相同，最后都汇到同一个地方：`AnrHelper`。它之后的事情决定了现场长什么样，读 trace 之前最好心里有这张图。

**先对当事进程单独 dump 一次。** `AnrHelper` 收到报告后，第一件事是开一个线程，只把出事的那个进程的线程栈抓下来，存成一个临时文件。源码注释里的理由很直白：主进程的栈几百毫秒之后就可能过时，而一次完整的 ANR dump 平均要好几秒。所以 trace 文件里第一个进程那一段，通常最接近“卡住那一刻”。

**排队的 ANR 串行处理。** 一个专门的线程从队列里一条一条取。某条 ANR 在队列里等了超过 10 秒，系统认为别的进程的栈已经没有参考价值，只 dump 当事进程。开机后 10 分钟内也这样处理，为的是减轻系统负担。看到 trace 里只有应用自己、没有 system_server，先别急着说现场缺失，这多半就是原因。

**决定 dump 哪些进程。** 当事进程排第一；然后是父进程、system_server、常驻的系统进程；其余进程按 CPU 占用排，有时间才轮到它们。

**整份 dump 有 20 秒预算。** 超出预算就地中止，日志里会留下 `Aborting stack trace dump … deadline exceeded`。进程多、机器慢的时候，文件末尾的那些进程可能根本没有被 dump。

**分叉。** 系统看这个进程对用户是否“值得关心”：有可见的界面，或者有前台服务在运行，就弹窗；两者都没有，直接结束进程，杀进程的理由是 `bg anr`。弹窗上点“关闭应用”，进程结束，退出原因是 `REASON_ANR`；点“等待”，进程继续运行，如果它是输入类 ANR 而且仍然没有响应，过一会儿弹窗会再出现。

静默 ANR 要再强调一次。有前台服务的后台进程不算静默，会弹窗；剩下的后台进程被直接杀掉，用户和测试都看不到。它们只出现在 `am_anr` 日志和 `ApplicationExitInfo` 里，是线上统计最容易漏掉的一块。

一次 Service 超时，在主日志里留下的记录大致按这个顺序出现：

```text
ActivityManager: Timeout executing service: ServiceRecord{... com.example.shop/.SyncService}
ActivityManager: Collecting stacks for pid 12866 into temporary file /data/anr/temp_anr_1234.txt
ActivityManager: Dumping to /data/anr/anr_2026-11-24-10-12-31-204
ActivityManager: Collecting stacks for pid 1873
ActivityManager: Done dumping
ActivityManager: ANR in com.example.shop
ActivityManager: PID: 12866
ActivityManager: Reason: executing service com.example.shop/.SyncService, waited 20003ms
ActivityManager: ErrorId: 7c1f0a3e-5b2d-4c1e-9d6a-2f8b1e4c0a55
ActivityManager: Frozen: false
ActivityManager: CPU usage from 24ms ago: 62% 12866/com.example.shop: 48% user + 13% kernel ...
```

有两处顺序值得留意。`ANR in` 那几行是 dump 结束之后才打印的，而 events 日志里的 `am_anr` 在 dump 开始之前就写了，两边的时间戳对不上是正常的。`Collecting stacks for pid 1873` 那一行里的 1873 是 system_server，它通常是被 dump 的第二个进程。

`ErrorId` 只在系统正开着 trace 时才会生成。同一个编号会写进当时的 Perfetto 记录，两边可以互相对上；平时抓的日志里没有这一行。`CPU usage` 那一段是判定前后整机在忙什么，第 5 节会用到它。

## 4. 现场有哪些材料，怎么拿

一次 ANR 会留下五处痕迹。它们的内容有重叠，可得性差别很大，按手头的权限挑着拿。

表：ANR 留下的五处痕迹

| 材料 | 在哪 | 怎么拿 | 适合看什么 |
|---|---|---|---|
| 主日志 | logcat 的 main 缓冲区 | `adb logcat` | `ANR in`、`PID`、`Reason`、`Frozen`，外加一段 CPU 占用 |
| events 日志 | logcat 的 events 缓冲区 | `adb logcat -b events` | `am_anr` 一行一条，连同前后的进程启动和杀进程记录，适合按时间筛 |
| trace 文件 | `/data/anr/` 目录下，每次一个 | root 或 userdebug 可以直接 pull，否则从 bugreport 里解 | 所有被点名进程的线程栈，核心材料 |
| DropBox | `data_app_anr`、`system_app_anr` 这类 tag | `adb shell dumpsys dropbox --print data_app_anr` | 主日志那一段加 trace 的长期存档 |
| 退出记录 | 系统保留的最近若干次退出记录 | 应用自己调 API 取 | 线上场景，进程已经不在了也能拿到本进程那一段 trace |

最后一行对应的是 `ApplicationExitInfo`，4.1 节会展开。前四种材料对应的命令：

```bash
adb logcat -b events | grep -E "am_anr|am_kill|am_proc_start"

adb shell dumpsys dropbox --print data_app_anr | grep -A 200 com.example.shop

adb bugreport bugreport.zip          # 压缩包里 FS/data/anr/ 下是全部 anr_* 文件

adb pull /data/anr/ .                # trace 文件每次一个，默认保留最近 64 个
```

DropBox 有两个坑。对应 tag 可能被关闭；同一个进程的同类记录还会被限流。查不到记录不能说明没发生过，以 events 日志为准。

trace 文件的权限是 0600，只有系统读得到。手里没有 root 的时候，`adb bugreport` 是最省事的办法：导出的压缩包里带着 `/data/anr` 下的全部文件。

### 4.1 线上没有 root，怎么拿栈

线上的 ANR 发生在用户的手机上，上面几条命令都用不了。常见的办法有四种，能拿到的东西和代价各不相同：

表：线上取 ANR 信息的四种办法

| 办法 | 能拿到什么 | 局限 |
|---|---|---|
| 下次启动时读系统的退出记录 | 系统抓的本进程线程栈，加退出原因 | API 30 起；只有本进程那一段；要等进程再启动 |
| 监听 `/data/anr` 下文件的变化 | 系统写的 trace | 新系统上文件是 0600、名字不固定，普通应用读不到 |
| 接管抓栈用的 `SIGQUIT` 信号 | ANR 当时本进程的线程栈 | 要写一点 native 代码，和系统的抓栈过程抢时间 |
| 主线程看门狗 | 主线程卡了多久、卡在哪个方法 | 只知道“卡”，不知道系统判的是哪一类 |

第一种最稳，代码也最短，用的是系统的 `ApplicationExitInfo`：

```kotlin
val am = context.getSystemService(ActivityManager::class.java)
val exits = am.getHistoricalProcessExitReasons(context.packageName, 0, 8)

exits.filter { it.reason == ApplicationExitInfo.REASON_ANR }.forEach { info ->
    info.traceInputStream?.bufferedReader()?.useLines { lines ->
        val subject = lines.firstOrNull { it.startsWith("Subject:") }
        // Subject 就是 Reason 原文，按它分桶之后再上传
    }
}
```

它有两个限制。`traceInputStream` 里只有本进程那一段，别的进程的栈出于隐私不会交给应用。另外，进程被判了 ANR 却活了下来（用户点了“等待”），之后因为别的原因死掉时，这条记录仍然带着当时的 trace。所以它统计的是“发生过 ANR 的进程”，和“因 ANR 而死”略有出入。

第三种办法值得多说两句。系统抓 Java 栈的时候，会给目标进程发一个 `SIGQUIT`，由运行时专门的线程把所有线程的栈写出去。应用可以在 native 层用 `sigaction` 把这个信号的处理接管过来：先在一个独立线程里把自己的线程栈记下来，再把信号交还给原来的处理函数。开源的崩溃采集库里，XCrash 一类用的就是这个思路。它的好处是不用等进程下次启动，代价是多了一块和系统实现细节绑在一起的 native 代码。

第四种办法到处都有现成实现，定期往主线程抛一个任务，超时没有执行就记一笔。它和系统判定用的不是同一把尺子，拿它的数据去解释一个具体的 ANR 经常对不上，更适合用来看趋势。

## 5. 读懂一份 trace

![一份 ANR trace 的结构](/images/android-anr-analysis/07-trace-anatomy.webp)

trace 文件是纯文本，一个进程接一个进程地排。开头是 `Subject`（Reason 原文）和当事进程的内存头，然后每个进程一段，段与段之间由系统插入 `----- dumping pid: N at <uptime 毫秒> -----`。各行的作用图里标了序号，这里补几条读法。

**时间要对齐。** 每个进程的 dump 时刻不同，从 `dumping pid` 那行的 uptime 能看出先后，system_server 往往比当事进程晚一两秒。拿 trace 里的栈去对日志时间戳，以这一行为准。

**线程块的第一行信息量最大。**

```text
"main" prio=5 tid=1 Sleeping
```

依次是线程名、优先级、线程编号和状态。状态常见的有 `Sleeping`（在等，锁、条件变量、消息队列都算）、`Blocked`、`Waiting`、`Native`（在 JNI 或系统调用里）、`Runnable`（正在跑，或者在就绪队列里）。下面几行里 `sysTid` 是内核看到的线程号，和 `ps`、`top` 里对得上。`schedstat` 的三个数依次是运行时间、在就绪队列里等待的时间、用掉的时间片数，前两个单位是纳秒；第二个数很大，说明这个线程一直在等 CPU。

**锁信息直接给出下一步。** ART 在栈帧下面列出和锁有关的信息：

```text
  - waiting to lock <0x0d1f4c34> (a com.example.shop.data.AccountStore) held by thread 14
```

等的是哪个对象，被哪个线程拿着，都写明了。这里的线程号对应另一个线程块第一行里的 `tid=`。翻到 `tid=14` 那一块，看它拿着锁在做什么，因果链就出来了。`- locked <0x…> (a …)` 表示这一帧持有某个锁；`- waiting on <0x…> (a java.lang.Object)` 是在等条件变量。

**以 `kernel:` 开头的行**是内核栈，只在线程陷入系统调用时出现。`binder_ioctl`、`futex_wait`、写盘相关的几个名字出现的频率最高，分别对应 Binder 调用、等锁、文件 IO。

**内存头在最前面。** 文件开头的 `RssHwmKb`、`RssKb`、`VmSwapKb` 是当事进程的常驻内存峰值、当前值和换出量。这几个数很大的时候，ANR 可能和内存压力有关，第 5.1 节的第六类会用到。

### 5.1 主线程停在哪里，下一步去哪

![主线程停在哪里，下一步去哪](/images/android-anr-analysis/08-main-thread.webp)

把常见的主线程形态归成六类之后，拿到一份 trace 通常一两分钟就能分出方向。图里每一类的左边是栈上的样子，右边是读法和下一步。

前四类都直接指向一个具体的等待对象或耗时代码，顺着走就行。第 6 节里有它们的完整例子。要展开说的是后两类。

**空闲。** 主线程停在 `nativePollOnce`，状态 `Sleeping`。它说明 dump 这一刻主线程没事可做，仅此而已。结合 Reason 有三种可能：卡住的阶段已经过去（开头那张工单就是这种）；Reason 是广播，而问题出在一个 `goAsync()` 之后忘了 finish 的后台任务上；或者主线程根本没轮到执行那个回调，它前面压着的别的消息在 dump 时已经跑完了。三种可能光看主线程都分不出来，要去看别的线程和 Reason 对应的时间窗。

**没抢到 CPU。** 状态是 `Runnable`，栈顶是再普通不过的代码，`schedstat` 里的等待时间却很长。线程是就绪的，只是整机的 CPU 被别人占着。去主日志的 `CPU usage` 段看是谁，再确认 `Frozen` 那一行：进程如果被冻结过，解冻需要时间，这段时间同样算在期限内。这一类的根因常常不在应用自己的代码里，但低端机上它真实存在，而且会成片出现。

### 5.2 别的线程怎么看

主线程只是入口，多数 ANR 的答案在别的线程上。看其他线程块的时候，有三个名字规律可以用。

名字以 `binder:` 开头、后面跟着进程号和编号的，是 Binder 线程池里的线程，它们在处理别的进程发来的调用。Reason 是 Provider 的 ANR，要看的就是宿主进程里这些线程在做什么。反过来，自己的进程作为调用方发出的同步 Binder 调用，跑在发起调用的那个线程上，主线程发起的就在主线程上，栈顶是 `BinderProxy.transact`。

名字里带 `OkHttp`、`Retrofit`、`pool-` 这类字样的，一般是应用自己或者三方库开的线程池。等锁的 ANR 里，拿着锁的那个线程多半在这里。

还有几种线程不用细看：`RenderThread`、`HeapTaskDaemon` 一类的框架线程，除非它们恰好出现在锁信息里，否则和业务无关。

线程数量本身也是个线索。一个进程有几百个线程，光是调度开销就不小，这类进程的 ANR 常常带着“没抢到 CPU”的特征。

### 5.3 trace 里的时间差

栈是某一时刻的快照，而这个时刻永远晚于卡住的时刻。三个时间点要分清：卡住开始、期限到期（判定）、dump 完成。输入 ANR 里前两个相差 5 秒，第三个还要再晚几百毫秒到几秒。

Android 14 起对当事进程做了预 dump，第一个进程那一段和判定时刻靠得很近。其余进程是在之后的完整 dump 里依次抓的，彼此之间差一两秒。Android 13 及更早的版本没有预 dump，所有进程都在完整 dump 里依次抓。

时间差带来的后果是：栈上看到的，可能是卡住结束之后主线程接着做的下一件事。主线程空闲、或者停在一个明显不会耗时的位置时，就要想到这一层，回到日志里去看判定之前那段时间发生了什么。开头那张工单的转机就在这里，第 6.7 节会回到它。

## 6. 分析流程

![分析流程：从一份现场到一条结论](/images/android-anr-analysis/09-flow.webp)

六个步骤，每一步都有一个产出。拿不到产出就停在那一步补材料，往下走只是浪费时间。

**第一步，收现场。** 第 4 节的五处痕迹，能拿到的都拿到。最要紧的是 trace 和前后各一分钟的日志。ANR 现场不会等人，测试同学复现一次之后要立刻把 logcat 存下来，否则缓冲区很快会被冲掉。

**第二步，读 Reason，定时间窗。** Reason 说明哪个定时器先到点、等了多久。按类型找到起点，终点就是 dump 的时刻。这一步的产出是一段有起有止的时间和被点名的进程。没有这一步，后面读出来的栈都是在猜。

Reason 里的关键字可以直接对号：

表：Reason 关键字与类型对照

| Reason 里的关键字 | 类型 | 时间窗从哪里开始 | 先看什么 |
|---|---|---|---|
| `is not responding. Waited` | 输入，窗口无响应 | 事件送达这条连接 | 主线程 |
| `does not have a focused window` | 输入，没有焦点窗口 | 应用获得焦点 | 页面为什么还没有可用的窗口 |
| `executing service` | Service 回调 | 回调派发 | 主线程 |
| `did not then call Service.startForeground()` | 前台服务启动 | 启动命令派给应用 | `startForeground()` 为什么没执行到 |
| `Broadcast of Intent` | 广播 | 派发给这个接收器 | 主线程，或拿着 `PendingResult` 的线程 |
| `ContentProvider not responding` | Provider 调用 | 调用方发起这次调用 | 宿主进程的 Binder 线程 |
| `failed to complete startup` | 进程启动 | `bindApplication` 下发 | 主线程，Provider 和 Application 的初始化 |

**第三步，找当事线程。** 输入、Service、进程启动三类，当事线程是主线程。广播可能是主线程，也可能是拿着 `PendingResult` 的那个线程。Provider 要去宿主进程看 Binder 线程。找到之后把栈归到 5.1 的六类里。

**第四步，补上下文。** 对端进程和 system_server 那几段，主日志的 CPU 占用，`Frozen` 标记，以及时间窗内的业务日志。DropBox 那份报告里还多一段内存压力的快照，怀疑设备整体卡顿时可以看。

**第五步，复现与验证。** 能稳定重现的，用 Perfetto 抓一条系统级的 trace，几个分类一起开：

```bash
adb shell perfetto -o /data/misc/perfetto-traces/anr.pftrace \
  -t 30s sched freq idle am wm gfx view binder_driver
```

不能重现的，至少要找到一条能佐证解释的业务日志。

**第六步，修复并守住。** 见第 7 节。

下面用七个例子把第三步到第五步走一遍。片段都做了删减，包名沿用 `com.example.shop`。

### 6.1 等锁：一次 Service 超时

```text
Reason: executing service com.example.shop/.SyncService, waited 20003ms

"main" prio=5 tid=1 Blocked
  at com.example.shop.data.AccountStore.syncAll(AccountStore.java:88)
  - waiting to lock <0x0d1f4c34> (a com.example.shop.data.AccountStore) held by thread 14
  at com.example.shop.sync.SyncService.onStartCommand(SyncService.java:41)
  at android.app.ActivityThread.handleServiceArgs(ActivityThread.java:5150)
  at android.os.Looper.loop(Looper.java:223)

"SyncWorker" prio=5 tid=14 Native
  at java.net.SocketInputStream.socketRead0(Native Method)
  at com.example.shop.net.RetryInterceptor.intercept(RetryInterceptor.kt:71)
  at com.example.shop.data.AccountStore.refreshRemote(AccountStore.java:120)
  - locked <0x0d1f4c34> (a com.example.shop.data.AccountStore)
  at com.example.shop.SyncWorker.run(SyncWorker.java:44)
```

主线程进 `syncAll()` 要拿 `AccountStore` 的锁，锁在 `SyncWorker` 手里，而它拿着锁在做网络请求。两块栈拼在一起，因果就完整了。修法不是给哪一边加超时，而是别让网络请求发生在锁里面：锁内只做内存读写，请求挪到锁外面，拿到结果之后再进锁更新。

### 6.2 广播 Reason，主线程却空闲

```text
Reason: Broadcast of Intent { act=com.example.shop.ACTION_REFRESH flg=0x14 cmp=com.example.shop/.RefreshReceiver }

"main" prio=5 tid=1 Sleeping
  at android.os.MessageQueue.nativePollOnce(Native Method)
  at android.os.Looper.loop(Looper.java:223)

"pool-3-thread-2" prio=5 tid=19 Native
  at java.net.SocketInputStream.socketRead0(Native Method)
  at com.example.shop.net.Api.call(Api.kt:118)
  at com.example.shop.sync.RefreshTask.run(RefreshTask.java:74)
  at java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1145)
```

这是一个有序广播，接收器在 `onReceive()` 里调了 `goAsync()`，把 `PendingResult` 交给线程池去刷新。`RefreshTask` 里的网络请求没有超时，`finish()` 写在请求之后，于是永远执行不到。翻到代码里，`finish()` 就在 `run()` 的最后一行。

修法是把 `finish()` 放进 `finally`，再给请求加上超时：

```kotlin
override fun onReceive(context: Context, intent: Intent) {
    val pending = goAsync()
    executor.execute {
        try {
            Api.refresh(timeout = 8.seconds)
        } finally {
            pending.finish()
        }
    }
}
```

还可以多想一步：接收器到底需不需要等网络结果。大多数情况下，`goAsync()` 可以换成“收到广播就交给 WorkManager，立即返回”。

### 6.3 等对端：一次跨进程的输入 ANR

```text
Reason: com.example.shop/com.example.shop.MainActivity (server) is not responding.
       Waited 5003ms for MotionEvent(action=ACTION_DOWN, deviceId=3, source=0x1002)

"main" prio=5 tid=1 Native
  at android.os.BinderProxy.transact(Native Method)
  at android.content.IContentProvider$Stub$Proxy.query(IContentProvider.java:...)
  at android.content.ContentResolver.query(ContentResolver.java:...)
  at com.example.shop.feed.FeedRepository.load(FeedRepository.kt:57)
  at com.example.shop.feed.FeedFragment.onViewCreated(FeedFragment.kt:88)
```

点击触发了页面刷新，刷新在主线程上同步查询了一个 Provider。这个 Provider 属于哪个进程，从 trace 的其他段里找。翻到那个进程，看名字以 `binder:` 开头的线程：

```text
Cmd line: com.example.partner

"binder:14021_2" prio=5 tid=8 Native
  at android.database.sqlite.SQLiteQuery.nativeFillWindow(Native Method)
  at android.database.sqlite.SQLiteQuery.fillWindow(SQLiteQuery.java:...)
  at android.database.sqlite.SQLiteCursor.getCount(SQLiteCursor.java:...)
  at com.example.partner.Provider.query(Provider.java:131)
```

对端在自己的 Binder 线程里，对一个很大的表做了一次查询。两边能推动哪一边，就改哪一边：对端加索引是治本；推不动对端的话，客户端至少要把这次查询挪到后台线程，页面先显示缓存。

### 6.4 Reason 说的是广播，问题在上一条消息

```text
Reason: Broadcast of Intent { act=android.intent.action.LOCALE_CHANGED flg=0x10400010 cmp=com.example.shop/.LocaleReceiver }

"main" prio=5 tid=1 Runnable
  at org.json.JSONTokener.readObject(JSONTokener.java:...)
  at org.json.JSONObject.<init>(JSONObject.java:...)
  at com.example.shop.config.ConfigParser.parse(ConfigParser.kt:66)
  at com.example.shop.config.ConfigManager.apply(ConfigManager.java:212)
  at com.example.shop.SplashActivity.onCreate(SplashActivity.java:92)
```

一个静态注册的接收器负责处理语言切换，它要回到主线程上执行。主线程此刻正在启动页的 `onCreate()` 里解析一份很大的配置 JSON，广播的消息排在后面。先到期的定时器是广播的，根因却是启动时的配置解析。

这种情况要靠时间窗来区分：Reason 对应的派发时刻到 dump 时刻之间，主线程一直停在另一件事上。要修的是 `ConfigManager`，和接收器无关。

### 6.5 Provider：Binder 线程在等主线程

```text
Subject: ContentProvider not responding
Cmd line: com.example.shop:search

"binder:12977_3" prio=5 tid=9 Blocked
  at com.example.shop.search.SearchProvider.query(SearchProvider.kt:74)
  - waiting to lock <0x0c21b7a8> (a com.example.shop.search.Index) held by thread 1
  at android.content.ContentProvider$Transport.query(ContentProvider.java:...)
  at android.content.ContentProviderNative.onTransact(ContentProviderNative.java:...)

"main" prio=5 tid=1 Runnable
  at com.example.shop.search.Index.build(Index.kt:132)
  at com.example.shop.search.SearchProvider.onCreate(SearchProvider.kt:51)
  at android.app.ActivityThread.installProvider(ActivityThread.java:...)
```

`search` 进程里的 Provider 在 `onCreate()` 里同步建一份全量索引，建索引期间一直拿着 `Index` 的锁。调用方（一个打开了无响应检测的系统组件）这时发来一次查询，Binder 线程拿不到锁，调用超时，调用方把 `com.example.shop:search` 报了上去。

修法在宿主这一边：`onCreate()` 只做最少的初始化，立刻返回，索引放到后台线程去建；查询进来时索引还没就绪，就返回空结果并带上一个“正在建索引”的标记，由调用方稍后重试。

### 6.6 前台服务：`startForeground()` 没执行到

```text
Reason: Context.startForegroundService() did not then call Service.startForeground(): ServiceRecord{... com.example.shop/.UploadService}

"main" prio=5 tid=1 Sleeping
  at android.os.MessageQueue.nativePollOnce(Native Method)
```

主线程空闲，Service 也早就跑完返回了，问题在代码的顺序上：

```kotlin
override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    if (!hasPermission()) return START_NOT_STICKY     // 提前返回，startForeground() 没执行到
    startForeground(NOTIFICATION_ID, buildNotification())
    upload(intent)
    return START_NOT_STICKY
}
```

用 `startForegroundService()` 拉起的服务，不管后面做不做，都要先调一次 `startForeground()`。确实不需要继续的话，调完之后再 `stopSelf()`。

### 6.7 主线程空闲，一切看起来都正常

回到开头的那张工单。Reason 是输入超时，`Waited 5002ms`；主线程空闲；应用自己的线程里没有值得怀疑的地方。

先确认时间窗。事件在 T 时刻送达，T+5.002 秒判定，dump 在判定之后几百毫秒完成。主线程在 dump 时空闲，说明它在这 5 秒里的某一刻被占住过，而且在 dump 之前已经恢复。能同时满足这两点的只有一种情形：处理这次点击的代码本身花了 5 秒多，然后正常返回了。

去日志里找证据。应用在 `submitOrder()` 的前后各打了一行日志，两行之间隔了 5.4 秒，中间是一次失败后自动重试的同步请求。再往前，logcat 里还有一行 `Choreographer: Skipped 324 frames!`，帧数乘以 16 毫秒，和 5 秒出头对得上。

```kotlin
// 请求离开主线程，请求期间按钮不可点
binding.submit.setOnClickListener {
    binding.submit.isEnabled = false
    lifecycleScope.launch {
        val result = withContext(Dispatchers.IO) { orderApi.submit(order) }
        render(result)
        binding.submit.isEnabled = true
    }
}
```

那张工单如果能重来一遍，走的就是 5.1 里“空闲”那一格的后半句：主线程的栈给不出答案时，把 Reason 里的毫秒数当成时间窗的长度，去日志里找这段时间里跑过的代码。

## 7. 修的时候

按类来修，比按“ANR”这个笼统的词去优化有效得多。

**等锁。** 原则是锁里面不做任何可能阻塞的事，IO、网络、Binder 调用都算。主线程和后台线程共享的锁尤其要小心：后台线程持锁三秒对它自己无所谓，对主线程就是一次 ANR。能用不可变对象加“整体替换”来代替加锁修改的场景，尽量替换。

**等别人兑现。** 主线程上的 `Future.get()`、`CountDownLatch.await()`、协程的 `runBlocking {}`，都要先回答一个问题：对方会不会需要主线程才能完成。Kotlin 协程里用 `runBlocking` 等一个 `Dispatchers.Main` 上的任务，是一定等不到的写法。

**等对端。** 主线程上的一切同步 Binder 调用都当作不可控的耗时操作对待，包括 ContentProvider 查询、不少 `PackageManager` 的接口、别的进程提供的 AIDL 服务。能用异步 API 的换成异步，能缓存的先给缓存。

**自己在忙。** 这一类最直白，栈顶就是答案。主线程上的 JSON 解析、图片解码、数据库查询、大的布局 inflate，挪到后台，结果通过回调回主线程。

**`apply()` 的积压。** `SharedPreferences.apply()` 调了之后并没有完。它把写盘排进队列，而 `onStartCommand()`、`onDestroy()` 和 Activity 的 `onPause()`、`onStop()` 在返回前会同步等这个队列清空。配置文件很大、或者一次写了很多项的应用，这几个回调就会莫名变慢。数据量小的问题不大，数据量上来了换 DataStore 或者别的存储方案。

**`goAsync()`。** 三条规矩：`finish()` 写在 `finally` 里；后台任务自带超时；接收器实在不需要结果时，把工作转交给 WorkManager 之类的调度器，`onReceive()` 立即返回。

**前台服务。** `startForeground()` 写在 `onStartCommand()` 的第一行，在任何可能提前返回的判断之前。

**Provider。** `onCreate()` 只做最少的初始化；耗时的数据准备放到后台，别让 Binder 线程在初始化期间等主线程手里的锁。

**启动期。** Android 15 起有 15 秒的进程启动期限，它覆盖所有 ContentProvider 的 `onCreate()` 和 `Application.onCreate()`。初始化仓库（比如 AndroidX Startup）里的每一项都问一句“首帧之前真的需要吗”，不需要的改成延迟初始化。

**没抢到 CPU。** 应用能做的有限：少开后台线程，避免自己的后台任务和自己的前台进程抢 CPU；在广播和 Service 回调里少做重活。系统层面的问题，比如后台应用成批启动、内存回收，要靠 Perfetto 的系统 trace 来证明，然后反馈给平台或厂商。

这些原则可以浓缩成一张代码评审用的清单，遇到主线程、Service、BroadcastReceiver、ContentProvider 的改动时过一遍：

表：和 ANR 相关的代码评审清单

| 检查项 | 为什么 |
|---|---|
| 主线程上有没有新出现的同步 Binder 调用 | 对端耗时不可控 |
| 主线程会不会去拿一把后台线程也在用的锁 | 持锁方一旦做 IO，主线程就跟着等 |
| `synchronized` 块里有没有 IO、网络、Binder | 同上，而且这种写法从栈上一眼能看出来 |
| 回调结束前调了多少次 `apply()`，文件有多大 | 回调结束前要同步等写盘 |
| `goAsync()` 的 `finish()` 是否在 `finally` 里，任务有没有超时 | `finish()` 不执行，计时就不会停 |
| `startForegroundService()` 拉起的服务，有没有先调 `startForeground()` | 30 秒的期限只认这一句 |
| 新加的 ContentProvider 的 `onCreate()` 做了什么 | 它在进程启动期限之内，也可能挡住 Binder 线程 |
| 新加的静态注册接收器在 `onReceive()` 里做了什么 | 静态注册的接收器都在计时 |
| 主线程上新出现的 JSON 解析、图片解码、数据库查询 | 自己在忙的那一类 |

**可观测性。** debug 包打开 StrictMode，把主线程 IO 这类问题在开发阶段暴露出来：

```kotlin
if (BuildConfig.DEBUG) {
    StrictMode.setThreadPolicy(
        StrictMode.ThreadPolicy.Builder()
            .detectAll()
            .penaltyLog()      // 违规写进 logcat，开发阶段够用
            .build()
    )
}
```

线上用 `ApplicationExitInfo` 按 `Subject` 分桶统计 ANR 率，版本之间做对比。应用内的主线程卡顿监控可以继续留着，但要清楚它只看得见主线程，也拿不到系统的视角。

## 8. 几个常见误解

表：关于 ANR 的十个常见误解

| 说法 | 实际情况 | 详见 |
|---|---|---|
| 主线程卡 5 秒就一定 ANR | 只在有一个输入事件正等着被处理时成立；没有事件时卡多久都不触发输入 ANR | 2.1 |
| `startService()` 之后不 `stopSelf()` 会 ANR | 20 秒计的是一次回调从派发到返回，和之后是否停止无关 | 2.2 |
| `onCreate()` 慢会 ANR | AOSP 没有生命周期定时器；Android 15 起另有 15 秒的进程启动期限 | 2.6 |
| 发生 ANR 进程就没了 | 弹窗之后进程还活着，点“等待”就继续运行 | 3 |
| 后台进程不会 ANR | 会，只是不弹窗，直接被结束 | 3 |
| debug 包的输入超时是 60 秒 | 60 秒只给 instrumentation 和 wrapper；被调试器附加的进程不判 ANR | 2.7 |
| Provider 没按时发布会报 ANR | 会被直接结束进程；`ContentProvider not responding` 来自调用方打开的检测 | 2.5 |
| trace 里主线程空闲就说明没问题 | 只说明 dump 那一刻空闲，判定和 dump 之间有时间差 | 5.1、5.3 |
| 所有进程的栈是同一时刻的 | 各进程依次 dump，先后差一到两秒，整份有 20 秒预算 | 3 |
| DropBox 里没有就是没发生 | tag 可能被关，记录可能被限流 | 4 |

## 9. 回到那张工单

ANR 的分析，难点从来不在拿到栈之后，而在拿到栈之前：你得先知道这份栈对应的是哪一段时间。Reason 把这段时间的终点和长度都写明了，顺着它把起点找出来，栈才有意义。

所以下一次再遇到一份“主线程空闲”的 trace，先别急着关工单。把 Reason 里的毫秒数当成时间窗的长度，到这段时间里的日志和别的线程里去找。开头那张工单，就是这样找到那次 5 秒多的同步请求的。

## 附：版本速查

表：已核对的版本差异

| 行为 | 版本 | 说明 |
|---|---|---|
| 输入 Reason 的写法 `… is not responding. Waited …` | 11 到 16 相同 | 六个版本的 `InputDispatcher.cpp` 都看过；更早的没有核对 |
| `startForegroundService()` 的超时走 ANR 路径，30 秒加 10 秒 | Android 13 起 | 11、12 里只有崩溃路径 |
| 对当事进程做预 dump | Android 14 起 | 12、13 没有 |
| 广播的软、硬两段超时 | Android 14 起 | 13 没有；15 起改由 `AnrTimer` 计时 |
| 进程启动期限，15 秒 | Android 15 起 | 14 没有，15 和 16 有 |
| 判定之后的主体流程 | 15 和 16 相同 | 对应的源文件只差几行 import 和内存头的内部表示 |

表里“X 起”的意思是：我在 X 这个版本的源码里见到了对应的代码，在它的前一个版本里没有见到。没有列在表里的行为，只按 Android 15 核对过。

## 参考资料

源码链接都指向 `android-15.0.0_r1` 这个 tag，行号以它为准。

- [ActiveServices.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/services/core/java/com/android/server/am/ActiveServices.java)：Service 回调计时，`startForegroundService()` 的期限
- [BroadcastQueueModernImpl.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/services/core/java/com/android/server/am/BroadcastQueueModernImpl.java)：广播的派发与软、硬超时
- [ContentProviderHelper.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/services/core/java/com/android/server/am/ContentProviderHelper.java)：Provider 的发布期限和 `appNotRespondingViaProvider()`
- [ContentProviderClient.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/core/java/android/content/ContentProviderClient.java)：调用方的无响应检测 `setDetectNotResponding()`
- [AnrHelper.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/services/core/java/com/android/server/am/AnrHelper.java)：ANR 的汇合、去重和预 dump
- [ProcessErrorStateRecord.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/services/core/java/com/android/server/am/ProcessErrorStateRecord.java)：判定之后的主体流程，静默 ANR 的判断
- [StackTracesDumpHelper.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/services/core/java/com/android/server/am/StackTracesDumpHelper.java)：trace 文件的命名、顺序和 20 秒预算
- [ActivityManagerService.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/services/core/java/com/android/server/am/ActivityManagerService.java)：输入超时入口，进程启动期限，DropBox
- [TimeoutRecord.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/core/java/com/android/internal/os/TimeoutRecord.java)：各种 Reason 字符串的出处
- [ActivityThread.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/core/java/android/app/ActivityThread.java)：应用侧何时报告 Service 完成，`QueuedWork.waitToFinish()` 的调用点，等待 Provider 时的阻塞位置
- [ViewRootImpl.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/core/java/android/view/ViewRootImpl.java)：输入事件走完哪些阶段才算 finish
- [ContentResolver.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/core/java/android/content/ContentResolver.java)：Provider 的几个超时常量
- [ApplicationExitInfo.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/core/java/android/app/ApplicationExitInfo.java)：`REASON_ANR` 和 `getTraceInputStream()` 的说明
- [InputDispatcher.cpp](https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-15.0.0_r1/services/inputflinger/dispatcher/InputDispatcher.cpp) 与 [AnrTracker.h](https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-15.0.0_r1/services/inputflinger/dispatcher/AnrTracker.h)：输入超时的登记与判定
- [Android 官方：应用无响应（ANR）](https://developer.android.com/topic/performance/vitals/ANR)：官方的概览，和本文的口径有出入时以源码为准
