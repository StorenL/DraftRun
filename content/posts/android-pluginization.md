---
title: 同一个进程，两个 APK：Android 插件化技术全景
dek: ClassLoader、资源与组件代理
excerpt: 一个活动页在安装包里找不到：它被压成 zip 放在 CDN 上，用户点进去才下载、校验、加载。系统始终只认识宿主一个 APK，插件的身份全部由宿主借用。文中按类加载、资源、组件代理三条线拆开这套技术。
date: 2026-11-26
time: 23:19
minutes: 50
tag: Android
hot: true
note: 文中耗时数字除标注来源外均为量级示意，不是任何具体机型的承诺值；框架细节以各项目对应版本的源码为准。
---
![封面：同一个进程，两个 APK](/images/android-pluginization/00-cover.webp)

一个活动页，在安装包里找不到。

它被压成一个几十兆的 zip，躺在 CDN 上。用户点进入口，宿主下载、校验、解压，再花几百毫秒把它加载起来。图片、文案、埋点，甚至一个 native 的排序模型 `.so` 都在跑。返回键按下去，页面消失，插件的类还留在内存里，等着下一次被唤醒。

整个过程中，系统只认识一个 APK。它没有安装过插件，`PackageManager` 的组件表里没有插件的 Activity，`ActivityTaskManager` 看到的永远是宿主容器，也没有哪个进程是“插件的”。所有“插件是一个合法应用”的迹象，都是宿主借自己的身份，替它做了一遍。

插件化就是这套“借身份”的工程。它不好写，因为 Android 并没有为第二个 APK 留门；它也不神秘，因为每一件事最后都落在四个很具体的边界上：**类、资源、组件、进程**。过去十几年里出现过的框架，无论叫 DroidPlugin、RePlugin、VirtualAPK、Atlas 还是 Shadow，无非是在用不同的手法翻这四堵墙。

先给一个不太讨喜的结论：如果今天你要在一个只走 Google Play 的新项目里做“不发版就能改功能”，答案通常不是插件化，而是 App Bundle 的动态特性、服务端驱动的 UI，或者 React Native / Flutter 这类自带引擎的方案。插件化的用武之地比 2017 年窄了很多。但它仍然值得学。能把一个没安装的 APK 跑起来的人，一定已经把 Android 的类加载、资源表、组件模型和进程模型各走了一遍；这四样东西，平时被 `startActivity()` 藏得太好了。

## 1. 先把词说清楚

中文技术圈里，“插件化”经常和“组件化”“模块化”“热修复”“动态化”混着用。它们有交集，但不是同一件事。混着用，讨论架构时很容易得出“我们也要做插件化”这种结论，而实际需要的可能只是拆模块。

| 名词 | 交付物是什么 | 运行时边界 | 典型实现 | 是否下发可执行代码 |
|---|---|---|---|---|
| 模块化 | 源码与构建单元 | 编译期，最终仍是一个 APK（或一组 split） | Gradle module、依赖注入、路由 | 否 |
| 组件化 | 以业务/能力划分的模块 + 通信机制 | 编译期，运行在同一个 APK 里 | ARouter、SPI、接口下沉 | 否 |
| 热修复 | 少量补丁（dex/so/资源） | 替换已安装 APK 中的部分实现 | Tinker、Sophix 等 | 是（补丁也是代码） |
| 插件化 | 一个独立的、未安装的 APK/包 | 宿主进程内（或宿主管理的进程内），借用宿主组件身份 | Shadow、RePlugin、VirtualAPK | 是 |
| 应用虚拟化 / 沙箱 | 一个可安装、运行第三方 APK 的“虚拟系统” | 宿主内部虚拟出安装环境，运行未改动的 APK | VirtualApp、双开类产品 | 是（且通常是第三方代码） |
| 动态化 | 配置、DSL、脚本、JS bundle、UI 描述 | 解释执行或引擎内执行 | H5、RN、Flutter、小程序容器、服务端驱动 UI | 通常否（脚本/配置） |
| 动态特性交付 | 由商店安装的 split APK | 系统安装，属于应用的一部分 | Play Feature Delivery、App Bundle | 是，但由商店交付 |

几个容易混淆的边界，值得单独说：

**插件化不是“把模块打成 aar”。** aar 是编译期依赖，最后会进同一个 APK；插件的定义恰恰是“编译期不进宿主，运行时才出现”。这两者的工程约束完全不同：aar 可以随便引用宿主代码，插件不行，因为插件在编译时根本不知道宿主会长成什么样。

**热修复不是“小插件”。** 热修复的目标是替换已有实现，通常不新增组件，也不改变 `AndroidManifest.xml`；插件化要新增 Activity、Service、资源，甚至新的 `Application`。热修复的补丁一般很小，插件可以很大；热修复的难点在“怎么让 ART 认这份新代码”，插件化的难点在“怎么让系统认这个新组件”。

**动态化是个更大的筐。** 服务端下发一份 JSON，客户端按 schema 渲染页面，这是动态化；下发一个 JS bundle，在 WebView 或 JS 引擎里跑，也是动态化；下发一个 APK，在宿主进程里跑，才是插件化。前两者的代码运行在解释器/引擎里，后者运行在 ART 上，能碰到的系统 API 完全不同——这也是合规政策的分界线。

**沙箱是插件化的“重模式”。** 插件化假设你控制插件代码，可以让它继承 `ShadowActivity`、可以在编译期做 transform；沙箱假设你控制不了要运行的 APK（可能是任何一个第三方应用），所以只能把 `PackageManager`、`ActivityManager`、`ContentProvider`、文件路径、设备标识全部虚拟一遍。两者的工程量差一个数量级。

## 2. 它为什么曾经流行，又为什么退潮

插件化在 2014 到 2018 年之间密集出现，有很具体的历史条件。

那几年 Android 的发版节奏慢：应用商店审核几天到几周，渠道包要重新签名，灰度要按渠道来。一个运营活动等不起两周，一个线上崩溃也等不起。包体同时在膨胀，超级 App 动辄上百兆，但用户常用的功能可能只占两成。大团队还面临另一个问题：几百个工程师在一个仓库里，编译一次二十分钟，A 团队改一个公共类，B 团队的构建就红了。

插件化同时给了三个诱人的承诺：**动态发布**（不经过商店就能上新功能）、**按需加载**（低频功能不进主包）、**物理隔离**（插件有自己的 ClassLoader 和资源域，编译和运行都解耦）。加上当时 Android 的缝隙还很宽——`DexClassLoader` 是公开 API，`AssetManager.addAssetPath` 虽然隐藏但能反射到，`Instrumentation` 可以被替换，hidden API 名单还没收紧——于是一批框架在几年内长了出来。

变化发生在 2018 年之后，而且是几条线同时收紧。

第一条是**系统的限制**。Android 9（API 28）开始限制非 SDK 接口，插件化框架赖以生存的 `ActivityThread` 字段、`Instrumentation` 替换、`IActivityManager` 代理，逐个进入灰名单或黑名单。Android 10 收紧分区存储，Android 11 引入包可见性，Android 12 要求 `PendingIntent` 显式声明可变性，Android 13/14 要求动态注册的广播接收器声明导出行为。Android 14 更进一步：动态加载的 DEX/JAR/APK 必须是只读文件；到 Android 17，这条规则延伸到 native 库，`System.load()` 加载的文件也必须只读。

第二条是**商店政策**。Google Play 的 Device and Network Abuse 政策写着：应用不得通过 Play 之外的机制修改、替换或更新自身，也不得从 Play 之外的来源下载可执行代码（如 dex、JAR、.so）。例外只留给运行在虚拟机或解释器里的代码，比如 WebView 里的 JavaScript。插件化下载的是货真价实的 dex 和 so，正好落在禁止的这一侧。国内应用商店的审核口径不完全一样，但“动态下发可执行代码需要可审核、可回滚、可追责”是共同方向。

第三条是**替代方案成熟**。App Bundle 和 Play Feature Delivery 让“按需下载模块”变成了官方能力；React Native、Flutter、小程序容器把“动态发页面”做成了基础设施；服务端驱动 UI 让大量运营页不需要发版。这些方案要么不下载可执行代码，要么由商店负责交付，绕开了插件化最敏感的部分。

所以今天的局面是：插件化没有消失，但它从“默认选项”变成了“特定工具”。还在用它的场景，大致有几类：

- 国内超级 App 的生态容器。宿主需要加载第三方开发的页面/能力，且不能要求对方走应用商店；
- 游戏、工具类应用的多开与沙箱。要运行未修改的第三方 APK，属于应用虚拟化；
- 企业内部分发、专有设备、车机/OTT 预装。没有 Google Play，更新通道由自己控制；
- 动态 native 能力。算法模型、音视频处理、风控 SDK 以 `.so` 形式按需下发；
- 大型 App 的“壳工程”。把构建拆成宿主 + 若干插件，缩短编译时间，顺带获得运行时的隔离。

这些场景有一个共同点：它们都愿意为“动态性”支付额外的工程和合规成本，而且通常有团队能长期维护。反过来说，如果只是为了“少发一次版”，今天有更便宜的路径。

## 3. 四堵墙

要理解插件化，先要理解 Android 为什么不让你加载第二个 APK。答案不是一条规则，而是四层彼此独立的设计。

![插件化要翻的四堵墙](/images/android-pluginization/01-boundaries.webp)

> 四堵墙对应四种系统契约：类加载器、资源表、组件表、进程身份。插件化框架的差别，本质上是在哪一层、用什么方式把这些契约“转接”过去。

**第一堵墙是类。** 每个应用进程在启动时，`ActivityThread` 会为它准备一个 `PathClassLoader`，路径指向已安装的 APK。系统通过这个加载器找到 `Application`、`Activity`、`Service` 的类。插件 APK 不在这个路径里，所以系统看不见它的类。想跑插件的代码，必须自己造一个 ClassLoader，把插件的 dex 挂上去。这一步是四堵墙里最简单的，因为 Android 提供了公开的 `DexClassLoader`。

