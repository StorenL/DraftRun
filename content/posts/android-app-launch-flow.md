---
title: 从图标到第一帧：Android 应用启动流程
dek: 应用启动常被压缩成一句「AMS 通过 socket 通知 Zygote fork 一个进程」。这句话没错，但它把横跨四个进程的十几次调用压成了一个名词。
excerpt: 一次冷启动要横跨 Launcher、system_server、Zygote、应用进程四个进程。按真实顺序走一遍，把每个交接点落到具体的类、日志和 trace 上，你才知道时间里哪一段是可以动的。
date: 2026-11-22
minutes: 40
tag: Android
hot: true
note: 文中数值除标注来源外，均为量级示意，不是任何具体设备的承诺值。
---

> 基准：Android 12 ~ 16（API 31 ~ 36），源码路径以 Android 15/16 为准。


![封面：从图标到第一帧](/images/android-app-launch-flow/00-cover.webp)

早上八点四十，地铁十号线。一个人用拇指点开外卖 App，屏幕先亮起一块品牌色，中间是应用图标，几百毫秒后内容滑出来。他大概率只会觉得“今天没卡”。

与此同时，项目的 CI 上多了一行日志：

```text
ActivityTaskManager: Displayed com.example/.MainActivity: +1s842ms
```

1.8 秒。谁拿走的？

这两个场景说的是同一件事，只是刻度不同。应用启动被讲过很多遍，但大多数讲法停在“AMS 通过 socket 通知 Zygote fork 一个进程”这句话上。这句话没错，但它把横跨四个进程、十几次跨进程调用、以及 SurfaceFlinger 在屏幕上的那次合成，压缩成了一个名词。压缩之后，你很难知道时间去哪儿了，也很难判断一个优化方案到底在优化哪一段。

下面按一次真实冷启动的顺序走，把每个交接点落到具体的类、日志和 trace 上。版本差异我会标出来；没有特别说明时，以 Android 15/16 的 AOSP 为准。

## 1. 先定义“启动”：三个时刻，和几种容易混的计时口径

“启动用了多久”这句话，在 Android 里能给出好几种不同的答案，取决于你从哪一秒开始算、又在哪一秒结束。

对用户来说，启动是三个连续的瞬间：手指抬起后屏幕上出现反馈（通常是启动画面）、第一帧内容出现、以及他终于能滑动或点击。对系统来说，启动是从 `system_server` 收到 `startActivity` 开始，到窗口完成第一帧绘制为止。对测试工具来说，是 `am start -W` 打印的那个 `TotalTime`。对性能工程师来说，是从 `bindApplication` 到 `Choreographer` 第一帧的整段 trace。

这几个数字不是同一个东西。混着用，就会得到“优化了 300ms 但用户没感觉”的结论。

![冷启动、温启动、热启动与几种计时口径](/images/android-app-launch-flow/01-start-types.webp)

### 冷、温、热：先确认进程在不在

Google 给的定义很干脆：

- **冷启动**：应用进程不存在，系统需要创建新进程。应用启动的绝大部分成本发生在这里。
- **温启动**：进程还在，但 Activity 已经被销毁（比如用户按返回键退出了界面，进程被系统留在后台）。再次进入时不需要 fork 进程，也不需要重新创建 Application，但 Activity 需要重新走 `onCreate`。
- **热启动**：进程和 Activity 都还在内存里，只是被切到后台。回到前台时通常只需要 `onRestart → onStart → onResume`，窗口内容还在。

听起来边界清楚，实际工作里最容易错的就是这一步。用 `am force-stop` 杀掉进程再启动，测到的是冷启动；按 Home 键退出再点图标，可能测到的是热启动；用 `am kill` 杀进程，杀得掉是冷启动；杀不掉（前台、可见或有前台服务）往往还是热启动。温启动则是进程还在、Activity 已经销毁。三种启动的耗时能差一个数量级，把它们混在一张报表里算平均值，得到的数字没有意义。

Android 15 起，应用自己也能拿到这次启动的系统视角信息：`ActivityManager` 的 `ApplicationStartInfo`（API 35）能给出启动类型、原因和阶段；Android 16 又加了 `getStartComponent()`，能区分触发启动的是 Activity、Service、Broadcast 还是 ContentProvider。对线上监控来说这很有用——终于不用靠猜“这个用户到底是冷启动还是热启动”。具体字段和用法放在第 8 节展开。

### 几个常用口径

把常见的指标放在一张表里，差异会更直观：

| 指标 | 起点 | 终点 | 典型用途 | 容易踩的坑 |
|---|---|---|---|---|
| `Displayed`（logcat） | ATMS 受理 `startActivity` | 首帧绘制完成 | 系统侧观测、线上粗粒度 | 不含 Launcher 点击处理；含 splash；和 `TotalTime` 是同一区间的两种采法 |
| `am start -W` 的 `TotalTime` | ATMS 受理（`notifyActivityLaunching`） | 首帧绘制完成 | 本地快速对比 | 与 `Displayed` 同区间；`WaitTime` 还含 shell 排队 |
| Perfetto 全链路 | `bindApplication`（可再往前追到 ATMS） | 首帧（可再往后追到合成） | 本地定位、方法级归因 | 要手动埋点才能细到业务方法 |
| TTID | 点击（埋点定义） | 首帧 | 用户视角的“看到内容” | 埋点位置不同，数值不可横向比较 |
| TTFD | 点击 | 内容真正就绪（`reportFullyDrawn`） | 用户视角的“能用” | 要 App 主动上报，否则等于 TTID |

`Displayed` 这一行值得多说一句。它的起点在 `system_server` 收到请求之后，所以 Launcher 从触摸到发出 Binder 调用的那段时间不在里面；它的终点是窗口完成第一帧绘制，而不是 `onResume` 返回。后面我们会看到，这两者之间可能隔着好几个 vsync。

## 2. 点击落下之后：Launcher 与 system_server

用户在 Launcher 上点图标，Launcher 收到的是一个普通的 `View` 点击事件。`OnClickListener` 里通常只有一行 `startActivity(intent)`，但这一行会依次穿过：

```text
Launcher 进程
  View.performClick()
    → OnClickListener.onClick()
      → Activity.startActivity(intent)
        → Activity.startActivityForResult(intent, -1)
          → Instrumentation.execStartActivity(...)
            → ActivityTaskManager.getService().startActivity(...)   // Binder proxy
```

`IActivityTaskManager` 是 Binder 接口，实现在 `system_server` 的 `ActivityTaskManagerService`（下文简称 ATMS）里。Android 10 起，这个接口从 `IActivityManager` 里拆出来，服务端也从 `ActivityManagerService` 换成了 ATMS。调用从这里离开 Launcher 进程，进入系统进程。注意这是一次同步 Binder 调用：Launcher 的 UI 线程会停在 `execStartActivity` 上，直到 system_server 把请求处理完并返回。返回之后 Launcher 才继续跑自己的逻辑；它真正收到 `onPause` 要等 system_server 在窗口过渡里安排，不一定紧接着这次调用——用户先看到的通常是启动画面。

### ATMS 收到请求之后

ATMS 的入口方法会创建 `ActivityStarter`。`ActivityStarter.execute()` 一进来就先调用 `ActivityMetricsLogger.notifyActivityLaunching()` 记下启动起点（Perfetto 里从这一刻开始出现 `launching: <pkg>` 的 async slice），然后才解析 Intent、做后续判断：

1. 检查调用方权限、目标 Activity 是否 exported、是否被 `android:exported` 或权限拦住；
2. 通过 `PackageManager` 解析 Intent（在同进程内调用 PackageManagerService），拿到 `ActivityInfo`；
3. 根据 launch mode、Intent flags、`ActivityOptions` 决定复用哪个 Task、是否需要新建 Task；
4. 找到或创建 `ActivityRecord`，把它挂到对应的 Task 上；
5. 如果目标 Activity 需要显示，安排启动窗口（Android 12 之后就是 SplashScreen）；
6. 判断目标进程是否存在：存在就复用，不存在就创建；
7. 让前一个 Activity 暂停（如果它是不透明的、会被完全覆盖）；
8. 等新进程 attach 回来，再把 `LaunchActivityItem`、`ResumeActivityItem` 发给它。