**第二堵墙是资源。** `Resources` 对象在进程启动时就绑定了宿主的 APK，`AssetManager` 里只有宿主的资源路径。插件的布局、图片、字符串、主题都在它自己的 `resources.arsc` 里，默认不可见。更麻烦的是资源 ID：`0x7f` 这个包段被宿主占着，插件如果也用 `0x7f`，两套资源会撞在一起。资源这堵墙没有公开的“加一个 APK”接口，历史上一直靠反射。

**第三堵墙是组件。** `Activity`、`Service`、`BroadcastReceiver`、`ContentProvider` 要在 `AndroidManifest.xml` 里声明，安装时被 `PackageManager` 解析进组件表。之后 `ActivityTaskManager` 启动 Activity 前会检查它是否在表里、是否 exported、权限是否满足；`ActivityThread` 收到事务后，用 `Instrumentation` 实例化它。插件没安装，组件表里没有它，系统在第一步就会拒绝。这堵墙最厚，因为它不是一个 API，而是一整套跨进程的契约。

**第四堵墙是进程。** 即使前三堵都翻过去了，插件仍然跑在宿主的 uid、进程、数据目录、native 库目录里。它没有自己的权限，没有自己的 `/data/data/<pkg>`，没有自己的 Binder 身份，通知、Task、`PendingIntent` 也都挂在宿主名下。插件化通常接受这一点——插件就是宿主的扩展；沙箱则试图把这堵墙也虚拟掉。

四堵墙对应四种手法：**Hook**（骗过系统的检查）、**占坑**（借一个已注册的壳）、**代理**（把系统调用转接到壳上）、**虚拟化**（造一个假系统）。所有框架都是这四种手法的组合，区别只在比例和实现位置。

## 4. 类：把插件的 dex 接进来

### 4.1 一条类加载链

Android 的类加载沿用 Java 的双亲委派模型，但实现是 ART 自己的。一个应用进程里，默认的加载器链大致是：

```text
BootClassLoader（加载 android.*、java.* 等系统类）
  └── PathClassLoader（加载已安装 APK 的 dex，父加载器是 BootClassLoader）
```

`PathClassLoader` 和 `DexClassLoader` 都继承 `BaseDexClassLoader`。真正干活的是它内部的 `DexPathList`：一个 dex 文件对应一个 `Element`，`Element` 里包着 `DexFile`、路径和加载器。`ClassLoader.loadClass()` 的顺序是：先看自己加载过没有（`findLoadedClass`），再问父加载器，父加载器找不到才调用自己的 `findClass()`。`BaseDexClassLoader.findClass()` 会遍历 `DexPathList` 里的 `Element`，让 `DexFile` 去定义类。

这套机制给插件留了一个口子：既然 `findClass` 是按路径找 dex，那我只要 new 一个 `DexClassLoader`，把插件 APK 的路径传进去，再把它挂到某个父加载器下面，插件的类就能被加载了。

```kotlin
val pluginLoader = DexClassLoader(
    pluginApk.absolutePath,      // 插件的 dex/apk 路径
    null,                        // optimizedDirectory，API 26 起已无效
    pluginNativeLibDir,          // 插件 so 所在目录，可为 null
    hostClassLoader              // 父加载器，决定插件能看见谁
)
val activityClass = pluginLoader.loadClass("com.example.plugin.MainActivity")
```

`DexClassLoader` 的第二个参数 `optimizedDirectory` 曾经很重要：在 Dalvik 和早期 ART 上，dex 要先优化成 odex 才能加载，这个目录用来放优化产物。API 26 起它被废弃，传 `null` 即可；Shadow 里还留着 `ODexBloc`，只在 API 27 以下才真正执行预优化，也是这个原因。

除了 `DexClassLoader`，还有两个现代选项：`InMemoryDexClassLoader`（API 26）从 `ByteBuffer` 直接加载 dex，不落盘；`DelegateLastClassLoader`（API 27）先查自己的 dex，再查父加载器，适合“插件优先”的隔离策略。它们解决的是同一件事的不同侧面，后面会展开。

![类加载器链与插件的挂载位置](/images/android-pluginization/02-classloader.webp)

### 4.2 父加载器决定一切

`DexClassLoader` 的第四个参数是父加载器。选谁，直接决定了插件的类可见性和冲突形态。

如果父加载器是宿主的 `PathClassLoader`，那么插件加载任何类时，都会先问宿主。宿主有，就用宿主的；宿主没有，才去插件 dex 里找。这叫“宿主优先”。好处是插件可以直接用宿主的类，包括宿主提供的 API、工具类、已经初始化的单例；坏处是插件自己带的同名类会被宿主盖掉。插件里打包了 OkHttp 4.x，宿主用的是 3.x，插件代码跑起来可能撞上 `NoSuchMethodError`，而编译期一点问题都没有。

如果父加载器是 `BootClassLoader`，插件只能看见系统类，看不见宿主。这叫“完全隔离”。好处是插件内部的依赖版本完全自主，坏处是插件没法直接调用宿主的能力，所有交互都得走 Binder、AIDL 或者宿主暴露的、由系统加载器加载的接口。

`DelegateLastClassLoader` 是折中：插件自己的 dex 优先，父加载器兜底。它适合“插件想用自己的依赖，但仍然需要系统类和少量宿主类”的场景，代价是宿主和插件之间的类型身份更容易错位。

Shadow 走的是第四条路：自定义 `PluginClassLoader`，在 `loadClass()` 里做包名白名单。白名单里的宿主包，走父加载器（宿主优先）；`com.tencent.shadow.core.runtime` 下的类，从 loader 自己的 ClassLoader 加载；其余的，先让一个 `specialClassLoader`（通常是宿主加载器的父级）试，失败后再从插件 dex 里找。这样插件既能用宿主明确开放的 API，又不会被宿主里碰巧同名的类劫持。

这段逻辑值得抄下来，因为它把“隔离”和“共享”从一个二选一的问题，变成了一个按包名分配的策略问题：

```kotlin
override fun loadClass(className: String, resolve: Boolean): Class<*> {
    findLoadedClass(className)?.let { return it }

    // 插件依赖的 Shadow runtime 类，从 loader 加载
    if (className.substringBeforeLast('.') == "com.tencent.shadow.core.runtime") {
        return loaderClassLoader.loadClass(className)
    }
    // 白名单里的宿主类，走双亲委派
    if (className.inPackage(hostWhiteTrie)) {
        return super.loadClass(className, resolve)
    }
    // 其余类：先试 specialClassLoader，再找插件自己的 dex
    return try {
        specialClassLoader.loadClass(className)
    } catch (e: ClassNotFoundException) {
        findClass(className)
    }
}
```

（上面是删掉部分异常处理后的主干，`inPackage` 是 Shadow 内部的包名前缀匹配；完整实现见 Shadow 的 `PluginClassLoader.kt`。）

### 4.3 类身份：两个加载器就是两个世界

Java 里，一个类的身份是“加载器 + 全限定名”。同一个 `com.example.Foo`，被两个 ClassLoader 各加载一次，就是两个不同的类。它们的静态字段不共享，`instanceof` 互相为 false，强转会抛 `ClassCastException`。

这条规则是插件化里大多数“玄学问题”的根源：

- 宿主和插件各有一份 Gson，插件把宿主传过来的对象反序列化，得到的是插件加载器里的 `Foo`；宿主拿到后强转成自己的 `Foo`，崩了。
- 插件里用了宿主的某个单例，但插件自己也打包了一份这个单例的类，于是插件里 `getInstance()` 拿到的是另一个实例，状态对不上。
- 插件通过 Binder 把对象传给宿主，宿主反序列化时用的是宿主的 ClassLoader，插件自定义的 `Parcelable` 类找不到，抛 `ClassNotFoundException`。

解决思路只有两类：**让两边用同一个类**，或者**让跨边界的对象不依赖类身份**。前者靠“接口下沉 + 宿主白名单 + 插件 `compileOnly` 依赖宿主 API”；后者靠 Binder、JSON、`Bundle` 里的基础类型、`Parcelable` 的显式类加载器设置。没有第三条路。

Shadow 的示例工程里，插件用 `compileOnly` 依赖宿主的 lib，打包时不带进去；运行时通过白名单让插件加载到宿主的同一份类。这个做法很有效，但它要求宿主和插件对“哪些包算公共 API”有严格的约定。一旦白名单写宽了，插件就能看见宿主的内部实现，隔离名存实亡；写窄了，插件编译不过。

### 4.4 Android 14 之后：动态加载的文件必须只读

从 Android 14（API 34）开始，targetSdk ≥ 34 的应用如果动态加载 DEX/JAR/APK，文件必须是只读的，否则系统抛 `SecurityException`。这不是建议，是运行时检查。

做法本身很简单，难的是时机：

```kotlin
val dir = File(context.filesDir, "plugins/${plugin.version}")
val apk = File(dir, "plugin.apk")

// 先完成下载、校验、解压，再设置只读；设置之后不能再写
downloadTo(apk)
verifySha256(apk, expectedHash)
check(apk.setReadOnly())

val loader = DexClassLoader(apk.path, null, File(dir, "lib").path, hostLoader)
```

几个容易踩的点：

- **只读要在加载之前设置，而且要在所有写操作之后设置。** 常见错误是“先加载，再补一个 `setReadOnly()`”，检查发生在加载那一刻，补晚了没用。
- **升级要换文件，不要原地覆盖。** 只读文件没法覆盖，正确做法是新版本下载到新目录，校验通过后原子切换指针，旧版本留给回滚。
- **`setReadOnly()` 的返回值要检查。** 在某些文件系统或路径上它可能失败；失败时不要继续加载。
- **Android 17 把这条规则扩展到了 native 库。** targetSdk ≥ 37 的应用，用 `System.load()` 加载的 `.so` 也必须是只读的。插件 so 的下载、解压、权限处理要按同一套流程走，别只盯着 dex。

这条限制的意图很明确：降低“应用在用户不知情时替换可执行代码”的风险。它没有禁止插件化，但把“从可写目录直接加载”这种最省事的实现方式堵死了。对正经做插件分发的团队，这只是一次安装流程的调整；对靠内存加载绕过检查的方案，它的收益也有限，成本却不低。

### 4.5 内存加载与“加固”不是一回事

`InMemoryDexClassLoader` 可以直接从内存里的 `ByteBuffer` 加载 dex，不经过文件。很多加固/壳方案用它来避免 dex 落盘，插件框架偶尔也会用它来减少 IO。

它不是一个免费的绕过手段：

- 内存占用更高。dex 要完整读进内存，大插件会明显增加 PSS；
- 没有 odex/vdex 缓存，冷启动更慢；
- 调试和崩溃归因更难，堆栈里看到的是内存地址；
- 它不改变合规性质。下发的仍然是可执行代码，商店政策不会因为“没落盘”就放行；
- 从机制上看，Android 14 的只读要求针对文件加载路径，内存加载绕开了这项检查；但它也绕开了系统的校验和优化，属于用稳定性换灵活性。

如果目的是“防止插件被逆向”，内存加载的收益很低，成本很高。真要保护知识产权，签名校验、代码混淆、关键逻辑下沉到 native、服务端风控，比“把 dex 读进内存”更实际。

## 5. 资源：给第二个 APK 配一套 Resources

### 5.1 Resources 是怎么绑上 APK 的

一个应用进程启动时，`ResourcesManager` 会为它创建 `Resources` 对象。`Resources` 内部持有 `AssetManager`、`DisplayMetrics`、`Configuration`，`AssetManager` 里记录着 APK 的路径和 `resources.arsc` 的位置。`getString()`、`getDrawable()`、`inflate()` 最终都走到 `AssetManager` 去查表。

插件的 APK 不在这个路径列表里，所以默认查不到。主流有三条路：往 `AssetManager` 里加路径、给 `Resources` 加一个资源提供者、或者让系统在创建 `Resources` 时就把插件 APK 当成“共享库”加进去。三条路之外还有低版本的包装方案，后面一并说。

在讲这三条路之前，先要理解资源 ID。

### 5.2 资源 ID 的命名空间

Android 的资源 ID 是一个 32 位整数，结构是 `0xPPTTEEEE`：

```text
0x7f  0x02  0x0a3f
 │     │     └── entry：资源在该类型里的序号
 │     └──────── type：string、drawable、layout……
 └────────────── package：0x01 是系统，0x7f 是普通应用
```

宿主和插件如果都用 `0x7f`，两套资源的 ID 会落在同一个命名空间里。`Resources` 拿到 `0x7f020a3f` 时，无法知道该去宿主表还是插件表查；AAPT 在编译期也可能报重复资源。所以插件必须换一个 package 段。

![资源 ID 的包段与三条加载路线](/images/android-pluginization/03-resources.webp)

`aapt2` 提供了 `--package-id` 参数：

```groovy
android {
    aaptOptions {
        // minSdk > 26：用大于 0x7f 的段，例如 0x80
        additionalParameters "--package-id", "0x80"
        // minSdk <= 26：改成小于 0x7f 的段，并加 --allow-reserved-package-id
        // additionalParameters "--package-id", "0x7e", "--allow-reserved-package-id"
    }
}
```

（AGP 8 以后推荐写成 `androidResources.additionalParameters`，参数本身不变。）

这里有一条不太直观的规则：**minSdk 大于 26 时，插件 package id 必须大于 `0x7f`；minSdk 小于等于 26 时，必须小于 `0x7f`，并且加 `--allow-reserved-package-id`。** 原因是低版本系统对大于 `0x7f` 的包段有 bug，AAPT 会做兼容处理；Shadow 的 Gradle 插件在 `ShadowPlugin.kt` 里直接检查这两个条件，配错就构建失败。

宿主保持 `0x7f`。minSdk 大于 26 时，插件分配 `0x80`、`0x81`……；minSdk ≤ 26 时分配 `0x7e` 这类小于 `0x7f` 的段。多个插件各自用不同的段，互不冲突；同一个插件升级时保持段不变，避免资源 ID 变化导致缓存失效。

### 5.3 三条加载路线

**路线一：反射 `AssetManager.addAssetPath()`。**

这是最老、最常见的做法：

```kotlin
val assetManager = AssetManager::class.java.newInstance()
val addAssetPath = AssetManager::class.java.getMethod("addAssetPath", String::class.java)
addAssetPath.invoke(assetManager, pluginApkPath)

val resources = Resources(
    assetManager,
    hostResources.displayMetrics,
    hostResources.configuration
)
```

它短、直接、在大多数设备上能跑。问题在于 `addAssetPath` 是隐藏 API，`AssetManager` 的构造器也是。Android 9 开始限制非 SDK 接口，反射会收到警告甚至异常；不同 ROM 对 `AssetManager` 的实现也有差异。它能用，但它是“借后门”，每次系统升级都要重新验证。Android Lint 会对这类调用给出 `DiscouragedPrivateApi` 警告，这不是多管闲事。

**路线二：`ResourcesProvider` + `ResourcesLoader`（API 30+）。**

Android 11 引入了 `android.content.res.loader` 包，提供了公开的资源覆盖机制。`ResourcesProvider` 可以从资源表或 APK 创建，`ResourcesLoader` 负责把 provider 挂到 `Resources` 上：

```kotlin
val provider = ResourcesProvider.loadFromTable(resourceTablePfd, null)
val loader = ResourcesLoader()
loader.addProvider(provider)
resources.addLoaders(loader)
```

`loadFromTable` 需要 `resources.arsc` 的文件描述符；后续版本又补了直接从 APK 创建 provider 的入口（`loadFromApk`）。这条路是公开 API，不依赖反射，也不会踩 hidden API 名单，缺点是版本门槛高（API 30），而且对“插件 APK 整体作为资源源”的支持在不同版本上不完全一致。它更适合“给已有 Resources 叠加一层资源”的场景，比如换肤、动态主题，而不是完整的插件资源隔离。

**路线三：资源分区 + `sharedLibraryFiles`（Shadow 在 API 28+ 的做法）。**

Shadow 没有走前两条路。它在构造插件 `Resources` 时，复制宿主的 `ApplicationInfo`，把宿主 APK 留在 `sourceDir`，把插件 APK 塞进 `sharedLibraryFiles`，然后调用 `PackageManager.getResourcesForApplication()`：

```kotlin
val applicationInfo = ApplicationInfo(hostApplicationInfo)
applicationInfo.publicSourceDir = hostApplicationInfo.publicSourceDir
applicationInfo.sourceDir = hostApplicationInfo.sourceDir
applicationInfo.sharedLibraryFiles = arrayOf(
    *hostApplicationInfo.sharedLibraryFiles.orEmpty(),
    pluginApkPath
)

val pluginResources = packageManager.getResourcesForApplication(applicationInfo)
```

`sharedLibraryFiles` 是系统用来加载“共享库 APK”的字段，WebView、地图 SDK 这类“APK 里带资源”的组件就用它。系统会把其中的 APK 作为独立的资源分区加进同一个 `Resources`，资源 ID 保持自己的包段。Shadow 的注释写得很清楚：放进 `sharedLibraryFiles` 的 APK，资源 ID 包段必须改成 `0x80` 或更大，否则加载后包段会被重映射成一个随机值。这就是前面那条 package id 规则的另一个理由。

这里有一个实际接入时容易踩的边界：“`0x80` 或更大”是按 API 28+ 的 `sharedLibraryFiles` 路线说的；而 minSdk ≤ 26 时，Shadow 的构建检查又要求插件段小于 `0x7f`。Shadow 的样例工程用 minSdk 16 + `0x7E`，用的是低 minSdk 的包段；如果同一个包要同时覆盖新旧系统，资源段怎么选、运行时走哪条路线，需要按目标设备实测，不能只看构建通过。

这条路的好处是：不反射、不碰隐藏 API、资源 ID 稳定、宿主和插件资源在同一个 `Resources` 里，`getIdentifier()` 和主题解析都能正常工作。代价是它依赖 `ApplicationInfo.sharedLibraryFiles` 这个字段的语义，属于“用公开 API 拼出非典型用法”，系统行为变化时要跟着验证。

API 27 及以下没有这套机制，Shadow 退回 `MixResources`：用已废弃的 `Resources` 构造器创建一个包装对象，先查插件资源，再回落到宿主资源。代码里对这个 fallback 有明确注释，也说明了为什么不首选它——`Resources` 的构造器已经标记 `@Deprecated`，未来可能不可用。

| 方案 | 最低版本 | 是否反射 | 资源 ID 稳定性 | 主要风险 |
|---|---|---|---|---|
| `AssetManager.addAssetPath` | 全部 | 是 | 取决于调用方 | hidden API、ROM 差异 |
| `ResourcesProvider` / `ResourcesLoader` | API 30 | 否 | 好 | 版本覆盖、API 语义差异 |
| `sharedLibraryFiles` + `getResourcesForApplication` | API 28（Shadow 实现） | 否 | 好（新版路线要求 > 0x7f） | 依赖字段语义；minSdk ≤ 26 时与 AAPT 规则冲突 |
| `MixResources` 包装 | API 27 及以下 | 部分 | 一般 | 依赖废弃构造器 |

### 5.4 主题、LayoutInflater 与 Context

资源加载通了，不代表界面就对了。插件 Activity 的主题、`LayoutInflater`、`Context` 都可能把资源解析回宿主。