这一串动作大部分跑在 `system_server` 里，只有少数会跨进程。它也是“启动时间”里最容易被忽略的一段：用户点下去到系统决定要启动你的 App，可能已经过去几十毫秒，这段时间在你的 App 里没有任何代码在跑。

`ActivityMetricsLogger` 在这里记下了 `Displayed` 的起点。也就是说，logcat 里那行 `Displayed +1s842ms` 的计时，从 ATMS 受理请求开始，到窗口首帧绘制完成结束。Launcher 从触摸到发起 Binder 调用的那一段、以及请求在 Binder 队列里排队的时间，都不在里面；而 ATMS 受理之后等前一个 Activity pause、创建进程、跑 `onCreate`、画首帧，全都算在里面。做线上监控时，这个差异会影响你对“用户等待”的归因。

### 启动窗口：那几百毫秒里用户看到的东西

进程还没创建、Application 还没跑，屏幕上不能是一片黑。Android 12 之前，这件事由 `WindowManager` 的 Starting Window 负责，通常表现为 `windowBackground` 里配置的那张图——很多 App 会放一张和启动页视觉一致的 drawable，制造“无缝”的错觉。Android 12 起，系统把这条路统一成了 `SplashScreen` API：窗口由系统创建，图标、背景色、动画都从主题里读，App 只能通过 `SplashScreen` 接口控制退场动画和保持条件。系统侧的创建/移除由 `WindowManager` 的 `StartingSurfaceController` 调度，真正把 splash 画出来的是 SystemUI 的 `SplashscreenContentDrawer`。

这带来两个直接影响。第一，启动画面不再是“你自己的一个 Activity”，而是一个系统窗口，它的生命周期和首帧绑定；第二，用户看到的“启动速度”在很大程度上变成了“splash 退出得利不利索”。后面讲首帧的时候会展开。

### system_server 决定：要不要 fork

不是每次启动都要创建进程。冷/温启动的分水岭在 `ActivityTaskSupervisor.startSpecificActivity()` 里：按 `(processName, uid)` 找到 `WindowProcessController`，如果 `wpc != null && wpc.hasThread()`，说明进程还活着，直接走 `realStartActivityLocked()` 复用；否则标记为需要冷启动，调用 `ATMS.startProcessAsync()`。

`startProcessAsync()` 并不会在 Binder 线程上直接 fork，而是 post 到 system_server 的 handler 再调 `ActivityManagerInternal.startProcess()`（trace 里能看到 `dispatchingStartProcess:<processName>`）。这既是为了复用 system_server 的调度上下文，也是为了避免持有 ATMS 锁的同时调用 AMS 造成死锁。也就是说，`startActivity` 的 Binder 调用往往很快就返回了；handler 线程只是发起进程创建请求，真正的 fork 在 zygote 里完成。

需要提醒的是，`system_server` 的主线程是单线程的。所有应用启动、Activity 切换、窗口事务都要在这条线程上排队（部分工作会分发给 `android.ui`、`android.display` 等线程，但关键决策仍然集中）。这意味着**你的 App 启动慢，有时不是因为你的代码慢，而是因为 system_server 正在忙**——比如另一个应用在频繁启动、系统在安装更新、或者某个系统服务持有了锁。线上大样本里，这类“系统侧抖动”会以长尾的形式出现，值得在 trace 里单独看一眼。

![一次点击穿过四个进程](/images/android-app-launch-flow/02-chain.webp)

> 图里把 Launcher、system_server、Zygote、App 四个进程画在了一起，①-⑦ 是链路里的关键交接点。实线是真实的跨进程调用，虚线是视觉上的叠放关系。

到这里，用户的手指已经抬起了一段时间，屏幕上出现的是系统画的启动画面，而 App 的进程可能还没被创建。下一站是 Zygote。

## 3. Zygote：一次 fork，复制一个世界

`system_server` 决定新建进程之后，调用 `Process.start()`。这个方法的实现在 `frameworks/base/core/java/android/os/Process.java` 里，真正干活的是 `ZygoteProcess`：它打开 `/dev/socket/zygote`（32 位进程走 `zygote_secondary`），把一长串参数按顺序写过去——`--runtime-args`、`--setuid`、`--setgid`、`--target-sdk-version`、`--nice-name`、`--is-top-app`、`--disabled-compat-changes`，最后是要执行的入口类 `android.app.ActivityThread` 和 `seq=<n>`。可选参数里有个 `--invoke-with`，zygote 子进程见到它会走 `WrapperInit.execApplication`，而不是普通的 `ZygoteInit` 路径；抓启动问题时遇到 wrapper 模式，要意识到轨迹会不一样。zygote 那边由 `ZygoteServer.runSelectLoop()` 循环读取 socket，每读到一条命令就 fork 一次。

zygote 进程本身是 `init.rc` 里声明的：

```ini
service zygote /system/bin/app_process -Xzygote /system/bin --zygote --start-system-server
    class main
    socket zygote stream 660 root system
    onrestart write /sys/android_power/request_state wake
```

`app_process` 是 `frameworks/base/cmds/app_process/app_main.cpp` 编译出来的小二进制，它的工作非常简单：解析参数，启动 ART 虚拟机，然后调用 `ZygoteInit.main()`。Zygote 的本体是 `frameworks/base/core/java/com/android/internal/os/ZygoteInit.java`，它做两件大事：

1. **预加载**：读取 `/system/etc/preloaded-classes`，把上万条常用类（不同 Android 版本与 OEM 配置差异很大，近几个版本已接近两万条）提前加载进内存；预加载主题资源、常用 native 库、文本资源；初始化 ART 运行时。这一步很贵，但只发生一次。
2. **进入循环**：启动 `ZygoteServer`，监听 zygote socket，等待 `system_server` 的命令。

预加载的东西是所有 App 共享的。共享的方式是 fork 加写时复制（Copy-on-Write）：fork 时内核只复制页表，父子进程指向同一批物理页，并把这些页标记为只读；任何一方尝试写入，内核才为它复制一份新的页。于是每个 App 进程都“天生”拥有一份已经加载好的类、资源和运行时状态，不需要重新加载一遍，也不需要重新占用一份物理内存。

![Zygote fork 与写时复制](/images/android-app-launch-flow/03-zygote.webp)

### fork 到底花了多少时间

很多文章把 fork 描述成“几乎零成本”，这不太准确。fork 本身确实很快（通常在毫秒级），但 fork 之后子进程不是立刻就能跑：它要建立自己的内存映射、处理首次访问共享页时的缺页异常、初始化 Binder、切换 SELinux 域、设置 uid/gid 和资源限制。这些工作在 trace 里表现为一段不算短的过渡，而不是一条“瞬间完成”的竖线。

更隐蔽的成本在缺页。zygote 预加载的类页是干净的共享页，子进程读它们不需要拷贝；但子进程一旦写入，就会触发复制。App 在启动过程中会修改不少共享状态（类初始化、静态变量、资源缓存），这些写操作会产生大量私有的脏页。物理内存因此增长，缺页也会消耗 CPU。系统侧的 `PinnerService` 会把启动关键的文件页钉在内存里，减少缺页；zygote 预加载裁剪则是在“共享得多”和“fork 更轻”之间做取舍。两者目标不同，但都会影响启动表现。

还有一个容易被忽略的细节：fork 出来的子进程只保留一个线程。zygote 在 fork 时是单线程的（`forkAndSpecialize` 只会让调用线程存活），ART 会重置线程池和锁状态。但 Binder 线程池并不是“以后再说”——子进程在 `ZygoteInit.zygoteInit()` 里调用 `nativeZygoteInit()`，这时就启动了 Binder 线程池，首线程 `binder:<pid>_1` 常驻，其余线程按需增长（默认上限 15）。所以在 `ActivityThread.main()` 之前，这个进程已经能接收 Binder 调用了。