**主题。** 插件的 `AndroidManifest.xml` 里可以声明 `android:theme`，但这个 manifest 不会被系统安装，主题得在运行时手动设置。Shadow 在创建 `ShadowApplication` 时读取 `pluginManifest.applicationTheme`，在初始化插件 Activity 时通过 `setTheme()` 把主题应用到容器 Activity 上。宿主的容器 Activity 通常注册成透明主题，避免启动时闪一下白底，再让插件的主题接管内容。

**LayoutInflater。** `LayoutInflater.from(context)` 会用 `context.getResources()` 去解析布局。如果 `context` 是宿主 Context，插件布局里的自定义 View、主题属性、资源引用就可能解析到宿主资源。Shadow 的 `ShadowContext`、`SubDirContextThemeWrapper`、`ShadowLayoutInflater` 就是为这件事准备的：让插件的 `Context` 返回插件的 `Resources`，让 inflater 用插件的 ClassLoader 去实例化 View。

**configChanges。** Shadow 的容器 Activity 在宿主 manifest 里必须把 `configChanges` 写全（`orientation`、`screenSize`、`smallestScreenSize`、`fontScale`、`uiMode`、`locale`……）。少写一个，转屏或切换深色模式时系统会重建容器 Activity，插件的状态就可能丢。这不是框架的怪癖，而是代理模式的必然：系统只认识容器，容器的重建策略必须覆盖插件可能遇到的所有配置变化。

### 5.5 资源相关的崩溃长什么样

资源问题通常不会在编译期暴露，而是在运行时以几种固定的形式出现：

- `Resources$NotFoundException: Resource ID #0x7f0a0123`：ID 是宿主的，但代码在插件里查；或者插件用了宿主的 `R` 类。
- `Resources$NotFoundException: String resource ID #0x0`：资源没找到，代码把 `0` 当成了有效 ID。
- `InflateException` 里套着 `ClassNotFoundException`：布局里的自定义 View 是插件类，但 inflater 用了宿主的 ClassLoader。
- 主题属性解析成宿主的值：`?attr/colorPrimary` 在插件的主题里没定义，回落到宿主主题。
- `Resources.NotFoundException` 只在某个密度/语言下出现：插件只打了一份资源，或者 split 没覆盖到。

排查时，先打印资源的真实名字，而不是只看 ID：

```kotlin
resources.getResourceName(resId)          // 包名:类型/名字
resources.getResourcePackageName(resId)   // 资源属于哪个包段
```

再看 `AssetManager` 里到底挂了哪些路径（反射 `getAssetPaths()`，或者直接在调试器里看，不同版本可能不一样），基本就能定位是“没挂上”还是“挂错了”。

## 6. 组件：最难的一堵墙

类加载和资源加载都是“进程内”的问题，只要肯写代码，总能解决。组件不是。组件的生命周期由 `system_server` 驱动，跨进程、跨线程，而且每一步都有检查。

![三种组件方案：Hook、占坑、代理](/images/android-pluginization/04-strategies.webp)

### 6.1 系统是怎么启动一个 Activity 的

先把链路压缩成一句话：`startActivity()` 经过 Binder 到 `system_server` 的 `ActivityTaskManagerService`，ATMS 校验、建 `ActivityRecord`、决定 Task，然后通过 `ApplicationThread` 把 `LaunchActivityItem` 发回应用进程；应用进程的 `ActivityThread` 收到事务，用 `Instrumentation.newActivity()` 反射实例化 Activity，再依次回调 `onCreate`、`onStart`、`onResume`。

这条链路上，插件会在两个地方被拦住：

1. **ATMS 校验**：目标 Activity 不在 `PackageManager` 的组件表里，`ActivityStarter` 直接抛 `ActivityNotFoundException` 或 `SecurityException`；
2. **实例化**：即使骗过了 ATMS，`Instrumentation` 拿到的类名还是插件的，宿主的 ClassLoader 找不到这个类，抛 `ClassNotFoundException`。

Hook 派和代理派，就是分别针对这两个拦截点做文章。

### 6.2 路线一：Hook 系统

Hook 的核心是“在系统调用链上换掉一个对象”。常见的有三个位置：

**替换 `Instrumentation`。** `ActivityThread` 里有一个 `mInstrumentation` 字段，负责实例化 Activity、调用生命周期。把它换成一个包装类，在 `execStartActivity()` 里把插件的 `Intent` 换成宿主已注册的占坑 Activity，在 `newActivity()` 里用插件的 ClassLoader 加载真正的 Activity 类。VirtualAPK 的 `VAInstrumentation` 就是 `extends Instrumentation implements Handler.Callback`，同时接管了 Activity 启动和 Handler 消息。

**替换 `ActivityThread.mH` 的 `Callback`。** `mH` 是主线程的 `Handler`，`LAUNCH_ACTIVITY`、`EXECUTE_TRANSACTION` 这些消息都从这里过。给它设置一个 `Callback`，在消息分发前把 `ActivityClientRecord` 里的 `activityInfo`、`intent` 换成插件的，再交给原 Handler。Android 9 之后，生命周期被包装成 `ClientTransaction` + `TransactionExecutor`，Hook 点也跟着从 `LAUNCH_ACTIVITY` 变成了 `EXECUTE_TRANSACTION`。

**代理 `IActivityManager` / `IActivityTaskManager`。** 通过动态代理替换 `ActivityManager.getService()` 返回的 Binder 代理，在 `startActivity()` 里改包名、改组件名、改 `userId`。DroidPlugin 把这条路走到了极致，AMS、PMS、`IContentProvider`、`INotificationManager` 统统代理了一遍。

Hook 方案的优点很直接：插件几乎不用改，第三方 APK 丢进来就能跑；组件、生命周期、任务栈看起来都和真应用一样。缺点是维护成本随 Android 版本指数增长：

- 被 Hook 的字段可能改名、改类型、被移除；
- 方法签名可能变，`execStartActivity` 在不同版本上的参数个数就不一样；
- 非 SDK 接口限制会拦截反射，Android 9 之后灰名单逐步收紧；
- 厂商 ROM 会改 `ActivityThread`、`Instrumentation` 的实现，AOSP 上能跑的 Hook，在某个定制系统上可能直接崩；
- Hook 失败通常发生在系统进程边界，堆栈难看，线上归因困难。

早期框架用 `Unsafe`、双重反射、`HiddenApiBypass` 之类的办法绕过限制。这些办法今天仍然存在，但它们是在和平台的兼容性策略对抗，不是稳定的工程基础。

### 6.3 路线二：占坑 + 换 ClassLoader

RePlugin 把自己定位成“只 Hook 一个点”的方案：它不代理 AMS，也不改 `Instrumentation`，而是把 `LoadedApk.mClassLoader` 换成一个自定义的 `RePluginClassLoader`，再在宿主 manifest 里预注册一批“坑位” Activity。

它的工作方式大致是：

1. 宿主启动时，`RePluginClassLoader` 替换 `LoadedApk.mClassLoader`，成为应用加载类的入口。它继承 `PathClassLoader`，把原加载器的 `pathList` 浅拷贝过来，让系统以为用的还是原来那个加载器；
2. 启动插件 Activity 时，框架向系统申请一个坑位，Intent 的目标是宿主已注册的坑位 Activity，插件的真实组件名放在 Intent extra 里；
3. 系统按坑位类名去加载类，`RePluginClassLoader.loadClass()` 先问插件管理器；管理器根据坑位映射表找到目标插件 Activity，用插件的 ClassLoader 加载并返回这个类——系统以为自己加载的是坑位，实际拿到的是插件类；
4. 生命周期回调发给这个实例，坑位在 `onCreate` 里校验 Intent 目标和当前对象是否一致，不一致就按异常恢复流程处理。

这套方案的关键是“坑位”要覆盖 launchMode、process、taskAffinity、theme、configChanges 等组合。RePlugin 按 launchMode、透明/不透明主题、横竖屏等维度预注册坑位，数量可以在 `HostConfigHelper` 里配；新插件只要能用现成的坑位，就不需要升级宿主。它宣称只 Hook 了 ClassLoader 一个点、没有 Binder Hook，支持静态广播、TaskAffinity、自定义主题、AppCompat、DataBinding，兼容到 API 9。这些能力在 2017 年是非常有竞争力的。

代价同样明显：manifest 体积膨胀；坑位组合不可能穷尽，遇到特殊 launchMode 或 `documentLaunchMode` 就要加坑；`mClassLoader` 是隐藏字段，替换它仍然依赖反射；插件和宿主的类隔离靠 ClassLoader 的委派顺序，边界不如代理方案清晰。

### 6.4 路线三：代理（Shadow）

Shadow 的思路和前两条完全不同：它不骗系统，也不换系统的对象，而是让系统启动一个**真实注册在宿主里的容器 Activity**，再由容器把生命周期转交给插件 Activity。

![Shadow 的容器 Activity 与插件 Activity](/images/android-pluginization/05-shadow-activity.webp)

> 系统只认识 `PluginContainerActivity`；`ShadowActivity` 通过 `HostActivityDelegator` 把调用转回容器，`super` 调用也一样。

宿主 manifest 里注册的是这样的组件：

```xml
<service
    android:name=".MainPluginProcessService"
    android:process=":plugin" />

<activity
    android:name="com.tencent.shadow.sample.runtime.PluginDefaultProxyActivity"
    android:launchMode="standard"
    android:configChanges="mcc|mnc|locale|touchscreen|keyboard|keyboardHidden|navigation|screenLayout|fontScale|uiMode|orientation|screenSize|smallestScreenSize|layoutDirection"
    android:hardwareAccelerated="true"
    android:theme="@style/PluginContainerActivity"
    android:process=":plugin" />

<activity
    android:name="com.tencent.shadow.sample.runtime.PluginSingleTask1ProxyActivity"
    android:launchMode="singleTask"
    ... />

<provider
    android:authorities="com.tencent.shadow.contentprovider.authority.dynamic"
    android:name="com.tencent.shadow.core.runtime.container.PluginContainerContentProvider" />
```

这些容器 Activity 是货真价实的宿主组件，系统启动它们不需要任何特殊处理。插件 Activity 不继承 `android.app.Activity`，而是在编译期被 Shadow 的 Gradle 插件用 ASM/Javassist 改写成继承 `ShadowActivity`：

```kotlin
class ActivityTransform : SimpleRenameTransform(
    mapOf(
        "android.app.Activity" to "com.tencent.shadow.core.runtime.ShadowActivity",
        "android.app.NativeActivity" to "com.tencent.shadow.core.runtime.ShadowNativeActivity"
    )
)
```

`ShadowActivity` 并不是 `Activity` 的子类。它继承 `ShadowContext`（本质是一个 `ContextThemeWrapper`），用组合的方式持有容器 Activity 的引用，并实现了 `Activity` 的一大票方法。插件代码被改写成 `extends ShadowActivity` 之后，`onCreate()`、`setContentView()`、`startActivity()` 这些调用都落在 Shadow 的实现上，再由它转给真正的容器。真正被系统 attach 的是 `PluginContainerActivity`；容器持有一个 `HostActivityDelegate`，delegate 再持有插件 `ShadowActivity` 实例。系统调用容器的 `onCreate()`，容器转给 delegate，delegate 转给插件 Activity；插件 Activity 里调用 `super.onCreate()` 时，`super` 方法被重定向回容器的 `superOnCreate()`，最终落到真正的 `Activity` 实现上。

这条链看起来绕，但它解决了一个 Hook 方案很难解决的问题：**系统看到的永远是一个合法组件，插件 Activity 只是容器里的一个“委托对象”**。没有反射替换，没有 Binder 代理，没有 hidden API。系统升级改了 `ActivityThread` 的内部字段，只要 `Activity` 的公开 API 不变，Shadow 就不用跟着改。

代价转移到了编译期和 API 覆盖面上：

- 插件必须用 Shadow 的 Gradle 插件构建，产物经过 transform；源码仍然可以独立运行，但打包成插件时字节码已经变了；
- `ShadowActivity` 要覆盖插件可能用到的每一个 `Activity` 方法。Shadow 的 `ActivityCodeGenerator` 把方法分成三类：系统回调、插件调用、私有方法，分别生成委托代码；没覆盖到的方法会在运行时抛 `Method Not Found`，需要补；
- 容器 Activity 的数量和类型要预先规划。`standard`、`singleTask`、`singleInstance` 各注册几个，launchMode 不匹配时行为会退化；
- `onActivityResult`、`savedInstanceState`、配置变更、进程重建都要在容器和插件之间做映射，任何一处遗漏都会变成“偶现”的 bug；
- 除了 `Activity`，`Application`、`Service`、`ContentProvider`、`Fragment`、`Dialog`、`PackageManager`、`PendingIntent`、`LayoutInflater`、`WebView` 也都有对应的 transform。Shadow 的 `transform/specific` 目录下有近二十个这样的规则，这就是“零反射”的真实成本：把运行时的兼容性工作，搬到了编译期和框架代码里。

Atlas 走的是另一条路。它把自己定位成“组件化框架”：用 Bundle 做代码和资源的拆分，靠定制过的 aapt 和 dexmerge 做加载与更新，而不是把插件当成一个独立 APK、再借宿主组件跑起来。它也不是多进程框架。这条路更接近“把模块化做到极致，再补上动态更新”，和 Shadow 的容器代理不是同一类方案。

### 6.5 Service：从 Hook 到 PPS

Service 的难点和 Activity 类似，但少了 UI 和 Task，多了进程和前台状态。

Hook 方案通常代理 `IActivityManager.startService()` / `bindService()`，把插件的 `Service` 换成宿主已注册的 `Service`，再在 `ActivityThread` 的 `handleCreateService()` 里把 `ServiceInfo` 换成插件的。VirtualAPK 的 `LocalService` 就是用反射调用 `Service.attach()` 来手工创建插件 Service 的。

Shadow 用了一个更“实体”的设计：`PluginProcessService`（PPS）。PPS 是宿主 manifest 里真实注册的 Service，跑在 `:plugin` 进程；插件 Service 不是系统创建的，而是由 PPS 进程里的 loader 创建，生命周期由 Shadow 驱动。插件要 `startService()` 时，调用会被路由到 PPS，由它在本进程内实例化插件 Service 并回调 `onCreate`/`onStartCommand`；跨进程的 `bindService()` 则通过 Binder 把 `IBinder` 传回调用方。

PPS 的好处是：系统只看到一个宿主 Service，插件 Service 的创建、销毁、进程归属都在框架控制之下；插件进程崩溃不会带走宿主主进程。代价是：插件 Service 的 `ServiceInfo`、权限、前台服务类型都要在宿主侧有对应的声明或映射；`startForeground()` 需要宿主容器 Service 承载；`bindService()` 的 `IBinder` 跨进程传递要处理类型和生命周期。

### 6.6 BroadcastReceiver：静态注册最麻烦

动态注册的 Receiver 相对简单：插件在自己的 `Context` 里调用 `registerReceiver()`，传入的 `IntentFilter` 和 `BroadcastReceiver` 都是插件对象，系统只认宿主进程，不关心 Receiver 类从哪来。

静态注册就麻烦了。`<receiver>` 要在 manifest 里声明，安装时被 `PackageManager` 解析；系统在广播到来时，按组件表找到 Receiver，启动进程、实例化、回调。插件没安装，组件表里没有它，静态 Receiver 永远不会被触发。

Hook 方案的做法是代理 `PackageManager` 的查询，或者在 `ActivityThread` 的 `LoadedApk.receivers` 里塞入插件的 Receiver 信息。Shadow 的做法是把插件的 Receiver 信息从插件 manifest 里解析出来，交给 `ShadowApplication` 保管，在需要时动态注册；它不试图让系统“以为”插件 Receiver 是静态注册的，而是接受“插件 Receiver 只能在进程存活时工作”这个前提。

Android 13（API 33）之后，动态注册非系统广播必须显式指定 `RECEIVER_EXPORTED` 或 `RECEIVER_NOT_EXPORTED`；Android 14 把要求扩展到所有动态注册。插件框架在注册插件 Receiver 时也要跟着传 flag，否则在 targetSdk 升级后直接崩。这类“平台行为变更传导到框架”的连锁反应，是插件化维护成本的典型来源。

### 6.7 ContentProvider：authority 是全局唯一的

ContentProvider 的特殊之处在于 authority。`ContentResolver` 通过 authority 找到 Provider，authority 在整个系统里必须唯一；插件如果用自己的 authority，系统里没有这个 Provider，查询直接失败。

插件化的做法通常有两种：

- **预注册容器 Provider**：宿主注册一个 `PluginContainerContentProvider`，用一个固定的 authority；插件的 Provider 调用被路由到这个容器，容器再按插件分包。Shadow 走的是这条路，配合 `UriConverter` 在编译期把插件里的 `Uri.parse()` 重定向，运行时代码里的 URI 会被改写成容器 authority + 插件标识。
- **Hook `IContentProvider`**：代理 `ContentResolver.acquireProvider()` 或 `ActivityThread.installProvider()`，把插件的 Provider 塞进 `ActivityThread.mProviderMap`。VirtualAPK 的 `PluginContentResolver` 属于这一类。

两条路都要处理 `exported`、权限、多进程初始化、`ContentProvider.onCreate()` 的时机。插件 Provider 不会像已安装应用那样在进程启动时被系统自动创建，框架要自己决定什么时候调用它的 `onCreate()`；调用早了，插件还没加载完；调用晚了，第一次 `query()` 会变慢。

### 6.8 Application：一个不被系统认识的 Application

插件的 `Application` 不会被系统创建，因为系统只知道宿主的 `Application`。框架要自己实例化插件的 `Application` 类，设置它的 `Context`、`Resources`、`ClassLoader`，然后按顺序调用 `attachBaseContext()` 和 `onCreate()`。

Shadow 的 `CreateApplicationBloc` 把顺序写得很清楚：先设置资源、ClassLoader、`ApplicationInfo`、`ComponentLauncher`，最后才 `attachBaseContext()`。这个顺序不能反：`attachBaseContext()` 里插件可能会访问资源或类加载器，提前调用会拿到空值。

插件 `Application` 的 `onCreate()` 通常由 loader 在加载插件时调用一次，而不是每次启动 Activity 都调用。它和宿主 `Application` 的初始化顺序、`ContentProvider` 的初始化顺序都不一样，插件里的第三方 SDK 如果依赖“Application 一定先于 Provider”这个假设，可能会出问题。

| 组件 | Hook 方案 | 占坑方案 | 代理方案（Shadow） | 主要难点 |
|---|---|---|---|---|
| Activity | 替换 Instrumentation / mH | 坑位 + ClassLoader 替换 | 容器 Activity + 生命周期委托 | 生命周期、Task、launchMode |
| Service | 代理 IActivityManager | 坑位 Service | PPS + 进程内实例化 | 进程、前台服务、Binder |
| Receiver | 代理 PackageManager / 动态注册 | 动态注册为主 | 解析后动态注册 | 静态注册、导出 flag |
| Provider | 代理 IContentProvider | 容器 Provider | 容器 Provider + URI 转换 | authority、权限、初始化时机 |
| Application | 手动创建 | 手动创建 | ShadowApplication | 初始化顺序、Context |

## 7. 进程与沙箱：当你要跑别人的 APK

前面讲的都是“你控制插件代码”的场景。还有一类更激进的方案：不要求插件改造，直接运行一个未安装的第三方 APK。这就是应用虚拟化，代表是 VirtualApp。

![VirtualApp 的三层结构与进程模型](/images/android-pluginization/07-virtualapp.webp)