从 Android 10（API 29）开始，manifest 里可以声明 `android:zygotePreloadName`，配合 `ZygotePreload` 接口做应用自定义预加载。它用的不是系统主 zygote，而是应用自己的 app zygote（通常配合 `android:useAppZygote` 给隔离服务用）：`doPreload()` 在这条私有 zygote 里执行，继承者只有声明了 app zygote 的隔离进程。普通应用的冷启动主进程不走这条路径，所以它算不上“启动优化银弹”，更多是给系统级/预装应用准备的能力。

### zygote 之后：从 C++ 到 Java 的第一跳

fork 完成后，子进程沿着 `ZygoteConnection.handleChildProc()` 继续往下走：`ZygoteInit.zygoteInit()` → `RuntimeInit.applicationInit()` → `findStaticMain("android.app.ActivityThread")`。最后这一步通过反射找到 `ActivityThread.main()`，调用它，然后进入 Java 世界的主循环。

`ActivityThread.main()` 短得几乎不像一个进程入口：

```java
public static void main(String[] args) {
    AndroidOs.install();                 // 安装 syscall 拦截
    Looper.prepareMainLooper();
    long startSeq = parseStartSeq(args);  // 进程参数里的 "seq=N"
    ActivityThread thread = new ActivityThread();
    thread.attach(false, startSeq);      // Binder 向 AMS 报到
    if (sMainThreadHandler == null) {
        sMainThreadHandler = thread.getHandler();
    }
    Looper.loop();                       // 主线程进入消息循环
}
```

（上面是删掉无关代码后的主干。`startSeq` 实际由 AMS 在启动进程时通过参数传入，用于对齐“这次是哪一次进程启动”。）

`prepareMainLooper()` 创建主线程的 `Looper` 和 `MessageQueue`；`attach()` 通过 Binder 向 `system_server` 报到；最后 `Looper.loop()` 让主线程进入无限循环，等待消息。注意在这个时刻，App 的业务代码一行都还没跑，`Application` 也还不存在。

## 4. 新进程醒来：attach、bind 与 Application

`ActivityThread.attach()` 做的最重要的一件事，是把自己的 `ApplicationThread` 对象（一个 Binder 服务端）交给 `system_server`：

```java
IActivityManager mgr = ActivityManager.getService();
mgr.attachApplication(mAppThread, startSeq);
```

`ApplicationThread` 是 App 进程暴露给 system_server 的回调接口。它的方法（`bindApplication`、`scheduleTransaction`、`scheduleLowMemory` 等）会在 App 进程的 Binder 线程上执行，然后把任务 post 到主线程的 `Handler`。这是整条链路里最容易混淆的一对名字。另外，`ActivityThread` 虽然叫 Thread，但它不是 `Thread` 的子类，只是“主线程上的管理者”：

- `ActivityThread` 是主线程上的对象，负责执行生命周期；
- `ApplicationThread` 是 Binder 回调接口，负责接收 system_server 的指令，它本身跑在 Binder 线程上。

system_server 侧收到 `attachApplication` 后分成两段：先由 AMS 的 `attachApplicationLocked()` 登记进程、把 `bindApplication` 发回 App；随后 `finishAttachApplicationInner()` 调 `ATMS.attachApplication()`，让窗口系统找到这个进程的 top Activity，下发 `LaunchActivityItem`。所以顺序是：**先 bindApplication（Application.onCreate 之前），后 launch Activity**。

`bindApplication` 在 Android 15 上有 28 个参数，打包成 `AppBindData` 下发：`ApplicationInfo`、`ProviderInfoList`（注意不是 `List<ProviderInfo>`，这是大列表的 Parcel 优化）、`Instrumentation` 名称、`ProfilerInfo`、`Configuration`，以及 system_server 记下的 `startRequestedElapsedTime/Uptime`。这组时间戳很关键：App 侧在 `handleBindApplication` 开头就把它 `Process.setStartTimes()` 进去，后面 `ApplicationStartInfo` 才有机会把“请求启动”和“首帧”对齐。

App 主线程收到 `BIND_APPLICATION` 消息后进入 `handleBindApplication()`。它要做的事可以概括成四步：建上下文、建 Application、跑所有 ContentProvider、跑 `Application.onCreate`。（`bindApplication` 是 Binder 方法名，App 侧对应 `BIND_APPLICATION` 消息和 `handleBindApplication()`，后文不再区分。）

### 一个顺序问题：ContentProvider 比 Application.onCreate 更早

`handleBindApplication()` 的执行顺序值得单独记住，因为它和很多人的直觉不同：

```text
LoadedApk.makeApplicationInner()   // 早期版本叫 makeApplication()
  → new Application()
  → Application.attachBaseContext()
  → installContentProviders()
      → 每个 ContentProvider 的 attachInfo()（内部回调 onCreate()）
  → Application.onCreate()
```

也就是说，**ContentProvider 的 `onCreate` 先于 `Application.onCreate`**。很多库把初始化塞进 ContentProvider（Firebase、WorkManager、某些 SDK 的自动初始化），你以为自己在 `Application.onCreate` 里才“开始”工作，实际上那些 Provider 已经先跑了一轮。Jetpack 的 App Startup 库利用了同一个机制：它注册一个 `InitializationProvider`，把各个 `Initializer` 合并到这一次 Provider 创建里，从而省掉每个库各自注册 Provider 的开销。但它并没有让初始化变早或变晚——只是把多次 Provider 创建合并成一次。

`Application.attachBaseContext()` 也在 `Application.onCreate` 之前。如果你在 `attachBaseContext` 里做了重活（比如加载多 dex、初始化热修复、读取配置），这段时间同样算在启动里，而且它发生在 Provider 之前。

### Application.onCreate：最容易被塞满的地方

`Application.onCreate()` 是 App 业务初始化最集中的入口，也常常是启动性能问题最集中的地方。日志初始化、崩溃上报、图片库、网络库、推送、埋点、配置中心、数据库迁移……每一件事单独看都只要“几毫秒”，叠在一起就是几百毫秒。更糟的是，这些初始化大多在主线程上串行执行，任何一个同步 IO 或锁等待都会把整条启动链路卡住。

一条典型的冷启动 trace 里，框架侧的绑定（进程登记、资源、Instrumentation）只花了 40ms 左右；但 `Application.onCreate` 里一次 SharedPreferences 的全量读取加上两个 SDK 的同步初始化，让整个 `bindApplication` slice 拉到了 380ms。更麻烦的是，这段代码在旗舰机上只要 90ms，在低端机上却会被 IO 放大好几倍——同一份代码，不同的设备，完全不同的结论。

这也是为什么启动优化不能只看平均值：低端机和冷文件缓存下的长尾，才是用户抱怨的那部分。

![App 主线程上的启动消息](/images/android-app-launch-flow/04-app-thread.webp)

## 5. Activity 的生命周期，只是启动的一半

`bindApplication` 之后，system_server 会继续发来下一组事务（`ClientTransaction`），其中通常包含 `LaunchActivityItem` 和 `ResumeActivityItem`。这套“生命周期事务化”的机制从 Android 9（API 28）开始，取代了早期逐个 `H` 消息驱动的回调。它们通过 `ApplicationThread.scheduleTransaction()` 到达 App 的 Binder 线程，再被 post 到主线程，由 `TransactionExecutor` 依次执行。`TransactionExecutor` 除了执行 item，还会把 Activity 的 `LifecycleState` 沿目标路径补齐——所以有时候 `onStart` 并不是由一个单独的 item 触发，而是执行器在推进状态时补上的。

先执行 `LaunchActivityItem`，对应 `ActivityThread.handleLaunchActivity()`：