VirtualApp 把自己定位成“轻量级 Android 虚拟机”。它的架构分三层：

- **VA Space**：一个隔离的虚拟空间，用来“安装”要运行的 APK；
- **VA Framework**：夹在 Android Framework 和虚拟 App 之间，代理系统服务调用。虚拟 App 请求 `PackageManager`、`ActivityManager`、`ContentProvider` 时，VA 把参数里的包名、uid、组件名改成宿主的，系统校验通过后，再把结果还原成虚拟 App 的视角；
- **VA Native**：做 IO 重定向和 native Hook。虚拟 App 里写死的 `/data/data/<pkg>` 路径要重定向到 VA 内部的目录；有些 JNI 层的调用没法在 Java 层拦截，只能在 native 层做。

它需要 5 类进程：VA Host Main（宿主主进程）、VA Host Plugin（64 位插件包进程）、VAPP Client（虚拟 App 进程）、VAServer（处理安装等不交给系统的请求）、CHILD（宿主集成的其他进程）。为了同时支持 32 位和 64 位应用，VA 需要两个 APK：主包和插件包，一个 32 位一个 64 位，因为一个进程只能运行在一种 ABI 模式下。

这套东西的工程量，比“加载一个插件 APK”大一个数量级。它要虚拟化的不只是类加载和资源，还有：

- 安装：`PackageManager` 的 `getPackageInfo()`、`getInstalledPackages()`、`resolveActivity()` 都要返回虚拟 App 的信息；
- 组件：Activity、Service、Provider、Receiver 的启动、生命周期、任务栈；
- 存储：`/data/data/<pkg>`、外部存储、`SharedPreferences`、数据库路径；
- 设备标识：`Settings.Secure`、`TelephonyManager`、`Build` 字段；
- 权限：宿主申请的权限，虚拟 App 以为是自己申请的；
- 通知、`PendingIntent`、账号、`ContentProvider` authority。

VirtualApp 的开源仓库 README 写得很直白：GitHub 上的代码在 2017 年 12 月停止更新，商业版在持续维护，商业版支持到 Android 14。开源版本对现代 Android 的适配有限，想直接拿来做产品，基本要自己重做大量工作。

更重要的是，虚拟化和插件化的风险模型不同。插件化加载的是你自己的代码，出了问题你能改；虚拟化运行的是别人的 APK，那个 APK 可能在你不知情的时候读取用户数据、上传通讯录、弹广告。Google Play 对“在设备上运行 Android 容器”的应用有专门要求：容器必须尊重目标应用声明的 `REQUIRE_SECURE_ENV` 标记，不能绕过 `FLAG_SECURE`，不能帮用户规避应用的保护。这类产品在应用商店的审核风险，远高于普通插件化。

如果只是想让插件跑在独立进程、崩溃不拖垮宿主，不需要 VirtualApp 这么重。宿主注册一个 `android:process=":plugin"` 的容器 Activity/Service，把插件限制在这个进程里，就够解决大部分稳定性问题；跨进程通信走 Binder，数据走 `Parcelable`/JSON。代价是插件和宿主的对象不能直接共享，所有交互都要序列化。

## 8. 一次插件启动的完整时序

把前面的东西串起来，看一次真实的插件启动。以 Shadow 的加载流程为例，宿主从收到“打开插件页面”的请求开始，大致会经历下面这些步骤。

![从插件包到首帧的加载时序](/images/android-pluginization/06-load-sequence.webp)

> 1~8 步都发生在插件真正显示之前；第 9 步的首帧，才是用户看到的“打开”。

**第 1 步：下载与校验。** 宿主从 CDN 拿到 `plugin-release.zip`，里面通常包含插件 APK、loader APK、runtime APK 和一份配置。校验签名、SHA-256、最低宿主版本、ABI 匹配。这一步最容易出问题：CDN 返回了错误页、zip 损坏、签名不匹配、插件和宿主版本不兼容，都会在这里失败。失败要能降级到宿主内置的兜底页面，而不是白屏。

**第 2 步：解压与安装。** 把 zip 解到应用私有目录，插件 APK 放到带版本号的目录，`.so` 解到独立的 lib 目录。API 34+ 要把这些文件设成只读；API 27 以下还要预生成 odex。安装完成后记录版本、路径、哈希，写进一个可原子切换的清单。

**第 3 步：创建 ClassLoader。** 用插件 APK 路径、lib 目录、父加载器创建 `PluginClassLoader`。父加载器是宿主加载器，白名单决定插件能看见哪些宿主类。这一步的失败通常是 `IOException`（文件被占用/不存在）或 `SecurityException`（文件可写）。

**第 4 步：解析插件 manifest。** 通过插件 ClassLoader 加载 `PluginManifest`，拿到包名、Application 类名、主题、四大组件列表、权限。Shadow 会检查插件包名是否和宿主一致——不一致直接拒绝加载，原因在 9.1 节展开。

**第 5 步：构造 ApplicationInfo。** 复制宿主的 `ApplicationInfo`，把 `sourceDir` 指向插件 APK，`nativeLibraryDir` 指向插件 lib 目录，`dataDir` 指向按业务名隔离的目录，再设置包名、Application 类名、主题、`appComponentFactory`。

**第 6 步：创建插件 Resources。** API 28+ 走 `sharedLibraryFiles` + `getResourcesForApplication`；API 27 及以下走 `MixResources`。资源创建失败通常意味着 package id 配错、`resources.arsc` 损坏，或者插件和宿主的资源冲突。

**第 7 步：创建插件 Application。** 用 `ShadowAppComponentFactory` 实例化插件的 `Application` 类，注入 `Resources`、ClassLoader、`ApplicationInfo`、广播列表，最后调用 `attachBaseContext()` 和 `onCreate()`。插件的全局初始化在这里发生，耗时完全取决于插件业务。

**第 8 步：启动插件 Activity。** 插件代码调用 `startActivity()`，`ShadowActivity` 把 Intent 交给 `PluginComponentLauncher`，后者把目标组件映射到宿主容器 Activity，并把插件类名、业务名等信息放进 Intent extras。系统启动容器 Activity；容器在 `onCreate()` 里通过 `DelegateProvider` 找到 loader，loader 用插件 ClassLoader 加载插件 Activity 类，实例化，`attachBaseContext()`，建立双向引用，然后转发生命周期。

**第 9 步：首帧。** 插件的 `setContentView()` 最终作用在容器的 Window 上；`LayoutInflater` 用插件的 Resources 和 ClassLoader 解析布局；View 树挂到容器上，走正常的 measure/layout/draw。首帧的时间 = 容器窗口创建 + 插件布局 inflate + 绘制 + 合成，和普通 Activity 的启动没有本质区别，只是前面多了 1~8 步。

把这几步的时间量级摊开（数字是经验区间，不是任何机型的承诺）：

| 阶段 | 量级 | 主要成本 | 能否提前 |
|---|---|---|---|
| 下载 + 校验 | 50 ms ~ 数秒 | 网络、签名校验、解压 | 可以预下载、预校验 |
| 解压 + 安装 | 10 ~ 80 ms | IO、zip 解压、so 拷贝 | 可以预安装 |
| ClassLoader + manifest | 10 ~ 50 ms | dex 验证、类加载 | 可以预加载 |
| Resources | 10 ~ 60 ms | 资源表解析、AssetManager | 部分可以预创建 |
| Application.onCreate | 业务决定 | SDK 初始化、IO | 可以延后 |
| Activity inflate + 首帧 | 30 ~ 150 ms | 布局、绘制 | 可以优化布局 |

真正影响用户感知的，往往不是第 1~6 步，而是第 7、8 步里的业务初始化。插件把 `Application.onCreate()` 写成第二个“启动初始化大礼包”，加载时间和宿主冷启动一样会失控。区别在于，宿主启动慢还能靠 Baseline Profile、启动框架优化；插件的启动优化工具链要弱得多，因为它不在系统的 dexopt/profile 流程里。

## 9. 工程化：插件化真正的成本

把技术跑通只是开始。插件化进入生产，要处理的是包、版本、灰度、监控、回滚、安全，以及“宿主和插件谁先升级”的排列组合。

### 9.1 插件包长什么样

一个典型的插件分发包不是一个裸 APK，而是一个 zip：

```text
plugin-release.zip
├── plugin.apk          # 业务插件，经过 Shadow transform
├── loader.apk          # 插件加载器（动态下发）
├── runtime.apk         # 插件运行时（动态下发）
└── config.json         # 版本、loader/runtime、插件表、白名单、哈希
```

`config.json` 是这张包的地图：loader 和 runtime 的 APK 名与哈希、每个插件的 `partKey` 和依赖关系、允许访问的宿主包白名单（`hostWhiteList`），都在里面。宿主拿到它之后，才知道先加载谁、后加载谁、哪些类可以透传给插件。

把 loader 和 runtime 也做成动态下发，是 Shadow 的一个关键设计。宿主里只留极少的引导代码（官方说法是 15 KB、约 160 个方法），框架本身的 bug 可以通过插件包修复，不必发宿主版本；不同插件还可以用不同版本的框架。代价是启动链路更长：要先加载 loader，再由 loader 加载 runtime，最后加载业务插件。

插件 APK 的 `applicationId` 通常要和宿主一致。Shadow 的 `CheckPackageNameBloc` 会强制检查这一点，理由写得很具体：

- `Resources.getIdentifier()` 用 `Context.getPackageName()` 当包名参数，如果插件用自己的包名，运行时 `getPackageName()` 返回的是宿主包名，资源就查不到；
- 很多 OEM 系统会把 `getPackageName()` / `getOpPackageName()` 的结果跨进程传给 `PackageManager` 查权限，插件如果返回自己的包名，系统查不到对应权限，行为会变得不可预测；
- 与其在 `Context` 里根据调用方动态返回不同包名（脆弱且容易出错），不如承认“插件就是宿主的一部分”，统一用宿主包名。

这也是插件化的一个基本立场：**插件不是一个独立应用，它是宿主的一段可替换代码。** 它的权限、身份、数据目录、通知渠道，最终都挂在宿主名下。

### 9.2 构建与资源

插件工程通常还是一个 Android application 模块，可以独立安装运行，方便开发调试；打包成插件时，Gradle 插件做几件事：

- 应用 Shadow transform，把 `Activity`、`Application`、`Service` 等父类替换成 Shadow 的代理类；
- 生成 `PluginManifest`，把原 manifest 里的组件信息编码进去；
- 用 `aapt2 --package-id` 给插件分配资源段；
- 把宿主 API 以 `compileOnly` 的方式接入，避免把宿主类打进插件；
- 打出 zip，附带 loader、runtime、配置。

这里最容易被忽略的是资源段和宿主 API 的版本契约。插件编译时依赖的宿主 API 版本，和运行时宿主的版本可能不一致；插件用了新 API，旧宿主没有，运行到那一行就 `NoSuchMethodError`。靠谱的做法是给插件 API 加版本号，加载时做兼容检查，不满足就拒绝加载或降级。

### 9.3 版本矩阵

插件化的测试矩阵比普通 App 大得多，因为多了一个维度：宿主版本 × 插件版本。

| 维度 | 典型取值 | 为什么重要 |
|---|---|---|
| Android 版本 | 8 ~ 17 | 类加载、资源、组件、权限行为都在变 |
| 宿主版本 | 最近 3~5 个 | 插件 API、资源段、容器组件可能不同 |
| 插件版本 | 当前 + 回滚版本 | 升级失败要能退回 |
| ABI | armeabi-v7a、arm64-v8a、x86_64 | so 加载、16 KB 页 |
| 进程 | 主进程、`:plugin` | 崩溃隔离、Binder、初始化顺序 |
| ROM | AOSP、主流厂商 | Hook/代理行为的差异 |
| 屏幕/语言/深色模式 | 关键组合 | 资源、主题、configChanges |

这张表不可能全量跑。实际做法是：用设备农场覆盖 Android 版本 × ABI 的骨架，用线上灰度覆盖 ROM 和宿主/插件版本组合，用崩溃聚合做长尾归因。

### 9.4 更新、回滚与“类卸载”

插件升级有一个绕不开的事实：**ClassLoader 加载过的类没法卸载。** 你可以在新目录里创建一个新的 ClassLoader 加载新版本，但旧 ClassLoader 和它加载的类、静态字段、线程、资源，仍然占着内存，直到进程结束。

所以插件更新通常意味着：

- 新版本装到新目录，创建新的 loader；
- 旧版本停止使用，等待进程重启或插件进程被杀；
- 如果要立即释放，只能杀掉插件所在进程，让下次启动重新加载；
- 回滚就是把“当前版本指针”指回旧目录，旧目录要保留至少一个版本。

这也是为什么插件通常跑在独立进程：更新/回滚可以只杀插件进程，不影响宿主主进程。代价是插件和宿主之间的通信要跨进程，所有共享状态都得序列化。

### 9.5 监控：插件的问题，宿主买单

插件化的线上监控要比普通 App 多几个维度：

- **加载成功率**：按插件版本、宿主版本、Android 版本、ABI 分桶；
- **加载耗时**：从点击到插件首帧，拆成下载、安装、类加载、资源、Application、Activity 六段；
- **崩溃归因**：堆栈属于插件还是宿主，插件版本是多少，是否发生在 transform 未覆盖的方法上；
- **资源异常**：`Resources$NotFoundException`、`InflateException`、主题解析失败；
- **native 异常**：`UnsatisfiedLinkError`、`dlopen` 失败、16 KB 页不兼容；
- **降级率**：加载失败后回落到宿主页面的比例；
- **内存**：多 ClassLoader、多 Resources、多 dex 带来的 PSS 增长。

一个常见的误区是“插件崩溃不算宿主崩溃”。从应用商店和用户的角度，它们是一个应用；插件崩溃同样会影响宿主的口碑和评分。插件化的稳定性目标不是“插件不崩”，而是“插件崩了，宿主还能用”。

### 9.6 安全

插件化把“可执行代码”变成了可下载的资源，安全边界也随之扩大：

- **签名与完整性**：插件包必须签名，宿主校验签名和哈希；下载走 HTTPS，必要时做证书固定；
- **来源控制**：只从自己的域名下载，不接受外部传入的插件路径；防止 zip slip（解压时路径穿越）；
- **权限最小化**：插件不应该获得宿主没有的权限；插件内的敏感操作要有独立的开关和审计；
- **代码审计**：插件和宿主一样要过安全扫描；动态下发不等于绕过审核；
- **回滚与熔断**：发现恶意/异常插件，能远程停用、回滚、清理本地文件；
- **数据隔离**：不同插件的 `dataDir`、`SharedPreferences`、数据库要分开，避免互相读写。

## 10. 合规：比技术更早决定项目生死

很多插件化项目不是死在代码上，而是死在合规上。技术方案可以慢慢磨，合规红线一旦踩了，前面所有工作都白做。

### 10.1 Google Play：不能下载可执行代码

Google Play 的 Device and Network Abuse 政策里有一段几乎是为插件化写的：

> An app distributed via Google Play may not modify, replace, or update itself using any method other than Google Play's update mechanism. Likewise, an app may not download executable code (e.g. dex, JAR, .so files) from a source other than Google Play. This restriction does not apply to code that runs in a virtual machine and has limited access to Android APIs (such as JavaScript in a webview or browser).

翻译过来：应用不能用 Play 之外的方式修改、替换、更新自身，也不能从 Play 之外的来源下载可执行代码（dex、JAR、.so）。例外是运行在虚拟机里、对 Android API 访问受限的代码，比如 WebView 里的 JavaScript。

这条政策的边界很清楚：

- 从 Play 之外的来源下发 dex / JAR / APK / .so → 违规；
- 下发 JS bundle、JSON 配置、图片、字体 → 通常没问题；
- 热修复补丁 → 补丁也是可执行代码，同样落在限制里；
- Play Feature Delivery / App Bundle 的动态模块 → 由 Play 安装，合规；
- 应用内运行一个“虚拟机”执行脚本 → 要看是否属于例外，以及是否“limited access to Android APIs”。

所以，一个只走 Google Play 的应用，做“原生插件化”基本等于放弃上架资格。这也是为什么海外的动态化方案以 RN、Flutter、H5、服务端驱动 UI 为主：它们下发的是脚本/配置/资源，不是可执行文件。

### 10.2 平台限制：一年比一年紧

即使不考虑商店政策，Android 平台本身也在持续收紧动态加载的空间。

![影响插件化的 Android 版本变化](/images/android-pluginization/08-version-matrix.webp)

| 版本 | 变化 | 对插件化的影响 |
|---|---|---|
| Android 9（API 28） | 限制非 SDK 接口 | Hook 方案开始大面积失效，反射字段/方法被拦 |
| Android 10（API 29） | 分区存储、后台启动限制 | 插件文件路径、后台 Service 行为变化 |
| Android 11（API 30） | 包可见性、`ResourcesProvider` | 查询插件/宿主包信息要加 `<queries>`；资源加载多了公开 API |
| Android 12（API 31） | `PendingIntent` 可变性、带 intent filter 的组件 `exported` 显式声明 | 插件里的 PendingIntent 要改；容器组件要写 `exported` |
| Android 13（API 33） | 动态 Receiver 导出 flag（非系统广播） | 插件 Receiver 注册要传 `RECEIVER_EXPORTED`/`NOT_EXPORTED` |
| Android 14（API 34） | DCL 只读、Receiver 全量 flag、隐式 Intent 限制 | 插件文件必须只读；Receiver 行为要适配 |
| Android 15（API 35） | 16 KB 页、edge-to-edge | 插件 so 要 16 KB 对齐；插件窗口要处理 insets |
| Android 16（API 36） | 大屏适配、更多后台限制 | 插件 UI 的配置变化、后台任务 |
| Android 17（API 37） | DCL 扩展到 native 库 | `System.load()` 的 so 必须只读 |

这张表每年都在变长。插件化框架的维护，本质上是在追这张表；一旦停止追，框架就会在某次系统升级后突然不可用。

### 10.3 国内应用商店与内容合规

国内没有 Google Play 这样一条统一的“禁止下载可执行代码”条款，但审核并不更松：

- 应用商店普遍要求提交的包与实际运行行为一致，动态下发的内容要可审核、可回滚；
- 隐私合规要求插件不能绕过宿主已告知用户的权限和隐私政策；
- 金融、医疗、政务类应用受更严格的监管，动态加载通常需要额外说明；
- 动态下发的页面/功能如果涉及内容，宿主承担内容审核责任。

插件化不能用来“绕过审核”。你可以用它做灰度、做实验、做紧急修复，但不能用它上线一个审核时不存在、审核后才出现的新功能。这条线在很多团队里是模糊的，建议在架构评审阶段就把合规同学拉进来，而不是等法务发邮件。

### 10.4 容器类应用的额外要求

如果做的是 VirtualApp 这类容器/双开产品，还要注意 Google Play 对“on-device Android containers”的要求：

- 容器必须尊重目标应用通过 `REQUIRE_SECURE_ENV` 声明的“不要在我这里运行”；
- 不能帮助用户绕过其他应用的 `FLAG_SECURE`；
- 不能伪装成目标应用去调用系统 API，让系统以为它真的安装在本机；
- 不能提供规避应用保护的手段。

`REQUIRE_SECURE_ENV` 的声明方式是在目标应用的 `<application>` 下加一个 property：

```xml
<application>
    <property
        android:name="REQUIRE_SECURE_ENV"
        android:value="1" />
</application>
```