```text
handleLaunchActivity()
  → performLaunchActivity()
      → ClassLoader.loadClass(activityName)
      → Instrumentation.newActivity()
      → Activity.attach(baseContext, ...)       // 创建 PhoneWindow、设置主题
      → Instrumentation.callActivityOnCreate()
          → Activity.onCreate()
              → setContentView() → LayoutInflater.inflate()
      → Activity.onStart()
  → handleResumeActivity()
      → Activity.onResume()
      → WindowManager.addView(decorView, lp)
          → ViewRootImpl.setView()
```

在这条路径里，`attach()` 会创建 `PhoneWindow`、应用主题、把 `DecorView` 建好；`setContentView()` 触发布局文件解析。很多启动优化文章把 `onCreate` 的耗时归咎于“业务代码”，其实大头经常在 `inflate`：一个层级很深的布局、一个被 `include` 多次的复杂 View、一个在 `onCreate` 里才初始化的自定义控件，都会在这段时间里显形。

`onResume()` 返回时，Activity 在框架层面已经“恢复”，但屏幕上往往还没有一帧。因为 `onResume` 之后才调用 `WindowManager.addView()`，`ViewRootImpl.setView()` 又只是把窗口挂上去并请求一次遍历（`requestLayout`），绘制要等下一个 vsync。所以 `onResume` 的耗时和“用户看到内容”的耗时之间，还隔着窗口创建、Surface 分配、测量布局绘制、合成送显好几步。

这里有一个很常见的误判：把 `onCreate` 到 `onResume` 的耗时当作启动时间。`onResume` 很快，只能说明生命周期回调很快；如果布局复杂、首帧渲染慢，用户仍然会盯着一块空白或启动画面。反过来，有些 App 的 `onResume` 很慢，但用户这段时间盯着的是启动画面（系统窗口），感知上反而没那么差。**生命周期时间和上屏时间不是一回事，必须分开测。**

### 窗口、主题与 splash 的关系

`Activity.attach()` 时会根据主题创建窗口。如果主题是 `windowIsTranslucent` 或者 `windowIsFloating`，窗口的创建、合成和 splash 的退出逻辑都会不一样；Android 12 之后，系统 splash 的退场和 Activity 窗口的首帧之间还有一层交接，主题配错会导致“闪一下”“两个启动画面叠在一起”或者“splash 退得太早，露出白屏”。

一个实用但常被忽略的检查：`installSplashScreen()` 必须在 `super.onCreate()` 之前调用，并且 `postSplashScreenTheme` 要指向正常的应用主题。启动主题只负责 splash 的外观，`installSplashScreen()` 会负责把主题切回去；这一步没做对，启动画面和内容之间就会出现明显的视觉跳变。这个跳变不增加启动时间，但会增加“启动很慢”的主观感受。

## 6. 第一帧：从 vsync 到屏幕

![第一帧的一生](/images/android-app-launch-flow/05-first-frame.webp)

`ViewRootImpl.setView()` 里最关键的动作是 `requestLayout()`，它会把一次 traversal 排进 `Choreographer` 的回调队列。`Choreographer` 是主线程上的帧调度器，它通过 `DisplayEventReceiver` 订阅 SurfaceFlinger 的 vsync；vsync 到来时，`doFrame()` 依次执行输入、动画、遍历（`measure` / `layout` / `draw`）这几类回调。

一次典型的首帧渲染要经过：

1. **主线程**：`performTraversals()` 测量、布局、绘制。绘制阶段把 View 树记录成 `RenderNode` 树，而不是立刻交给 GPU。
2. **同步到 RenderThread**：主线程调用 `ThreadedRenderer`，把 `RenderNode` 树同步给 RenderThread。这一步是跨线程的，主线程不直接执行 GPU 命令。
3. **RenderThread**：`drawFrame()` 把绘制指令提交给 GPU，渲染到 `Surface` 的图形缓冲区，然后 `queueBuffer()` 把 buffer 交还给 `BufferQueue`。
4. **SurfaceFlinger**：收到 buffer 后合成所有可见图层，再通过 HWC 送到显示控制器。
5. **屏幕**：在下一个扫描周期把这一帧显示出来。

任何一个环节错过 vsync，这一帧就要等到下一个 vsync。60Hz 的屏幕上，一帧的预算是 16.7ms；首帧又往往要跨 2~4 个 vsync，因为它还要等窗口创建、Surface 分配、splash 退场这些前置条件。于是你经常会看到 `onResume` 在 200ms 时返回，而 `Displayed` 在 420ms 才打点。

### “已绘制”是谁记录的

`Displayed` 这行日志不是 App 自己打的，也不是 `ActivityThread` 打的。它是 `system_server` 侧的 `ActivityMetricsLogger` 在收到窗口绘制完成的信号后记录的。Android 的窗口系统会在 App 的第一帧被提交、且窗口完成首次绘制后，通知 ATMS；ATMS 再统一记一笔“这个 Activity 已经 displayed”。这也是为什么 `Displayed` 的终点比 `onResume` 晚，而且不依赖 App 埋点。

`splash` 的退场也挂在这条信号上。Android 12+ 的 `SplashScreen` 默认在首帧绘制后退出；App 可以通过 `setKeepOnScreenCondition` 让 splash 多留一会儿（比如等首页数据），但这有代价：用户看到的是“启动画面还在”，感知上并不会因为“数据已经准备好”而变快，反而可能觉得更慢。更常见的做法是让 splash 尽快退场，用骨架屏或占位内容承接，把“等待”从启动阶段移到内容加载阶段——用户的容忍度会高很多。

### 第一帧之后，启动还没有结束

第一帧只是“有内容了”。对很多 App 来说，用户需要的是数据加载完成、列表可滚动、按钮可点击，这就是 `reportFullyDrawn()` 对应的 TTFD。`reportFullyDrawn()` 可以在合适的时机调用，但系统有一个防作弊设计：如果在首帧完成之前调用，它会推迟到首帧之后再记录；而且实际记录以首次有效调用为准，不是“多调几次取最后一次”。所以在 `onCreate` 里提前调一下并不会把 TTFD 变小，只是自欺欺人。

判断“可交互”比“已绘制”更难：输入事件的分发、动画的稳定性、列表的预取都会影响体验。平台侧的 `FrameMetrics` / `JankStats` 看的是首帧之后每一帧的卡顿，Macrobenchmark 的 `StartupTimingMetric` 只给 TTID / TTFD 两个里程碑，回答不了“首帧之后卡不卡”。两者要一起看。

![一次冷启动的完整时序](/images/android-app-launch-flow/06-sequence.webp)

> 把前面几节拼在一起：Launcher 发出请求，system_server 决策并拉起 Zygote，新进程 attach 后执行 Application 和 Activity 生命周期，主线程完成第一帧，splash 退场。冷启动才走“进程诞生”那一段；温启动和热启动直接从生命周期开始。

## 7. 400 毫秒的预算

![一条 400ms 冷启动的预算表](/images/android-app-launch-flow/07-budget.webp)

先把“启动”当成一个项目来管：给它一个总预算，再把这笔预算分给链路上的每一段。图里把一条约 400ms 的冷启动摊到七个阶段，用来展示“钱花在哪几段”，不是某台设备上的严格实测：

| 阶段 | 量级 | 里面发生了什么 | 谁能让它变小 |
|---|---|---|---|
| ATMS 调度 + Binder 交接 | 20–60 ms | 权限校验、Intent 解析、Task 决策、进程创建指令、窗口事务 | 系统；应用只能减少启动次数 |
| Zygote fork + 进程初始化 | 10–40 ms | 页表复制、缺页、Binder 线程池、SELinux 切换 | 系统、OEM |
| bindApplication | 40–150 ms | 资源加载、Application 创建、Provider 初始化、`Application.onCreate` | 应用（主要可优化段） |
| Activity 创建 + inflate | 30–120 ms | 类加载、`Activity.attach`、`setContentView`、布局解析 | 应用 |
| 首帧渲染 | 40–150 ms | measure / layout / draw、RenderThread、GPU | 应用 |
| 合成 + 上屏 | 16–50 ms | SurfaceFlinger 合成、HWC 送显 | 系统 |
| 未归因抖动 | 0–100 ms | 系统繁忙、冷缓存、CPU 调度、锁竞争 | 双方 |