容器读到这个标记，就应该拒绝加载该应用。这类要求看起来是“技术细节”，实际上是应用商店判断“容器是否合规”的依据。

## 11. 选型：什么时候该用，什么时候不该

把前面所有内容压缩成一张决策图，大概是这样的。

![动态化方案的选择路径](/images/android-pluginization/09-decision.webp)

先用几个问题排除掉大多数情况：

1. **只走 Google Play 吗？** 是 → 不要做原生插件化，用 App Bundle 动态特性、RN/Flutter、H5、服务端驱动 UI。
2. **只是改文案、配色、页面结构？** 是 → 服务端驱动 UI 或 H5，不需要插件化。
3. **只是修几行代码/几个资源？** 是 → 热修复（仍需评估商店政策），或者正常发版。
4. **只是包体大、想按需下载？** 是 → App Bundle + Feature Delivery，或资源分包。
5. **要运行未修改的第三方 APK？** 是 → 应用虚拟化/沙箱，做好合规评估。
6. **必须下发新的原生组件、native 库，且分发渠道不在 Play？** 是 → 代理型插件化（Shadow 这类），并接受长期维护成本。
7. **以上都不是？** → 模块化 + 组件化 + 功能开关，通常就够了。

| 方案 | 动态性 | 上线速度 | 合规风险 | 工程成本 | 适用场景 |
|---|---|---|---|---|---|
| 模块化/组件化 | 无（随版本发） | 慢 | 无 | 低 | 大多数应用 |
| App Bundle + Feature Delivery | 中（商店安装） | 中 | 低 | 中 | 只走 Play、按需功能 |
| 热修复 | 低（补丁） | 快 | 中高 | 中 | 紧急修复、小范围 |
| H5 / 服务端驱动 UI | 高（配置/页面） | 快 | 低 | 中 | 运营页、活动、轻交互 |
| RN / Flutter | 高（JS/Dart bundle） | 快 | 低~中 | 高 | 跨端、动态业务 |
| 代理型插件化 | 高（原生 APK） | 快 | 高（非 Play 渠道） | 很高 | 生态容器、内部分发、专有设备 |
| 应用虚拟化/沙箱 | 极高（任意 APK） | 快 | 极高 | 极高 | 多开、安全隔离、特定行业 |

如果评估完还是要做，下面这份清单可以当 checklist：

**架构**
- 明确“宿主 API”边界：哪些类/接口允许插件访问，用白名单固化，版本化；
- 明确进程模型：插件跑主进程还是独立进程，崩溃如何隔离；
- 明确资源段规划：每个插件一个 package id，升级不变；
- 明确容器组件数量：standard/singleTask/singleInstance 各需要多少，configChanges 写全；
- 明确插件生命周期：加载、启动、停止、更新、回滚、卸载。

**构建**
- 插件可独立构建、独立运行、独立调试；
- 宿主与插件的依赖用 `compileOnly`/`provided` 隔离，避免重复打包；
- 资源段、签名、ABI、16 KB 对齐纳入 CI 检查；
- 产物带版本、哈希、最小宿主版本、变更说明。

**运行时**
- 下载、校验、解压、只读、加载，每一步都有失败降级；
- 插件加载有超时和熔断，不能阻塞主线程；
- 插件崩溃可归因，能定位到插件版本和宿主版本；
- 有灰度、有回滚、有远程停用开关。

**合规**
- 确认分发渠道的政策（Play / 国内商店 / 内部分发）；
- 动态内容可审核、可回滚、可追责；
- 隐私政策覆盖插件行为；
- 安全团队评审下载、签名、解压、加载全链路。

**退出策略**
- 插件化的成本会随时间上升，设计时就要想好“怎么退”；
- 把插件能力收敛到少数几个边界清晰的模块，避免全 App 插件化；
- 保留“关掉插件、回到宿主内置实现”的开关。

## 12. 尾声：借来的身份

回到开头那个没安装的 APK。它跑起来了，用户看到的是一个正常的页面，返回、旋转、深色模式、字体缩放都工作。这背后是宿主把自己的 `ClassLoader`、`Resources`、`Activity` 容器、进程身份借给了它。

借来的东西，总要还。还的方式是：每次 Android 大版本升级，你都要重新验证一遍；每次商店政策调整，你都要重新评估一遍；每次插件崩溃，你都要在“插件的问题”和“宿主的问题”之间做一次归因。插件化把这些成本从“发版”转移到了“长期维护”，它不是免费的动态性。

但它留下的东西比框架本身更有价值。为了把插件跑起来，你必须搞清楚：

- 一个类是怎么被加载的，双亲委派在 Android 上到底怎么走；
- 一个资源 ID 是怎么组成的，`Resources` 和 `AssetManager` 怎么绑定；
- 一个 Activity 是怎么被系统创建的，`Instrumentation`、`ActivityThread`、`TransactionExecutor` 各管什么；
- 一个进程的身份由哪些东西构成，权限、uid、数据目录、Binder 分别意味着什么。

这四件事，平时被 `startActivity()` 和 `setContentView()` 藏得太好了。插件化把它们翻出来，摆在桌面上。就算你最后没有做插件化，这份理解也会在你做模块化、做启动优化、排查资源问题、设计跨进程通信时派上用场。

所以我的建议是：把插件化当成一门“理解 Android 的课”，而不是一个“必须上的架构”。如果业务真的需要，选代理型方案，接受它的成本，做好合规和监控；如果只是想要动态性，先看看 App Bundle、服务端驱动 UI、RN/Flutter 是不是已经够了。

最难的部分，从来不是让一个 APK 跑起来，而是判断哪些东西不应该被动态化。

## 附：版本速查

| 能力 / 变化 | 起始版本 | 说明 |
|---|---|---|
| `DexClassLoader` | API 1 | 从任意 dex/JAR/APK 加载类 |
| `InMemoryDexClassLoader` | API 26 | 从内存 `ByteBuffer` 加载 dex |
| `DelegateLastClassLoader` | API 27 | 插件优先的委派顺序 |
| 非 SDK 接口限制 | API 28 | 反射隐藏 API 被限制，Hook 方案受冲击 |
| `ResourcesProvider` / `ResourcesLoader` | API 30 | 公开的资源覆盖 API |
| `PendingIntent` 可变性要求 | API 31 | 必须指定 `FLAG_IMMUTABLE` / `FLAG_MUTABLE` |
| 动态 Receiver 导出 flag | API 33 / 34 | `RECEIVER_EXPORTED` / `RECEIVER_NOT_EXPORTED` |
| DCL 只读要求（dex/JAR/APK） | API 34 | 动态加载文件必须只读 |
| 16 KB 页支持 | Android 15（API 35） | so 需 16 KB 对齐，Play 有对应要求 |
| DCL 只读要求扩展到 native | API 37 | `System.load()` 的文件必须只读 |
| `REQUIRE_SECURE_ENV` | manifest property | 应用声明“不要在容器中运行” |

## 参考资料

- [Google Play：Device and Network Abuse 政策](https://support.google.com/googleplay/android-developer/answer/9888379)
- [Android 14：Safer Dynamic Code Loading](https://developer.android.com/about/versions/14/behavior-changes-14#safer-dynamic-code-loading)
- [Android Developers Blog：Android 17 Beta 与 native DCL](https://android-developers.googleblog.com/2026/04/the-fourth-beta-of-android-17.html)
- [Android：非 SDK 接口限制](https://developer.android.com/guide/app-compatibility/restrictions-non-sdk-interfaces)
- [Android：16 KB 页大小适配](https://developer.android.com/guide/practices/page-sizes)
- [Android：在设备上的 Android 容器中限制加载](https://developer.android.com/training/basics/intents/limit-play-loading)
- [Android：ResourcesProvider](https://developer.android.com/reference/android/content/res/loader/ResourcesProvider)
- [Android：Play Feature Delivery](https://developer.android.com/guide/playcore/feature-delivery)
- [Android：App Bundle 概览](https://developer.android.com/guide/app-bundle)
- [AOSP：`BaseDexClassLoader.java`](https://android.googlesource.com/platform/libcore/+/refs/heads/main/dalvik/src/main/java/dalvik/system/BaseDexClassLoader.java)
- [AOSP：`DexPathList.java`](https://android.googlesource.com/platform/libcore/+/refs/heads/main/dalvik/src/main/java/dalvik/system/DexPathList.java)
- [AOSP：`ActivityThread.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/app/ActivityThread.java)
- [AOSP：`Instrumentation.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/app/Instrumentation.java)
- [AOSP：`Resources.java`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/content/res/Resources.java)
- [Tencent Shadow：零反射全动态 Android 插件框架](https://github.com/Tencent/Shadow)
- [Shadow：`PluginClassLoader.kt`](https://github.com/Tencent/Shadow/blob/master/projects/sdk/core/loader/src/main/kotlin/com/tencent/shadow/core/loader/classloaders/PluginClassLoader.kt)
- [Shadow：`CreateResourceBloc.kt`](https://github.com/Tencent/Shadow/blob/master/projects/sdk/core/loader/src/main/kotlin/com/tencent/shadow/core/loader/blocs/CreateResourceBloc.kt)
- [Shadow：`PluginContainerActivity.java`](https://github.com/Tencent/Shadow/blob/master/projects/sdk/core/activity-container/src/main/java/com/tencent/shadow/core/runtime/container/PluginContainerActivity.java)
- [Shadow：`ActivityCodeGenerator.kt`](https://github.com/Tencent/Shadow/blob/master/projects/sdk/coding/code-generator/src/main/kotlin/com/tencent/shadow/coding/code_generator/ActivityCodeGenerator.kt)
- [Qihoo360 RePlugin](https://github.com/Qihoo360/RePlugin)
- [Didi VirtualAPK](https://github.com/didi/VirtualAPK)
- [Alibaba Atlas](https://github.com/alibaba/atlas)
- [VirtualApp](https://github.com/asLody/VirtualApp)