这张表最容易被误读成“把各段加起来就是启动时间”。七段加起来正好 400ms，只是为了让预算概念直观；在真实 trace 里，各段互相重叠：`startProcessAsync` 发起的进程创建和 system_server 的窗口/pause 事务并行推进，首帧渲染也要等某个 vsync 才开始。可靠的做法是在 Perfetto 里看自己应用的那条时间线，把每个 slice 的墙钟时间标出来，而不是拿一张通用表往项目上套。

预算表的正确用法是**分段设上限**。400ms 是示例总量；真实项目按目标定总预算（比如中端机冷启动 p90 ≤ 600ms），再按比例分给各段。比如：

```text
总预算（p90，冷启动，中端机）：≤ 600 ms
  bindApplication：≤ 150 ms
  Application.onCreate：≤ 50 ms
  Activity.onCreate：≤ 80 ms
  onResume → 首帧：≤ 150 ms
```

任何一段超过上限，就在代码评审或 CI 里把它拦下来。启动性能的退化很少是一次大改动造成的，更多是某次版本里“顺手加了一个 SDK”这种小改动，一点点把预算吃光。没有分段上限，回归就只能靠用户投诉发现。

### 钱到底花在哪里

把 trace 里最常见的“意外”列一下，基本能覆盖大部分现场：

- **冷缓存**：应用刚安装、系统刚重启、或者用户很久没打开，dex、资源、so 都不在 page cache 里，IO 会把所有阶段一起拉长。低端机 eMMC 的随机读比旗舰机 UFS 慢好几倍，这也是为什么同一版应用在不同设备上能差出几百毫秒（经验值）。
- **dex 与类加载**：安装时 `dex2oat` 如果没有把启动路径编译好（没有 baseline profile，或者 profile 不包含启动路径），启动时就要解释执行甚至现场 JIT。类加载本身也是成本，尤其是多 dex、大型 SDK、Kotlin 顶层对象和反射。
- **Provider 与 Application.onCreate**：每个 Provider 的 `onCreate` 都在主线程同步执行；`Application.onCreate` 里任何一次同步 IO、SP 读取、SDK 初始化，都会直接推后首帧。
- **布局与资源**：`setContentView` 的 inflate 成本与布局层级、自定义 View 的构造、图片解码强相关。启动页放一张大图，解码可能比布局还贵。
- **首帧渲染**：measure/layout/draw 的耗时取决于 View 树复杂度；Compose 应用还要加上首次组合的成本。
- **系统侧抖动**：`system_server` 忙、Binder 线程排队、CPU 被其他进程占满、温控降频。线上大样本里，这部分会以长尾形式出现。

所以“启动优化”至少是三层工作：**降低工作量**（少加载、少初始化）、**把工作移出首帧**（延后、异步、预计算）、**让等待看起来短**（splash、骨架屏、缓存内容）。三者的收益和风险完全不同，后面会分别讲。

## 8. 怎么把启动时间量出来

![不同启动度量工具的能力边界](/images/android-app-launch-flow/08-measure.webp)

先把工具摆在桌面上。没有一把尺子能同时做到“线上可用、可重复、能定位到方法”，所以启动测量很少能靠一个工具完成，通常要凑成一组：一个负责快速对比，一个负责定位，一个负责守住回归。

### 先用一把粗糙但快的尺子

本地最顺手的工具还是 `am start -W`：

```bash
adb shell am force-stop com.example
adb shell am start -W -n com.example/.MainActivity
```

输出大概长这样（Android 10 起）：

```text
Starting: Intent { cmp=com.example/.MainActivity }
Status: ok
LaunchState: COLD
Activity: com.example/.MainActivity
TotalTime: 412
WaitTime: 438
Complete
```

两个时间字段的口径完全不同：

- `TotalTime`：system_server 侧回填的启动耗时。起点不是 shell 发起命令，而是 ATMS 受理启动、`ActivityMetricsLogger.notifyActivityLaunching()` 记下时间戳；终点是窗口首帧绘制完成（`notifyWindowsDrawn()`）。等前一个 Activity pause、创建进程、`bindApplication`、`Application.onCreate`、`Activity.onCreate`、inflate、首帧绘制，都在这段里；不含 Launcher 的点击，也不含请求在 Binder 上的排队。
- `WaitTime`：纯 shell 侧墙钟，从命令进程开始等待到拿到结果为止，包含 Binder 往返、shell 被调度的延迟，恒大于等于 `TotalTime`。**把 `WaitTime` 当 App 启动时间，是最常见的错误之一。**

`Status: ok` 只表示等待没有超时，不表示启动成功；`LaunchState` 才是启动类型：COLD / WARM / HOT / RELAUNCH。很多老教程还在写 `ThisTime`——这个字段在 Android 10 的 `WaitResult` 里已经被移除，现代系统不会打印它。如果你在某篇文章里看到 `ThisTime`，基本可以判断那份资料停在 Android 9。

两个参数也值得知道：`-S` 会先 `force-stop` 再启动，中间固定 sleep 250ms；`-R n` 会循环 n 次，但每轮之间会模拟返回，所以从第 2 次起往往是温启动或热启动，要每轮都冷启动得用 `-R n -S`。

### Displayed：系统帮你打的点

```bash
adb logcat -s ActivityTaskManager ActivityManager | grep -i displayed
```

你会看到：

```text
ActivityTaskManager: Displayed com.example/.MainActivity for user 0: +412ms
```

这行日志由 `system_server` 侧的 `ActivityMetricsLogger.logAppDisplayed()` 打印，起点是 ATMS 受理启动请求，终点是窗口首帧绘制完成。它和 event log 里的 `wm_activity_launch_time` 来自同一个 `windowsDrawnDelayMs`，数值相等；这也是为什么这两个数字经常对得上。线上做大盘监控时，这是一个常见口径；但它有两个盲区：第一，它不知道你的内容是否真的可用（TTFD 要你主动 `reportFullyDrawn()`）；第二，它包含 splash 窗口的时间，启动画面停留得久，`Displayed` 也可能被推后，而用户感知未必同步变差。

如果 App 调用了 `reportFullyDrawn()`，还会多一行：

```text
ActivityTaskManager: Fully drawn com.example/.MainActivity for user 0: +1s234ms
```

想看更细的事件流，可以打开 event log：

```bash
adb logcat -b events | grep -E "wm_activity_launch_time|wm_on_create_called|wm_on_resume_called"
```

`wm_activity_launch_time` 与 `Displayed` 同源，字段大致是 `(user, token, component, time)`；`wm_on_create_called` / `wm_on_resume_called` 是 EventLog 定义的生命周期事件，用来观察事务从排队到执行隔了多久，写入点随版本变化。老资料里的 `am_activity_launch_time` 是旧 ActivityManager 口径，现代版本是否还写要看具体版本，别默认它和 `Displayed` 一致。

### Perfetto：能定位到方法的时间线

需要回答“这 400ms 花在哪一行代码”时，`am start -W` 不够用，得上 Perfetto。

抓一条启动 trace 的最小配置（文本 proto，保存为 `launch.cfg`）：

```textproto
buffers { size_kb: 65536 fill_policy: RING_BUFFER }

data_sources {
  config {
    name: "linux.ftrace"
    ftrace_config {
      atrace_categories: "am"
      atrace_categories: "wm"
      atrace_categories: "gfx"
      atrace_categories: "view"
      atrace_categories: "binder_driver"
      atrace_apps: "com.example"
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_wakeup"
      ftrace_events: "power/cpu_frequency"
      ftrace_events: "power/cpu_idle"
      buffer_size_kb: 8192
      drain_period_ms: 250
    }
  }
}

data_sources { config { name: "android.packages_list" } }
duration_ms: 8000
data_source_stop_timeout_ms: 10000
```

抓取：

```bash
adb push launch.cfg /data/local/tmp/launch.cfg
adb shell perfetto --txt -c /data/local/tmp/launch.cfg \
  -o /data/misc/perfetto-traces/launch.pftrace
adb pull /data/misc/perfetto-traces/launch.pftrace
```

如果只是想快速看一眼，Perfetto 的 simple mode 更省事：

```bash
adb shell perfetto -o /data/misc/perfetto-traces/launch.pftrace \
  -t 10s --buffer 64mb sched freq idle am wm gfx view binder_driver
adb pull /data/misc/perfetto-traces/launch.pftrace
```

也可以先用 `atrace` 抓个轻量版：

```bash
adb shell atrace -t 5 -b 32768 am wm gfx view sched freq idle binder_driver > startup.atrace
```

打开 trace 后，重点找这些 slice：

```text
system_server 泳道：startActivityInner / launching: com.example / dispatchingStartProcess:com.example / attachApplication:com.example
App 泳道：        bindApplication / ActivityThreadMain / PostFork / activityStart / activityResume / Choreographer#doFrame
渲染相关：        inflate / measure / layout / draw / RenderThread 的 DrawFrame
```

这些是 Android 15 源码里能对上的名字；不同版本、厂商定制和 atrace 分类会略有差异，找不到某个 slice 时先怀疑名字而不是链路。`wm_on_create_called` / `wm_on_resume_called` 来自 EventLog（30057 / 30022），写入点随版本变化；同名 atrace slice 在不同版本/层级可能也存在，用之前先确认，别把两者混着用。

在 Perfetto 里看启动，比记住 slice 名字更重要的是建立一个习惯：**先找“首帧”那个点，再往回推**。首帧之前的每一段空白、每一次线程唤醒、每一次 Binder 等待，都是候选优化点。很多时候你会看到 App 主线程在等 Binder、等锁、等 IO，而不是在“执行代码”；这类等待是最容易在代码层面被忽略的。

### Macrobenchmark：可重复、可进 CI 的尺子

如果你的团队要守启动性能，最终需要一个可重复的基准。`androidx.benchmark` 的 Macrobenchmark 是目前最合适的选择：

```kotlin
@get:Rule
val benchmarkRule = MacrobenchmarkRule()

@Test
fun startup() = benchmarkRule.measureRepeated(
    packageName = "com.example",
    metrics = listOf(StartupTimingMetric()),
    iterations = 10,
    startupMode = StartupMode.COLD,
    compilationMode = CompilationMode.Partial(),
) {
    pressHome()
    startActivityAndWait()
}
```

`StartupTimingMetric` 提供两个值：`timeToInitialDisplayMs`（从系统收到启动 intent 到目标 Activity 首帧）和 `timeToFullDisplayMs`（到 App 调用 `reportFullyDrawn()`）。它跑在 release 构建上，会自动控制编译模式、启动模式和屏幕状态，重复多次取统计值——这些恰好是 `am start -W` 做不到的。把它接进 CI，每次 PR 跑一次冷启动，超过阈值就报警，启动性能才可能长期不退化。

一个提醒：Macrobenchmark 测的是“受控条件下的启动”，不是用户真实场景。用户可能在低电量、后台一堆应用、弱网、冷缓存的情况下启动；基准数据用来比较版本间的相对变化，不要拿它当线上绝对值。

### ApplicationStartInfo：线上归因的新工具

Android 15（API 35）起，App 可以通过 `ApplicationStartInfo` 看到这次启动的系统视角信息。它不是同步返回的，而是通过监听器或历史记录拿：

```kotlin
val am = context.getSystemService(ActivityManager::class.java)

am.addApplicationStartInfoCompletionListener(context.mainExecutor) { info ->
    if (info.startupState != ApplicationStartInfo.STARTUP_STATE_FIRST_FRAME_DRAWN) {
        return@addApplicationStartInfoCompletionListener
    }
    val ts = info.startupTimestamps
    val launch = ts[ApplicationStartInfo.START_TIMESTAMP_LAUNCH] ?: return@addApplicationStartInfoCompletionListener
    val firstFrame = ts[ApplicationStartInfo.START_TIMESTAMP_FIRST_FRAME] ?: return@addApplicationStartInfoCompletionListener
    val ttidMs = (firstFrame - launch) / 1_000_000
    // startType / reason 一起打点，才能把“冷启动长尾”分桶
    logStartup(info.startType, info.reason, ttidMs)
}

// 本次启动可能早于监听注册，回看最近几次
am.getHistoricalProcessStartReasons(3).forEach { /* 同样的字段 */ }
```

`getStartupTimestamps()` 里是一组纳秒时间戳：`START_TIMESTAMP_LAUNCH`、`FORK`、`BIND_APPLICATION`、`APPLICATION_ONCREATE`、`FIRST_FRAME`、`FULLY_DRAWN` 等。`startType` 区分冷/温/热，`reason` 区分图标、通知、广播、Service、Provider……Android 16 又加了 `getStartComponent()`，可以区分触发启动的组件类型，对“后台广播拉起进程导致启动变慢”这类问题尤其有用。

它不能替代 trace，但能回答很多线上问题：这次究竟是冷启动还是热启动？是用户点图标，还是某个广播把进程拉起来的？进程启动后卡在哪个阶段？把 TTID 按 `startType` 和 `reason` 分桶之后，“冷启动慢”这种笼统结论才有机会变成可以动手的线索。

### 测量方法本身也会骗人

最后说几个方法论上的坑。它们比工具选择更常见：

- **用 debug 包测启动**。debug 包没有 R8、没有 profile、JIT 策略也不同，测出来的数字不能代表 release。要测就测 release，且签名与线上一致。
- **把温启动当冷启动**。`am force-stop` 才是真冷启动；`am kill` 只能杀可杀的后台进程，杀得掉是冷启动，杀不掉往往还是热启动。热启动更简单——按 Home 再回来就是。
- **只跑一次**。启动耗时受系统负载、温度、文件缓存影响很大，单次数字没有意义。至少跑 10 轮，看中位数和 P90，并且把“安装后首次启动”单独拎出来——它包含 dexopt 和 profile 安装，明显更慢。
- **以为 `TotalTime` 不含 pause**。`TotalTime` 的起点在 ATMS 受理请求，等前一个 Activity pause 的时间本来就算在里面；`WaitTime` 只是在这个基础上又加了 shell 排队。被污染的通常不是“有没有 pause”，而是你的启动路径是否经过了一个很重的页面。
- **Perfetto buffer 太小**。默认 buffer 可能只有几 MB，启动 trace 很容易丢数据，尤其是 sched 和 binder 事件都打开时。抓之前把 size 调大，抓完先看 trace 的 stats。
- **不固定编译状态**。`cmd package compile` 的结果会显著改变启动数字：`speed` 编译过会让结果“过于漂亮”，`verify`/`reset` 会让结果“过于难看”。实验前统一编译状态，记录 `dumpsys package dexopt`，或者直接用 Macrobenchmark 的 `CompilationMode` 固定它。
- **一边测一边装应用**。`adb install`、系统更新、Play 商店后台任务都会影响结果；测试前把设备静置一会儿，同一台设备做 A/B 时随机化顺序。

## 9. 优化的顺序

![启动优化的四种杠杆](/images/android-app-launch-flow/09-optimize.webp)

启动优化最常见的失败方式，是一上来就“优化代码”：把某个方法改成异步、把某个库换成更轻的、给 `onCreate` 加个 `Thread`。这些动作可能有效，但如果不知道时间花在哪一段，你只是在碰运气。

我更推荐按这个顺序推进：**测量 → 定预算 → 删除 → 挪走 → 并行 → 观感**。前两步是前提，后四步按收益/风险排序。

### 删除：收益最大，也最容易被忽略

启动链路上最贵的，常常是**根本不需要在这个时机做的事**。

第一步是审计 ContentProvider。把合并后的 manifest 打出来，看看有多少个 Provider：

```bash
aapt2 dump xmltree app-release.apk --file AndroidManifest.xml | grep -A2 "<provider"
# 或者直接用 Android Studio 的 APK Analyzer 看 merged manifest
```

每一个 Provider 都会在主线程上被同步构造并执行 `onCreate`，而且发生在 `Application.onCreate` 之前。很多 SDK 会在自己的 AAR manifest 里偷偷注册 Provider，你只是在 `build.gradle` 里加了一行依赖，启动时就多了一段初始化。能删的删，能合并的用 App Startup 合并，不能删的至少确认它是不是真的需要在首帧前跑。

第二步是盘点 `Application.onCreate` 和 `attachBaseContext` 里的初始化。判断标准很简单：**这段初始化产出的东西，首帧用得到吗？** 如果首帧只是一个 loading 页或列表骨架，推送、统计、AB 实验、图片库的磁盘缓存、数据库迁移，全都可以挪到首帧之后。

第三步是构建层面的瘦身：R8 打开、资源压缩、`so` 裁剪、移除未使用的 native 库。包体变小，IO 和加载成本都会下降；这不是“启动专项优化”，但它会体现在启动数字里。

### Profile：让 ART 提前知道启动路径

Baseline Profile 是系统侧给应用的现成优化，但很多人用错了。

**Baseline Profile** 是一份“哪些方法值得 AOT 编译”的清单。构建时，它被编译成 `baseline.prof` 放进 APK；安装或更新时，ART 用它做 `speed-profile` 编译，让启动路径上的方法不必每次解释执行或等 JIT。**Startup Profile** 则是另一回事：它喂给 DEX 布局优化，把启动相关的代码集中到主 dex 里，减少启动时的 major page fault。两者经常被混为一谈，实际上一个影响“编译”，一个影响“加载”。

生成 Baseline Profile 的常见方式是用 Baseline Profile Gradle 插件配合 Macrobenchmark 采集；文件通常叫 `baseline-prof.txt`，startup profile 则放在 `src/<variant>/main/baselineProfiles/` 下，名字是 `startup-prof.txt`，AGP 8.3+ 支持按 variant 生成和消费。接入 `androidx.profileinstaller` 之后，非 Play 渠道或旧版本设备可以靠它把 profile 写进去；Play 则会在安装/更新时应用 APK 里的 baseline profile 和 cloud profile，让没跑过你应用的用户也能受益。

有几个硬限制和坑：

- 打包进 APK 的 `baseline.prof` 有 **1.5 MB 上限**，超过之后行为依版本而定（丢弃或构建报错），所以 profile 要覆盖关键路径，而不是无脑全量。
- profile 不是“装完立即生效”的。`profileinstaller` 通常在进程启动几秒后才写 profile，第一次安装的首次启动享受不到；Play 下发的 profile 也要等安装/更新流程完成。
- debug 包不会走线上 profile，测启动必须用 release。
- profile 只覆盖它采集到的路径。如果你的启动路径变了（换了启动页、加了新 SDK），旧 profile 就失效了，需要重新采集。
- 手动验证 profile 是否生效，可以看 APK 里有没有 `assets/dexopt/baseline.prof`，以及设备上的 `/data/misc/profiles/cur/0/<pkg>/primary.prof`；也可以用 `cmd package compile` 强制重编译做对照实验。

```bash
unzip -l app-release.apk | grep -E "baseline.prof|baseline.profm"
adb shell cmd package compile -m speed-profile -f com.example
adb shell dumpsys package dexopt | grep -i com.example
```

### 挪走：把工作从首帧前移到首帧后

“挪走”是启动优化里性价比最高、也最容易被做坏的手段。

正确的做法是：首帧只做“必须现在做”的事；其余初始化注册成“首帧后任务”，在 `ViewTreeObserver.OnPreDrawListener`、`Choreographer.postFrameCallback` 或空闲队列里执行。注意不要用 `Handler.post` 直接扔到主线程队列尾部——那仍然在主线程上，只是晚了几毫秒；重活要放到后台线程，并且保证主线程不 `await` 它。

App Startup 在这里很好用：它把多个 `Initializer` 合并到一个 `InitializationProvider` 里，按依赖拓扑排序执行。你可以把非首帧必需的 Initializer 从 `InitializationProvider` 里摘出来，改成手动延迟初始化；也可以给某个库的 initializer 加 `tools:node="remove"`，自己控制初始化时机。

```kotlin
class CrashInitializer : Initializer<Unit> {
    override fun create(context: Context) {
        CrashReporter.init(context)
    }

    override fun dependencies(): List<Class<out Initializer<*>>> = emptyList()
}
```

```xml
<provider
    android:name="androidx.startup.InitializationProvider"
    android:authorities="${applicationId}.androidx-startup"
    android:exported="false"
    tools:node="merge">
    <meta-data
        android:name="com.example.CrashInitializer"
        android:value="androidx.startup" />
</provider>
```

但“挪走”有一个必须诚实面对的代价：它把成本从 TTID 转移到了 TTFD。如果用户首帧看到的是完整界面、但数据要 2 秒后才出来，那不算优化，只是把等待藏到了骨架屏后面。挪走的工作要配合**可感知的过渡**（骨架屏、占位图、进度），否则只是让指标好看。

### 并行：缩短串行路径，而不是制造更多线程

启动初始化经常是一棵依赖树：崩溃上报依赖设备信息，设备信息依赖存储，存储依赖权限检查……把所有任务串起来顺序执行，总时长是各段之和；把互不依赖的任务并行，总时长取决于最长路径。

做这件事的前提是先画出依赖图，然后用 `CompletableFuture`、Kotlin 协程或专门的任务调度器执行。几个原则：

- 主线程只负责“发起”和“等待结果”，不要在并行任务里偷偷 `runBlocking`；
- 给每个任务设置超时和降级，别让一个慢 SDK 拖死整条启动；
- 并行度不要超过设备能承受的范围，低端机上开一堆线程反而会因为调度和锁竞争变慢；
- 任何并行任务都不应该在首帧前持有主线程需要的锁。

如果你不确定某个初始化能不能并行，先别动它。启动优化里，“不确定”通常意味着“有隐藏依赖”。

### 首帧与观感：该做的和不该做的

首帧侧最有效的优化通常是布局：减少层级、用 `ConstraintLayout` 扁平化、`ViewStub` 延迟加载首屏不需要的部分、避免首帧前多次 `requestLayout`。Compose 应用则要关注首次组合的成本，Baseline Profile 对 Compose 的收益尤其明显。

Splash 是观感层的工具，不是性能工具。Android 12 之后系统统一接管启动画面，App 能控制的是退出时机和退场动画：

```kotlin
override fun onCreate(savedInstanceState: Bundle?) {
    val splash = installSplashScreen()   // 必须在 super.onCreate() 之前
    super.onCreate(savedInstanceState)
    setContentView(R.layout.main)
    splash.setKeepOnScreenCondition { viewModel.isLoading }
    splash.setOnExitAnimationListener { view -> view.remove() }
}
```

`setKeepOnScreenCondition` 会在每次请求绘制前求值，条件必须极快；用它等首屏数据，用户看到的可能是“启动画面停了很久”，感知上未必更好。更常见的选择是让 splash 按系统默认节奏退场，用骨架屏承接后续加载。

最后是交互层的优化：首屏数据缓存、列表预取、点击后的即时反馈、返回时的状态保留。这些不改变 `Displayed`，但会改变用户对“快”的感受。日志里的数字只是代理指标；用户按下图标后，多久觉得自己“可以用了”，才是终局。

## 10. 几个容易踩的坑

前面按链路讲，这一节按“事故现场”讲。下面这些在真实项目里反复出现，往往不是技术难题，而是认知偏差。

### 初始化：把不该现在做的事拿掉

**把 Application 当成初始化容器。** `Application.onCreate` 只是 App 自己的一个初始化入口——在它之前还有 `attachBaseContext` 和所有 Provider 的 `onCreate`。它也不是“应用启动时必须把所有东西准备好”的承诺。崩溃上报、埋点、推送、图片库、网络库、数据库、AB 实验、配置中心……全塞进去，启动自然慢。判断标准只有一个：首帧用得到吗？用不到就往后放。

**忽略 ContentProvider 的启动税。** 它比 `Application.onCreate` 更早执行，而且是在主线程上同步执行。很多 SDK 的自动初始化就藏在 Provider 里；你甚至没有在代码里调用它。审计 merged manifest 是启动优化的第一步，成本极低，收益常常超出预期。

**多进程带来的隐藏成本。** 只要有一个组件声明了 `android:process`，那个进程启动时就要再跑一遍 Application 和它的 Provider。主进程启动可能被无关进程拖慢，尤其是推送、Widget、后台 Service 这些“看起来不影响启动”的组件。能用单进程解决的，不要为了“解耦”拆进程。

**WebView 预热放错位置。** 首次创建 WebView 会加载 WebView provider 和 native 库，成本几十到上百毫秒。放在首帧前预热，等于让所有用户为少数会打开 H5 的场景买单；放到首帧后或空闲时更合理。

### 时序：别把 onResume 当终点

**把 onResume 当成“启动完成”。** `onResume` 返回时，窗口可能还没加、首帧还没画、splash 还没退。用 `onResume` 计时会漏掉首帧渲染和 splash 退场；用它做“启动完成”的埋点，会得到一个系统性偏小的数字。

**只优化 TTID，不看 TTFD 和 jank。** 把初始化挪到首帧后，TTID 会变好看，但用户可能看到的是一个空列表加转圈。TTFD 和 `FrameTimingMetric` 才能告诉你“内容真正可用”和“首帧之后有没有卡”。两个指标一起看，才不会被自己的优化骗到。

**并行初始化没有依赖图。** 看到 `onCreate` 慢，第一反应是“开线程并行”。但如果两个初始化共用同一把锁、同一个磁盘目录、同一个类加载器，并行只会让它们互相等；低端机上线程太多还会增加调度开销。先画依赖图，再谈并行。

**把 splash 当成性能优化。** Splash 让用户更早看到反馈，但它不减少真实首帧时间。人为延长 splash（比如等接口返回）会把“启动慢”变成“启动画面停很久”，指标可能好看，体验不会。官方对 splash 的定位一直是反馈，不是加速。

### 验证：别在错误的环境里下结论

**在低端机上翻车。** 旗舰机上 200ms 的初始化，在低端机上可能变成 600ms：IO 更慢、CPU 更弱、内存压力更大、dexopt 策略不同。启动优化的验证必须在低端机上做，至少要在目标用户的主流机型上做；只在 Pixel 上测，得到的结论经常不可迁移。

**忽略“启动的边界”。** 有些启动成本其实不在启动链路里，而在用户看到的第一屏：首屏接口慢、图片解码慢、列表首次布局慢。这时候优化 `onCreate` 的收益很小，应该优化首屏数据和占位内容。分清“启动阶段”和“首屏阶段”，能省下很多无效优化。

## 11. 尾声：回到那半秒

回到开头的那次点击。用户看到的是：图标按下、启动画面出现、内容出来、可以滑动。四个进程、若干次 Binder、一次 fork、几次 vsync 的等待，被压缩成了不到半秒的视觉体验。

这条链路里，应用能控制的部分其实没有想象中多：zygote 是系统的，ATMS 的调度是系统的，SurfaceFlinger 的合成是系统的，连 splash 都是系统画的。应用能控制的是**自己进程里那段**——`bindApplication`、`Application.onCreate`、Provider、Activity、布局、首帧。把这段做到极致，在中等复杂度应用上往往能省下几十到两三百毫秒（经验值）；再往上，就要靠系统、靠设备、靠产品设计（比如首屏缓存、骨架屏、预加载）一起配合。

所以启动优化值得培养的能力有三样：

1. **能画出链路**：知道一次点击之后，请求经过了哪些进程、哪些线程、哪些队列；
2. **能量出时间**：知道每个工具测的是哪一段，知道自己的数字和别人的数字为什么不一样；
3. **能做取舍**：知道每项优化把成本挪到了哪里，以及这个代价用户是否愿意接受。

Android 的启动流程一直在变：Android 9 把生命周期事务化，Android 12 统一了 splash，Android 15 给了 `ApplicationStartInfo`，Android 16 继续细化启动归因。但底层那条链路——意图、进程、绑定、生命周期、首帧——骨架一直没变。理解了这条链路，新版本带来的新 API 只是多了一把更好用的尺子，而不是一套需要重新背诵的流程。

## 附：版本速查

| 能力 / 行为 | 起始版本 | 说明 |
|---|---|---|
| `Activity.reportFullyDrawn()` | API 19 | 定义 TTFD / “Fully drawn” |
| `FrameMetrics` | API 24 | 逐帧 UI / RenderThread 耗时 |
| `ClientTransaction` / `TransactionExecutor` | API 28 | 生命周期事务化 |
| Baseline / cloud profile 的编译路径 | API 28+（Play 参与） | `speed-profile` 部分 AOT |
| `android:zygotePreloadName` / `ZygotePreload` | API 29 | app zygote fork 前预加载应用代码，供隔离服务使用 |
| 系统 SplashScreen（`android.window.SplashScreen`） | API 31 | 冷/温启动统一启动画面，首帧后退场 |
| AndroidX `core-splashscreen` | minSdk 21（图标 23+） | 向下兼容的系统 splash |
| `windowSplashScreenBehavior` | API 33 | 控制是否始终显示动画图标 |
| `ApplicationStartInfo` | API 35 | startType / reason / startupState / 时间戳 |
| `ApplicationStartInfo#getStartComponent()` | API 36 | 区分 Activity / Service / Broadcast / Provider 触发 |
| 16 KB page size 支持要求 | Android 15（16 KB 页设备） | Google Play 自 2025-11-01 起要求 target 35+ 的新包/更新支持，和启动间接相关 |

## 参考资料

- [Android 官方：应用启动时间（TTID / TTFD）](https://developer.android.com/topic/performance/vitals/launch-time)
- [Android 官方：启动画面 SplashScreen](https://developer.android.com/develop/ui/views/launch/splash-screen)
- [Android 官方：迁移到 Android 12 SplashScreen](https://developer.android.com/develop/ui/views/launch/splash-screen/migrate)
- [Android 官方：Baseline Profile 概览](https://developer.android.com/topic/performance/baselineprofiles/overview)
- [Android 官方：Baseline Profile 与 Startup Profile 的区别](https://developer.android.com/topic/performance/baselineprofiles/difference-baseline-startup)
- [Android 官方：Macrobenchmark 指标](https://developer.android.com/topic/performance/benchmarking/macrobenchmark-metrics)
- [Android 官方：ApplicationStartInfo](https://developer.android.com/reference/android/app/ApplicationStartInfo)
- [Android 官方：App Startup 库](https://developer.android.com/topic/libraries/app-startup)
- [Android 官方：ART 配置与编译器过滤器](https://source.android.com/docs/core/runtime/configure)
- [Perfetto 文档：Android 上的 trace 抓取](https://perfetto.dev/docs/getting-started/local-android-trace-recording)
- [AOSP：`ActivityThread.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/app/ActivityThread.java)
- [AOSP：`ActivityStarter.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/wm/ActivityStarter.java)
- [AOSP：`ActivityMetricsLogger.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/wm/ActivityMetricsLogger.java)
- [AOSP：`ZygoteInit.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/com/android/internal/os/ZygoteInit.java)
- [AOSP：`TransactionExecutor.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/app/servertransaction/TransactionExecutor.java)