---
title: 让出去，再回来：在 Android 开发里用好 Kotlin 协程
dek: 挂起、作用域与取消的边界
excerpt: 订单详情页上线一周收到三条反馈：弱网退出后闪退、连点刷新数据回退、转屏重发请求。三个现象指向同一组问题——协程跑在哪个作用域里、什么时候被取消、回来之后还给谁用。文中按这三条线拆开。
date: 2026-11-25
time: 08:51
minutes: 55
tag: Android
hot: true
note: 文中的订单详情页及其代码是教学示例；线程数、超时时间等数值以开头标注的版本为准，行为描述对着这些版本的源码核对过。
---
![封面：让出去，再回来](/images/android-kotlin-coroutines/00-cover.webp)

订单详情页上线一周，收到了三条反馈。

第一条来自线上崩溃：弱网下用户打开订单详情，没等加载完就退出了，几秒钟后请求返回，应用闪退。第二条来自测试：连点两次刷新，页面先显示出新数据，一眨眼又变回了旧数据。第三条来自产品：转一下屏幕，加载动画又转了一圈，网络请求重新发了一遍。

这个页面当时的写法很典型：

```kotlin
private var requestSeq = 0

private fun refresh() {
    val seq = ++requestSeq
    Thread {
        val order = OrderApi.fetch(orderId)          // 同步的网络请求
        runOnUiThread {
            if (seq == requestSeq) render(order)      // 只渲染最后一次请求的结果
        }
    }.start()
}
```

为了不让旧结果盖掉新结果，已经有了请求编号。为了不去更新一个已经销毁的界面，还得再加一个 `isFinishing` 的判断。转屏之后重新请求的问题，则要再找一个地方把结果存下来。每一条反馈对应一处补丁，补丁之间互不相干。

换成协程之后，这三个问题用的是同一套机制。等待结果的时候，协程把线程让出去，所以界面不卡。协程属于一个作用域，作用域跟着界面或者 ViewModel 的生命周期取消，所以界面销毁之后它自己会停，网络请求也跟着取消。新旧两次加载之间只差一个取消，旧的停下来，新的才开始，先后顺序错不了。

下面按四个问题展开：在哪个作用域里启动，在哪个线程上跑，什么时候取消，出错往哪走。回答完这四个，再说结果怎么交给界面，数据层和后台任务怎么写，怎么测试和排错。例子从头到尾都围着这一个订单详情页。

## 1. 挂起：让出的是线程

`suspend` 修饰的函数叫挂起函数。它和普通函数的区别只有一条：它可以在执行到一半的时候停下，把所在的线程让出去，等条件满足了再从停下的地方继续。停下的那一刻叫挂起点，继续的那一刻叫恢复。

![挂起：让出的是线程，不是代码的先后顺序](/images/android-kotlin-coroutines/01-suspend.webp)

图里上下两条时间线做的是同一件事：发请求，等结果，更新界面。上面一条在等结果的时候把主线程卡住，这段时间里产生的绘制和点击只能排队。下面一条在等结果的时候挂起，主线程回到消息循环去处理绘制和点击，结果回来之后再恢复。

这里最容易误会的一点是：挂起让出的是线程，代码的先后顺序一点没变。先发请求，拿到结果，再更新界面，写法和读法都是顺序的。变的只是等待的那段时间里，线程去干了别的活。

能让出线程的只有挂起函数。`delay`、`withContext`、Room 的 suspend 查询、Retrofit 的 suspend 接口，以及所有调用了它们的函数。`Thread.sleep`、阻塞的 IO、一段耗时的计算都不是挂起函数，它们在哪个线程上跑，哪个线程就被占住，外面有没有包着协程都一样。

所以 `suspend` 这个关键字本身不切线程。下面这个函数照样带着 `suspend`，照样会把调用它的线程卡住：

```kotlin
suspend fun readDraft(file: File): String = file.readText()
```

在主线程上调用它，主线程就卡在读文件这一行上。想让它不卡界面，得自己把这一行挪到别的线程，第 3 节讲怎么做。

挂起函数只能在协程里调用，或者被另一个挂起函数调用，这一点由编译器检查。普通的函数里要发起一次挂起调用，得先有一个协程，通常是在一个作用域上 `launch`：

```kotlin
binding.preview.setOnClickListener {
    lifecycleScope.launch {                     // 从这里开始才是在协程里
        val draft = repository.loadDraft(orderId)   // 挂起函数，主线程在这一行让出去
        binding.preview.text = draft                // 恢复之后才执行到这一行
    }
}
```

这个协程跑在主线程上。`loadDraft` 返回之前，点击回调早就执行完了，协程停在 `loadDraft` 这一行上等结果，主线程回去处理别的消息。结果回来，协程恢复，才执行给 `preview` 赋值的那一行。

还有一点要记住：挂起前后，协程可能已经换了一条线程。不要假设挂起点两边是同一个线程，也不要指望 `ThreadLocal` 在挂起点之后还保持原来的值。

实现上，编译器会把每个挂起函数改写成一个状态机。每遇到一个挂起点，它记下当前的编号，再把一个叫 continuation 的对象交出去。恢复的时候用这个对象把函数再调用一次，它从记下的编号接着往下走。写业务代码不需要碰 continuation，不过知道有这么个东西，后面很多行为的来由会清楚很多：取消的检查发生在挂起点，异常顺着它往回传，恢复之后落在哪条线程上由调度器决定。

## 2. 启动：作用域、构造器、调度器

协程必须在一个作用域里启动。`CoroutineScope` 本身不执行任何东西，它只是一个装着 `CoroutineContext` 的容器，作用是记账：在这个作用域里启动的协程都记在它名下，作用域取消的时候一并取消。上下文里最常用的是两个元素，`Job` 表达父子关系，`CoroutineDispatcher` 决定在哪条线程上跑。新启动的协程会拿到一个新的 `Job`，其余的元素从作用域继承。

### 2.1 在哪个作用域里启动

Android 把常用的作用域都准备好了，按“这段工作属于谁”来选：

表：Android 上常用的作用域

| 属于谁 | 用哪个作用域 | 什么时候取消 |
|---|---|---|
| 当前界面 | `lifecycleScope` | 界面销毁时。加载、动画、监听都算 |
| 转屏后还要用的数据 | `viewModelScope` | ViewModel 被清除时 |
| Compose 的回调 | `rememberCoroutineScope` | 离开组合时。点击这类一次性回调用它 |
| Compose 里跟着值的工作 | `LaunchedEffect` | 值变化，或离开组合时 |
| 必须做完的事 | WorkManager | 被系统叫停，或超过时限。比如上传、同步 |
| 应用级的长期任务 | 自己创建的作用域 | 应用退出时 |

![作用域跟着谁走，协程就活到什么时候](/images/android-kotlin-coroutines/02-scopes.webp)

`lifecycleScope` 和 `viewModelScope` 的上下文都是 `SupervisorJob() + Dispatchers.Main.immediate`。前者让各个子协程互不影响，第 5 节会讲。后者让协程默认在主线程上跑，并且从主线程上调用时不用再多 post 一次。

`GlobalScope` 不要用。官方给了三条理由：它让你不得不硬编码各种参数，它让测试变得很困难，它没有一个统一的上下文。落到实际后果上，就是在它里面启动的协程跟任何生命周期都没有关系，界面早就没了它还在跑。

需要比界面活得久的工作，放 ViewModel 里。需要比 ViewModel 活得久的工作，交给 WorkManager。确实需要应用级的，自己创建一个，在 Application 里持有，再通过构造函数注入给用到它的类，测试的时候才换得掉：

```kotlin
class ShopApplication : Application() {
    // 应用级的协程都记在这里，不随任何一个界面取消
    val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
}
```

这里用 `SupervisorJob`，是因为里面启动的任务彼此不该连坐，一个失败不应该把整个应用级作用域取消掉。用 `Default` 而不用 `Main`，是因为这里启动的通常是后台任务，也没有界面可更新。

Compose 里的两个要分清。`rememberCoroutineScope()` 返回的作用域绑定在组合里的这个位置，离开组合时取消，适合在点击这类回调里启动一次性的工作。`LaunchedEffect` 在进入组合时启动，key 变了就取消再重启，离开组合时取消。官方文档专门提醒，不要在组合过程本身往任何作用域里启动协程，因为组合会反复执行。

Fragment 里要多想一步。和视图绑定的工作，用 `viewLifecycleOwner.lifecycleScope`。它的生命周期跟着 Fragment 的视图走，视图销毁了协程就取消。用 Fragment 自己的 `lifecycleScope` 的话，视图没了协程还在。

还有一个分工上的建议，同样来自官方的最佳实践：业务上的协程由 ViewModel 启动，界面层只调用 ViewModel 的普通方法。界面层自己启动的协程只做和界面有关的事，比如一段动画。这样转屏的时候工作不会被取消，启动点也不会散落在各个界面里。

### 2.2 用哪个构造器

表：四个常用的协程构造器

| 写法 | 得到什么 | 要不要等它结束 | 用来做什么 |
|---|---|---|---|
| `launch` | 一个 `Job` | 不等 | 发起一件事，结果通过状态交回 |
| `async` | 一个 `Deferred` | 不等 | 并发的几路任务，之后再取结果 |
| `withContext` | 块的返回值 | 等 | 换一个调度器或上下文执行一段代码 |
| `coroutineScope` | 块的返回值 | 等 | 在挂起函数里并发启动几个子协程 |

`launch` 默认只是把协程安排到调度器上，不保证马上执行，也可能还没开始就被取消。Android 的两个作用域用的是 `Main.immediate`，从主线程上调用时，第一个挂起点之前的代码是同步执行的，不用再等下一轮消息循环。`async` 的取消规则和 `launch` 相同，差别在异常怎么处理，放到第 5 节。

`launch` 和 `async` 的第一个参数是上下文。可以传另一个调度器，可以传 `CoroutineName` 这样的元素，它们会和作用域的上下文合并，作用域里没有的加上，有的被覆盖。唯一不要往里放的是 `Job`。放了它，新协程的父级就变成了这个 `Job`，而不是启动它的作用域，父子关系到这里就断了。协程库的文档把这种写法明确列为错误，只是为了兼容旧代码才没有禁止。

表里后两个都带一个作用域，体现的是结构化并发：在它们里面启动的子协程，全都记在这次调用的名下，这次调用要等所有子协程结束才返回。

```kotlin
suspend fun loadAll(orderIds: List<String>): List<Order> = coroutineScope {
    orderIds.map { id -> async { repository.loadOrder(id) } }.awaitAll()
}
```

这样做的好处是，`loadAll` 返回的时候，不会有它启动的协程还在后台跑着。任何一个子协程失败，其余的会被取消，异常抛给 `loadAll` 的调用方。启动协程的时候要守住这种父子关系，一旦把它启动到一个和当前调用没有关系的地方，这份等待和这份取消就都没有了。

### 2.3 在哪条线程上跑

![四个常用调度器：代码分别在哪条线程上跑](/images/android-kotlin-coroutines/03-dispatchers.webp)

表：四个常用调度器

| 调度器 | 放什么 | 线程 |
|---|---|---|
| `Main` | 更新界面，读写只属于界面的状态 | 主线程，一条 |
| `Main.immediate` | 同上，已经在主线程上时不再 post | 主线程，一条 |
| `IO` | 阻塞的文件、网络、数据库操作 | 上限默认取 64 和核数里较大的那个 |
| `Default` | CPU 密集：解析、排序、解码 | 上限等于核数，至少两个 |

调度器是从作用域继承来的。`viewModelScope.launch { }` 里的代码跑在主线程上，要换线程就用 `withContext` 临时切一下，第 3 节讲。`IO` 和 `Default` 共用同一个线程池，线程名都是 `DefaultDispatcher-worker-N`，所以在 `Default` 上切到 `IO`，通常并不会真的换一条线程。两者的区别在并行上限。`IO` 允许更多线程同时等着，`Default` 的线程数不超过核数。把阻塞 IO 放进 `Default`，等于让少数几条线程去干等，排在后面的计算任务只能跟着等。

`Main` 和 `Main.immediate` 的差别只有一处：已经在主线程上的时候怎么办。用 `Main`，每次恢复都要往主线程的消息队列里 post 一次，哪怕当前就站在主线程上。用 `Main.immediate`，当前在主线程上就直接执行。AndroidX 的两个作用域默认用的是后者，所以在 `onCreate` 里写 `viewModelScope.launch { }`，`launch` 返回的时候第一个挂起点之前的代码已经跑完了，紧接着读状态，读到的是更新以后的值。

不要把 `Dispatchers` 写死在类里。官方最佳实践的第一条就是注入调度器：把它做成构造函数的参数，默认值是 `Dispatchers.IO`，测试的时候换成一个测试调度器：

```kotlin
class OrderRepository(
    private val api: OrderApi,
    private val dao: OrderDao,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val defaultDispatcher: CoroutineDispatcher = Dispatchers.Default,
)
```

## 3. 主线程安全：切线程的事写在数据层

官方文档给 main-safe 下的定义很短：这个函数不会阻塞主线程上的界面更新。落到写法上是一条规则：数据层里的每个挂起函数，自己负责把自己切到合适的线程上，调用方不需要知道它内部用了哪个调度器。

![withContext：切线程的事写在数据层，调用方只看到一次挂起](/images/android-kotlin-coroutines/04-with-context.webp)

图里调用方在主线程上调用 `loadReceipt`，中间的线程切换全部发生在 Repository 内部。对 ViewModel 来说，这次调用只是一次普通的挂起：线程让出去，结果回来，接着往下走。

### 3.1 withContext：让挂起函数自己切线程

做法是把阻塞的那一段代码用 `withContext` 包起来：

```kotlin
class OrderRepository(
    private val api: OrderApi,
    private val dao: OrderDao,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) {
    // 在主线程上调用它也没有问题
    suspend fun loadReceipt(orderId: String): Receipt = withContext(ioDispatcher) {
        val cached = receiptFile(orderId)
        if (cached.exists()) {
            Json.decodeFromString(cached.readText())     // 阻塞的文件读取和解析
        } else {
            val receipt = api.getReceipt(orderId)        // suspend 接口，本身就不占主线程
            cached.writeText(Json.encodeToString(receipt))
            receipt
        }
    }
}
```

这个函数里混着两类调用。`readText` 和 `writeText` 是普通的阻塞调用，所以整个块要放进 `withContext(ioDispatcher)`。`api.getReceipt` 是 Retrofit 的 suspend 接口，它自己就不占主线程，放在 `withContext` 里只是为了和前后两行写在同一个块里。第 7 节会讲 Retrofit 内部是怎么切线程的。

于是 ViewModel 里没有任何一行和线程有关的代码：

```kotlin
class OrderDetailViewModel(
    private val repository: OrderRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow<OrderUiState>(OrderUiState.Loading)
    val uiState: StateFlow<OrderUiState> = _uiState.asStateFlow()

    private var loadJob: Job? = null

    fun load(orderId: String) {
        loadJob?.cancel()                      // 旧的加载在下一个挂起点停下来
        loadJob = viewModelScope.launch {
            _uiState.update { OrderUiState.Loading }
            try {
                val order = repository.loadOrder(orderId)
                _uiState.update { OrderUiState.Loaded(order) }
            } catch (e: IOException) {
                _uiState.update { OrderUiState.Error("订单加载失败，请稍后重试") }
            }
        }
    }
}
```

代码里的 `OrderUiState` 是一个密封接口，有 `Loading`、`Loaded`、`Error` 三种取值，完整的定义放在第 6 节。

开头第二条反馈，连点两次刷新之后新旧结果互相覆盖，就是被 `loadJob?.cancel()` 这一行解决的。旧的协程被取消之后，不会再走到更新状态的那一行。

关于 `withContext` 的开销，官方文档的说法是：和等价的回调写法相比，它没有额外的开销。所以不必为了少切几次线程而把代码绕来绕去。需要留意的只有两点。一是在已经处于 `IO` 上的时候再 `withContext(Dispatchers.IO)`，调度器没有变，不会真的分发。二是同一个函数里要发起十次网络请求的话，可以合到一个外层的 `withContext` 里，只切一次线程。

`withContext` 和 `launch` 的区别在于等不等。`withContext` 挂起当前协程，拿到结果再往下走。`launch` 启动一个协程就返回，结果要靠别的方式交回来。需要结果的时候用前者，只是发起一件事的时候用后者。

### 3.2 把回调式的 API 包成挂起函数

不是所有库都提供了 suspend 函数，系统 API 里大量还是回调式的，直接用又回到了开头那种层层嵌套的写法。遇到这种 API，可以在它外面薄薄地包一层 suspend 函数，用的是 `suspendCancellableCoroutine`：

```kotlin
suspend fun LocationManager.awaitCurrentLocation(provider: String): Location =
    suspendCancellableCoroutine { cont ->
        getCurrentLocation(provider, null, mainExecutor) { location ->
            if (location == null) {
                cont.resumeWithException(IllegalStateException("拿不到位置"))
            } else {
                cont.resume(location)
            }
        }
        cont.invokeOnCancellation {
            // 协程被取消时要收回的资源，在这里收回
        }
    }
```

调用 `suspendCancellableCoroutine` 的时候协程挂起，块里的代码负责注册回调。回调来了，用 `cont.resume` 或者 `cont.resumeWithException` 恢复协程。它和标准库的 `suspendCoroutine` 只差一点，就是取消。协程在等待期间被取消的话，这个函数抛出 `CancellationException`，而且之后哪怕回调来了、调用了 `resume`，协程也不会再恢复。协程库把这叫作及时取消的保证。`invokeOnCancellation` 里放的是取消时要做的清理，比如注销监听器。

有两处要留意。回调的注册和注销必须是线程安全的，因为 `invokeOnCancellation` 可能在任何时候被调用，甚至和回调本身同时发生。另外，这个函数是为一次性回调准备的，会反复触发的回调要用 `callbackFlow`。

包好之后，调用方就可以把它当普通挂起函数用。超时、取消、切换线程，前面讲的那些对它全部适用：

```kotlin
val location = withTimeout(3_000) { locationManager.awaitCurrentLocation(provider) }
```

### 3.3 共享的状态放在哪：线程限定、原子类、Mutex

几个协程要读写同一份数据的时候，有三种办法，按优先级排。

第一种，把状态限定在一条线程上。界面状态只在主线程上读写，而 `viewModelScope` 和 `lifecycleScope` 里的代码默认就在主线程上。同一个线程上的协程只会在挂起点互相切换，两次挂起之间的代码是一口气跑完的，不会执行到一半被别的协程插进来。所以只在主线程上读写的普通变量，用不着加锁。要留意的是挂起点本身：协程 A 读了某个变量之后挂起，等它恢复的时候，协程 B 可能已经把这个变量改掉了。

第二种，把状态交给线程安全的类。`MutableStateFlow` 的 `update` 是原子操作，简单的计数和标志位可以用 `AtomicInteger` 这类。

第三种，用 `Mutex`。前两种都不合适的时候才轮到它：

```kotlin
private val uploadMutex = Mutex()
private val uploaded = mutableSetOf<String>()

suspend fun uploadOnce(file: File) {
    uploadMutex.withLock {
        if (file.name in uploaded) return
        repository.upload(file)
        uploaded.add(file.name)
    }
}
```

`Mutex` 是协程版的互斥锁。它的 `lock` 是挂起函数，锁在别人手里的时候当前协程挂起，线程不占着。平时不用直接调 `lock`，用 `withLock`，它把 `lock`、`try/finally`、`unlock` 写在了一起，块里出了异常或者协程被取消，锁都会被释放。它是不可重入的，拿着锁的协程再去 `lock`，会把自己挂起，而且没有人能来解救。

不要用 `synchronized` 包住会挂起的代码。Java 的监视器要求加锁和解锁在同一条线程上，协程挂起之后很可能换了一条线程再恢复，到解锁的时候就对不上了。`synchronized` 里面只能放不挂起的代码。

## 4. 取消：协程什么时候停

取消是协作式的。`job.cancel()` 做的只是把这个 Job 标记成取消状态，正在跑的代码不会被打断。协程要到下一个挂起点，或者自己检查了状态之后，才会真正停下来。

会响应取消的地方有三类。一类是挂起函数，`delay`、`withContext`、Room 和 Retrofit 的 suspend 函数都在里面，它们在挂起的时候被取消，恢复时会直接抛出 `CancellationException`。另一类是循环或者长计算里的 `ensureActive()` 和 `isActive`，得自己写。还有一类是 `yield()`，它让出线程，顺便检查取消。

```kotlin
suspend fun compressAll(photos: List<Photo>): List<File> =
    withContext(Dispatchers.Default) {
        photos.map { photo ->
            ensureActive()                 // 已经被取消的话，这里抛出 CancellationException
            compress(photo)
        }
    }
```

不响应取消的是不挂起的代码：`Thread.sleep`、阻塞的 IO、中间没有检查的大循环。它们在哪个线程上跑，就把那个线程占到跑完为止。能响应线程中断的阻塞调用，可以用 `runInterruptible` 包起来，协程被取消时它会中断那个线程。

取消之后，协程不会立刻消失。`cancel()` 返回的时候，它可能还要再跑一小段才停在挂起点上。需要等它真的结束，用 `cancelAndJoin()`。

取消一个协程的时候具体发生了这样几件事。它的 Job 进入取消状态，它启动的子协程跟着取消。如果它正停在某个挂起函数上，那个挂起函数会抛出 `CancellationException` 把它唤醒。它里面的 `finally` 会执行。一直到下一个挂起点或者取消检查的地方，它才真正停下。整个过程里没有任何线程被终止，协程所在的线程一直在正常地跑别人的任务。

取消还会传到协程正在等的外部资源上。等网络请求的时候，Retrofit 会把这次请求取消掉。等回调的时候，`suspendCancellableCoroutine` 的 `invokeOnCancellation` 会被调用，可以在里面注销监听。协程库把这叫作及时取消：已经被取消的协程，哪怕结果正好在这时候回来，也不会再恢复。`withContext` 的结果回到原来的调度器时，如果发现调用方已经取消，结果会被直接丢掉。

![取消往下传，失败往上传](/images/android-kotlin-coroutines/05-cancellation.webp)

图里是协程之间的父子关系。父协程被取消，所有子协程跟着取消，这一点在两种作用域里都一样。不一样的是子协程失败的情况，第 5 节接着说。

取消之后还有三条规矩。

**清理里还要调用挂起函数。** 协程已经被取消了，它里面的挂起函数一调用就会抛出 `CancellationException`。清理工作必须做完的话，把这一段放进 `withContext(NonCancellable)`：

```kotlin
try {
    upload(file)
} finally {
    // 协程已经被取消，这个调用照样会执行
    withContext(NonCancellable) {
        dao.markUploadFailed(file.id)
    }
}
```

`NonCancellable` 只给 `withContext` 用，不要和调度器并列写在同一个 `withContext` 里。要切线程，在它的块里再套一层 `withContext(Dispatchers.IO)`。原因是 `withContext` 在回到原来的调度器时遵守“及时取消”的约定，调用方已经取消的话它会抛出 `CancellationException`，把 `try` 里原本的异常盖掉。从 `NonCancellable` 的块里出来以后，如果要接着碰界面，先调一次 `ensureActive()`。

**不要把 `CancellationException` 吃掉。** `catch` 里只写具体的异常类型，比如 `IOException`。写成 `Exception` 或者 `Throwable` 会把取消也接住，协程以为自己只是出了一点错，接着往下跑。标准库的 `runCatching` 捕获的正是 `Throwable`，所以挂起函数里不要直接用它：

```kotlin
suspend inline fun <T> runSuspendCatching(block: () -> T): Result<T> =
    try {
        Result.success(block())
    } catch (e: CancellationException) {
        throw e                             // 取消不是失败，原样传出去
    } catch (e: Throwable) {
        Result.failure(e)
    }
```

**超时不算失败。** `withTimeout` 到点之后抛出的是 `TimeoutCancellationException`，它是 `CancellationException` 的子类。于是有一个不太直观的后果：这个异常没被接住的话，外层的协程不会被当成失败，只是正常结束。想让超时算一种失败，用 `withTimeoutOrNull` 然后检查返回值，或者接住它再抛一个别的异常。另外，超时只能取消块里的协程，块里如果是不响应取消的阻塞代码，它还是会跑完。

```kotlin
val order = withTimeoutOrNull(5_000) { repository.loadOrder(orderId) }
if (order == null) {
    _uiState.update { OrderUiState.Error("加载超时，请重试") }
    return
}
_uiState.update { OrderUiState.Loaded(order) }
```

## 5. 异常：出错之后往哪走

协程里的异常有四条去处，取决于两件事：它所在的协程是用 `launch` 还是用 `async` 启动的，所在的上下文里是普通的 `Job` 还是 `SupervisorJob`。

![异常去哪儿：先看在哪种作用域里，再看是 launch 还是 async](/images/android-kotlin-coroutines/06-exceptions.webp)

带普通 `Job` 的作用域，就是 `coroutineScope`、`withContext`、`runBlocking` 里启动的子协程。一个子协程失败，所在的作用域被取消，兄弟协程一起停下，异常一路抛给最外层的调用方。在这种作用域里，`launch` 和 `async` 没有区别，`async` 启动的协程就算没有人来取结果，失败时照样会取消父协程。

`SupervisorJob` 是 `lifecycleScope`、`viewModelScope`、`supervisorScope` 这些。一个子协程失败，兄弟协程不受影响。接下来看它是 `launch` 还是 `async`：`launch` 的异常交给上下文里的 `CoroutineExceptionHandler`，没有这个处理器，就交给线程的 `uncaughtExceptionHandler`，在 Android 上就是崩溃。`async` 的异常只能从 `await()` 拿到，没有人 `await`，它就留在返回的 `Deferred` 里，不崩溃，也没有日志。

最后这一格是最安静的一种失败。在 `viewModelScope` 里随手 `async` 了一下却忘了取结果，出错时什么现象都没有。

所以处理异常的正路是把 `try/catch` 写在协程里面，就像第 3 节的 `load()` 那样：只捕获 `IOException`，捕获到之后把状态改成 `Error`。`CancellationException` 不是 `IOException`，取消照常往上走，不会被误接。

`CoroutineExceptionHandler` 的定位是兜底。它只在整个协程结束之后、异常没有别的地方可去时才被调用。给一个会传给父级的子协程单独挂处理器没有用，那种情况用的是父作用域里的那一个。它适合记日志、做上报，不适合拿来表达界面的错误状态。在 AndroidX 的两个作用域里要给它，可以写在启动协程的上下文参数里：

```kotlin
private val errorLogger = CoroutineExceptionHandler { _, e ->
    CrashReporter.record(e)
}

viewModelScope.launch(errorLogger) { /* … */ }
```

装了处理器之后，原本会崩溃的异常被它接住了。所以要不要装、装了以后做什么，得先想清楚。

并发的时候，`coroutineScope` 和 `supervisorScope` 选哪个，看这几路任务是不是缺一不可：

```kotlin
suspend fun loadDetail(orderId: String): OrderDetail = supervisorScope {
    val invoice = async { api.getInvoice(orderId) }
    val logistics = async { api.getLogistics(orderId) }
    OrderDetail(
        order = requireNotNull(dao.findById(orderId)).toOrder(),
        invoice = invoice.awaitOrNull(),      // 发票拿不到，不影响订单本身
        logistics = logistics.awaitOrNull(),
    )
}

private suspend fun <T> Deferred<T>.awaitOrNull(): T? = try {
    await()
} catch (e: CancellationException) {
    throw e                                 // 外层被取消时要照常传出去
} catch (e: Exception) {
    null
}
```

发票和物流是锦上添花的两路数据，缺了也照样能展示订单，所以用 `supervisorScope`，每一路单独取结果。如果三样数据缺一不可，就换成 `coroutineScope`，任何一路失败都会把另外两路取消，在外层写一个 `try/catch` 就够了。

## 6. 把结果交给界面：StateFlow 和生命周期

开头第三条反馈，转屏之后重新加载，要分两步解决。加载放到 `viewModelScope` 里，转屏时 ViewModel 还在，协程不会被取消，这是第一步。第二步是给结果找一个界面销毁之后仍然存在的地方，这个地方就是 ViewModel 里的 `StateFlow`。

![冷流转热流：从数据层到界面的一条状态流](/images/android-kotlin-coroutines/08-stateflow.webp)

Room 的查询返回的是一个冷流，每次有人收集它才执行一次查询。到 ViewModel 这一层，用 `stateIn` 把它转成一个 `StateFlow`，上游从此只启动一份，谁来收集都共享同一个结果。下面是订单详情页 ViewModel 的另一个版本，`uiState` 不再由 `load()` 一步一步地更新，而是直接从数据层的 Flow 转换过来：

```kotlin
sealed interface OrderUiState {
    data object Loading : OrderUiState
    data class Loaded(val order: Order) : OrderUiState
    data class Error(val message: String) : OrderUiState
}

class OrderDetailViewModel(
    private val repository: OrderRepository,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    private val orderId: String = checkNotNull(savedStateHandle["orderId"])

    val uiState: StateFlow<OrderUiState> = repository.observeOrder(orderId)
        .map { entity ->
            if (entity == null) OrderUiState.Error("订单不存在")
            else OrderUiState.Loaded(entity.toOrder())
        }
        .catch { emit(OrderUiState.Error("订单加载失败，请稍后重试")) }
        .stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(5_000),
            initialValue = OrderUiState.Loading,
        )

    private val _messages = Channel<String>(Channel.BUFFERED)
    val messages: Flow<String> = _messages.receiveAsFlow()

    fun refresh() {
        viewModelScope.launch {
            try {
                repository.refreshOrder(orderId)     // 写进 Room 之后，uiState 的上游会再发一次
            } catch (e: IOException) {
                _messages.send("刷新失败，请稍后重试")
            }
        }
    }
}
```

`StateFlow` 有三条性质，决定了它适合当界面状态。它必须有一个初值，新来的订阅者马上就能拿到当前的状态。它的更新是合并的，慢的订阅者会跳过中间的值，但一定拿得到最新的那一个。它永远不结束，所以收集它的协程必须属于一个会被取消的作用域。

`stateIn` 的三个参数，`scope` 是共享协程待的地方，`initialValue` 是最初的状态，`started` 决定上游什么时候启动、什么时候停：

表：SharingStarted 的三种策略

| 策略 | 上游什么时候启动 | 什么时候停 | 适合 |
|---|---|---|---|
| `Eagerly` | 立刻 | 永不停 | 必须一直保持着的数据 |
| `Lazily` | 第一个订阅者出现时 | 之后永不停 | 第一位用户来过之后就一直要保着的数据 |
| `WhileSubscribed` | 第一个订阅者出现时 | 最后一个离开一段时间后，下面写的是 5 秒 | 界面状态，转屏时不想打断加载 |

转屏的时候，旧界面销毁，新界面创建，中间只隔一瞬间。`WhileSubscribed(5_000)` 里的 5 秒正是留给这一瞬间的：最后一个订阅者离开 5 秒内又来了新的，上游就不会停，新界面直接拿到当前值。

`WhileSubscribed(5_000)` 的初值只在第一次起作用。之后上游停下来再启动，状态保持为上一次的值，不会闪一下加载中。

上游的异常要在 `stateIn` 之前用 `catch` 接住，否则异常会终止共享协程，界面只会停在最后一个状态上。`catch` 只管它上游的异常，所以写在 `map` 的后面是对的。

### 6.1 收集要跟着“看得见”走

界面什么时候收集，决定了协程在后台是不是还在白白做功：

![收集要跟着“看得见”走：STARTED 才收，STOPPED 就停](/images/android-kotlin-coroutines/07-lifecycle.webp)

应用退到后台时，Activity 只是停在了后台，还没有销毁，`lifecycleScope` 里的协程还在跑。直接写 `lifecycleScope.launch { viewModel.uiState.collect { … } }`，界面看不见的时候它照样在更新。`repeatOnLifecycle` 把收集放在一个随生命周期启停的协程里：到 `STARTED` 才启动，掉到 `STARTED` 以下就取消，再回来就重新启动。

```kotlin
class OrderDetailActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { viewModel.uiState.collect { render(it) } }
                launch { viewModel.messages.collect { showSnackbar(it) } }
            }
        }
    }
}
```

`repeatOnLifecycle` 自己是挂起函数，所以要放在 `lifecycleScope.launch` 里面；它在生命周期销毁时返回。它要在 `onCreate` 或者 `onViewCreated` 里调用一次，不要写在会被反复执行的地方，否则可能同时跑着好几份。块每次重启都是一个新的协程，所以只需要执行一次的初始化不要写在里面。

Compose 里对应的写法是 `collectAsStateWithLifecycle`，它来自 `androidx.lifecycle:lifecycle-runtime-compose` 这个依赖，默认也是 `STARTED` 开始收集、`STOPPED` 停止：

```kotlin
@Composable
fun OrderDetailScreen(viewModel: OrderDetailViewModel) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    when (val s = state) {
        OrderUiState.Loading -> LoadingIndicator()
        is OrderUiState.Loaded -> OrderContent(s.order)
        is OrderUiState.Error -> ErrorPanel(s.message)
    }
}
```

Compose 自带的 `collectAsState` 不感知生命周期，只要组件还在组合里就一直收集。同一个界面在 ViewPager 的下一页，或者返回栈的下面，组合还在，协程也还在跑。官方文档对 `LaunchedEffect` 也有同样的提醒：它的作用域绑的是组合，不是 Activity 的生命周期。

### 6.2 一次性事件和持续状态要分开

状态适合放在 `StateFlow` 里，事件不适合。“刷新失败”是一条提示，弹一次就该结束；如果把它做进 `uiState`，转屏之后新界面重新订阅，会再收到一次，提示又弹一遍。

上面 ViewModel 里的 `_messages` 就是为事件准备的，用一个缓冲的 `Channel`，对外通过 `receiveAsFlow` 暴露成 `Flow`。界面同样在 `repeatOnLifecycle` 里收集它。这条做法没有官方条文，是社区这些年踩出来的经验，但道理是清楚的：状态是“现在是什么样”，事件是“刚才发生了什么”，两者的收集方式本来就不该一样。

它也有代价。`receiveAsFlow` 里的每一条消息只发给一个订阅者，订阅者刚拿到消息就被取消的话，比如界面正好退到后台，这条消息就丢了。一条都不能丢的事件不适合走这条路。

### 6.3 Flow 里常用的几个算符

表：Flow 里常用的几个算符

| 算符 | 做什么 |
|---|---|
| `map`、`filter` | 转换、筛选每一个值 |
| `onEach` | 每来一个值做点事，常用来打日志 |
| `distinctUntilChanged` | 和上一个值相同就不往下发 |
| `combine` | 把几个流最新的值合在一起 |
| `debounce` | 停顿一段时间没有新值，才往下发最后一个 |
| `mapLatest` | 来了新值就取消上一次还没做完的转换 |
| `catch` | 接住上游的异常，换成别的值继续往下发 |
| `flowOn` | 让它上游的那一段在别的调度器上跑 |

把它们拼起来，就是一个搜索框：

```kotlin
private val query = MutableStateFlow("")

val results: StateFlow<List<Order>> = query
    .debounce(300)
    .distinctUntilChanged()
    .mapLatest { repository.search(it) }       // 新的关键词一来，上一次搜索就被取消
    .catch { emit(emptyList()) }
    .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())
```

`flowOn` 平时用得不多，因为数据层的挂起函数已经自己切好了线程。只有自己用 `flow {}` 写上游、里面又有阻塞调用的时候，才需要在它后面接一个 `flowOn(Dispatchers.IO)`。它只影响写在它前面的算符。

上面用到的算符里，`mapLatest` 到 1.11.0 还标着实验性，用到它的地方要加 `@OptIn(ExperimentalCoroutinesApi::class)`。

把上面这段搜索放回类里，后面测试一节会用到它：

```kotlin
@OptIn(ExperimentalCoroutinesApi::class)
class OrderSearchViewModel(private val repository: OrderRepository) : ViewModel() {

    private val query = MutableStateFlow("")

    val results: StateFlow<List<Order>> = query
        .debounce(300)
        .distinctUntilChanged()
        .mapLatest { repository.search(it) }
        .catch { emit(emptyList()) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    fun onQuery(text: String) {
        query.value = text
    }
}
```

### 6.4 Compose 里的三个入口

Compose 里和协程打交道的入口主要有三个，各管一种场景。

界面状态来自 ViewModel 的 `StateFlow`，用 `collectAsStateWithLifecycle` 把它变成组合里的状态，前面已经写过。

跟着界面走的工作用 `LaunchedEffect`。它在进入组合时启动，key 变了就取消再重启，离开组合时取消，适合入场动画、按某个值定时刷新这一类事。

响应点击这类回调，用 `rememberCoroutineScope()`：

```kotlin
@Composable
fun RefreshButton(onRefresh: suspend () -> Unit) {
    val scope = rememberCoroutineScope()
    Button(onClick = { scope.launch { onRefresh() } }) {
        Text("刷新")
    }
}
```

它拿到的作用域在按钮离开组合时取消。用户在请求进行中关掉了页面，这个协程不会留下来。这个作用域里的工作只应该做和界面有关的事，业务上的加载还是交给 ViewModel，这是前面说过的分工。

一次性任务的异步结果，官方文档推荐 `produceState`，同时把它的适用范围限定在不需要挺过转屏的任务上。要挺过转屏的，还是放 ViewModel。

## 7. 数据层和后台任务

![Retrofit 和 Room 都已经自己切好了线程](/images/android-kotlin-coroutines/09-data-layer.webp)

Room 和 Retrofit 对 suspend 的支持，要点在于它们的 suspend 函数本身就不占主线程。调用方在主线程上调用，中间的线程切换由它们自己完成，调用方只是被挂起。

Room 这边，suspend 的查询由生成的代码用 `withContext` 切到 Room 的执行器上。返回 `Flow` 的查询更省事，表里的数据一变，Flow 就自动再查一遍：

```kotlin
@Dao
interface OrderDao {
    @Query("SELECT * FROM orders WHERE id = :id")
    suspend fun findById(id: String): OrderEntity?

    @Query("SELECT * FROM orders WHERE id = :id")
    fun observeOrder(id: String): Flow<OrderEntity?>

    @Upsert
    suspend fun upsert(order: OrderEntity)
}
```

几个 suspend 的写操作要放在同一个事务里，用 `withTransaction`。事务块跑在 Room 事务执行器的线程上，同一时间只有一个事务，其余的排队：

```kotlin
db.withTransaction {
    orderDao.upsert(order)
    itemDao.replaceAll(order.id, order.items)
}
```

Retrofit 这边，接口方法声明成 `suspend` 就够了：

```kotlin
interface OrderApi {
    @GET("orders/{id}")
    suspend fun getOrder(@Path("id") id: String): OrderDto
}
```

它背后做的事和图里左边一栏一样：把 continuation 存起来，把请求交给 OkHttp，回调的时候再恢复。有两点和写业务代码直接相关。一是协程被取消时，它会调用 `call.cancel()`，用户退出页面，请求也跟着撤了。二是响应码不是 2xx 的时候抛的是 `HttpException`，它不是 `IOException`，想把它也当成一种失败，`catch` 里要单独写上。

所以回到第 3 节那条规则：数据层的挂起函数要自己保证 main-safe，但在 Room 和 Retrofit 的 suspend 函数外面，不需要再套一层 `withContext(Dispatchers.IO)`。需要套的只有你自己写的阻塞代码。

### 7.1 必须做完的事交给 WorkManager

界面的作用域随着界面取消，ViewModel 的作用域随着 ViewModel 取消。上传一张凭据这种事，用户关掉页面也应该做完，它需要一个比两者都活得久的东西。WorkManager 的 `CoroutineWorker` 就是干这个的：

```kotlin
class UploadReceiptWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val path = inputData.getString(KEY_PATH) ?: return Result.failure()
        return try {
            repository.uploadReceipt(File(path))
            Result.success()
        } catch (e: IOException) {
            if (runAttemptCount < 3) Result.retry() else Result.failure()
        }
    }
}
```

`doWork` 是挂起函数，默认跑在 `Dispatchers.Default` 上，要换调度器就在它里面用 `withContext`。系统给它的时间上限是十分钟，到点或者约束条件不再满足时，WorkManager 会取消它返回的 future，协程的 `Job` 跟着被取消。所以 `doWork` 里面的阻塞代码同样要响应取消，这一点和普通的协程没有区别。

提交任务的时候，把约束条件和重试的退避策略一起写上：

```kotlin
val request = OneTimeWorkRequestBuilder<UploadReceiptWorker>()
    .setInputData(workDataOf(KEY_PATH to file.path))
    .setConstraints(
        Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
    )
    .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
    .build()

WorkManager.getInstance(context).enqueue(request)
```

这段代码可以在任何地方调用，不需要一个协程。`doWork` 里返回 `Result.retry()` 之后，下一次尝试的间隔由这里的退避策略决定。

还有一种情况介于两者之间：工作应该比调用方活得久，但调用方又想等它做完。官方给的写法是注入一个外部作用域，在它里面启动，再用 `join()` 等结果：

```kotlin
class ReceiptUploader(private val externalScope: CoroutineScope) {
    suspend fun upload(file: File) {
        externalScope.launch { uploadBlocking(file) }.join()
    }
}
```

`launch` 把协程记在了外部作用域名下，调用方被取消它也不会停。`join` 只是挂起当前的协程，调用方取消的时候它照常抛出 `CancellationException`。

### 7.2 没有自带作用域的组件：BroadcastReceiver 和 Service

Activity 和 Fragment 有 `lifecycleScope`，ViewModel 有 `viewModelScope`，`BroadcastReceiver` 什么都没有。它的 `onReceive` 在主线程上执行，返回之后系统就认为这次广播处理完了。要在这里做一点异步的事，先调用 `goAsync()` 拿到一个 `PendingResult`，系统会等到你调用它的 `finish()` 为止。这段时间仍然算在广播的执行时限里，前台广播 10 秒，后台广播 60 秒，超时就是一次广播 ANR。官方文档还提醒，这里的事情不做完，后面的广播就一直等着。

```kotlin
class SyncReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val pendingResult = goAsync()
        val appContext = context.applicationContext
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                SyncWorker.sync(appContext)          // 几秒钟之内能做完的事
            } finally {
                pendingResult.finish()               // 不调用它，就是一次广播超时
            }
        }
    }
}
```

这里随手新建了一个作用域，和前面“不要随手新建作用域”的说法并不矛盾。这一次广播的协程，生命周期本来就该和这一次广播绑在一起，`finish()` 之后它也该结束了。能这么做的前提是事情够短。官方文档对 `goAsync` 的说明里写得很清楚，耗时长的任务应该交给 JobScheduler 或者 Service 这一类的机制。

启动型的 Service 也没有自带作用域，自己创建一个，在 `onDestroy` 里取消：

```kotlin
class SyncService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        serviceScope.launch {
            try {
                SyncWorker.sync(applicationContext)
            } finally {
                stopSelf(startId)
            }
        }
        return START_NOT_RESTART
    }

    override fun onDestroy() {
        serviceScope.cancel()
        super.onDestroy()
    }
}
```

系统对从后台启动 Service 另有限制，能不能启动得单独考虑。所以实际的取舍是：几秒钟之内能做完的事，用 `goAsync` 配一个临时作用域；要可靠做完的事，交给 WorkManager。

## 8. 测试和排错

### 8.1 测试

前面反复说要把调度器注入进来，为的就是这一步。测试里把 `Dispatchers.Main` 换成一个测试调度器，再给 Repository 传一个假的实现，ViewModel 就可以在单元测试里跑了。

下面的例子假定 `OrderRepository` 是一个接口，实际项目里通常都会这么做，前面为了少写几行才写成了类。`FakeOrderRepository` 是它在测试里的实现：`loadOrder` 直接返回准备好的订单，`calls` 记录被调用了几次，`failWith` 用来指定下一次调用抛什么异常。

```kotlin
@OptIn(ExperimentalCoroutinesApi::class)
class MainDispatcherRule(
    val dispatcher: TestDispatcher = UnconfinedTestDispatcher(),
) : TestWatcher() {
    override fun starting(description: Description) = Dispatchers.setMain(dispatcher)
    override fun finished(description: Description) = Dispatchers.resetMain()
}

class OrderDetailViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val repository = FakeOrderRepository()
    private lateinit var viewModel: OrderDetailViewModel

    @Test
    fun `加载成功之后是 Loaded`() = runTest(mainDispatcherRule.dispatcher) {
        viewModel = OrderDetailViewModel(repository)
        viewModel.load("42")
        assertEquals(OrderUiState.Loaded(FAKE_ORDER), viewModel.uiState.value)
    }

    @Test
    fun `加载失败之后是 Error`() = runTest(mainDispatcherRule.dispatcher) {
        repository.failWith(IOException("timeout"))
        viewModel = OrderDetailViewModel(repository)
        viewModel.load("42")
        assertIs<OrderUiState.Error>(viewModel.uiState.value)
    }
}
```

`runTest` 跑在一个线程上，`delay` 之类的时间等待会跳过。它的默认超时是 60 秒，测试里有永远不结束的协程就放到 `backgroundScope` 里，不然测试会一直挂到超时。

`UnconfinedTestDispatcher` 来了任务就执行，所以 `load("42")` 返回的时候状态已经更新完了，测试写起来是顺序的。想验证“请求发出去了但还没回来”这类中间状态，换 `StandardTestDispatcher`，它把任务排进调度器，等你发话：

```kotlin
@Test
fun `请求在 advanceUntilIdle 之后才发出`() = runTest {
    Dispatchers.setMain(StandardTestDispatcher(testScheduler))
    try {
        viewModel = OrderDetailViewModel(repository)
        viewModel.load("42")
        assertEquals(0, repository.calls)        // 还排着队
        advanceUntilIdle()
        assertEquals(1, repository.calls)
    } finally {
        Dispatchers.resetMain()
    }
}
```

所有测试调度器共用同一个调度器时钟，这样 `advanceTimeBy`、`runCurrent` 这些操作对它们是一起生效的。`Dispatchers.setMain` 到 1.11.0 仍然标着实验性，所以类上要加 `@OptIn(ExperimentalCoroutinesApi::class)`。

虚拟时间在这个时候派上用场。第 6 节搜索框的 `debounce(300)` 要等 300 毫秒，测试里不用真的等：

```kotlin
@OptIn(ExperimentalCoroutinesApi::class)
class OrderSearchViewModelTest {

    @Test
    fun `停顿 300 毫秒之后才发起搜索`() = runTest {
        val repository = FakeOrderRepository()
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        try {
            val viewModel = OrderSearchViewModel(repository)
            viewModel.onQuery("鞋")
            runCurrent()                       // 执行到下一个挂起点，debounce 还在计时
            assertEquals(0, repository.calls)

            advanceTimeBy(300)                 // 虚拟时间往前走 300 毫秒
            runCurrent()                       // 执行恰好排在这一刻的任务
            assertEquals(1, repository.calls)
        } finally {
            Dispatchers.resetMain()
        }
    }
}
```

`advanceTimeBy` 只执行排在它之前的任务，恰好排在第 300 毫秒的那个不包含在内，所以后面要再跟一句 `runCurrent`。整个测试一瞬间就跑完，验证的却是“不到 300 毫秒不发请求，到了才发”这件事。

### 8.2 排错

出了问题，先看症状落在哪一类：

表：协程相关问题的症状和方向

| 症状 | 多半的原因 | 往哪儿看 |
|---|---|---|
| 界面卡顿 | trace 里主线程在读写文件或数据库，数据层有一段阻塞代码没放进 `withContext` | 主线程栈顶的业务函数 |
| 退出页面后闪退 | 日志里是更新界面的代码抛的异常，协程跑在不随生命周期取消的作用域里 | 启动它的是哪个作用域 |
| 协程停在某一处 | 被取消后又调用了挂起函数，或者异常被吃掉 | 有没有 `catch` 范围过宽，有没有 `runCatching` |
| 界面停在加载中 | 上游出了异常，被 `catch` 之外的地方吞掉 | `stateIn` 之前有没有 `catch` |
| 界面在后台也刷新 | 收集没放在 `repeatOnLifecycle` 里 | 界面层的收集代码 |
| 测试卡到超时 | 有永远不结束的协程没放进 `backgroundScope` | 测试里启动的协程 |

协程库自带一套调试模式。打开之后，每个协程会分到一个编号，执行它的线程名里会带上协程的名字和编号，比如 `coroutine#12`，默认的名字就是 `coroutine`。它由系统属性 `kotlinx.coroutines.debug` 控制，取值可以是 `on`、`off`，或者默认的 `auto`，`auto` 表示 JVM 开启了断言才启用。这个属性只在类加载时读一次，调试包里要打开，得在 `Application` 的 `attachBaseContext` 里先设好：

```kotlin
override fun attachBaseContext(base: Context) {
    System.setProperty("kotlinx.coroutines.debug", "on")
    super.attachBaseContext(base)
}
```

`CoroutineName` 可以给协程起一个有意义的名字，排查的时候线程名一眼就能认出来：

```kotlin
viewModelScope.launch(CoroutineName("load-order")) { /* … */ }
```

## 9. 一张评审清单

碰到启动协程的代码，按顺序问这几个问题：

表：启动协程之前要问的问题

| 问题 | 不对的时候会怎样 |
|---|---|
| 这段工作属于谁，作用域选对了吗 | 界面销毁后还在跑，或者转屏时被白白取消 |
| 有没有把 `Dispatchers` 写死 | 测试里换不掉调度器 |
| 数据层的挂起函数是不是 main-safe | 主线程被卡住 |
| 阻塞的循环里有没有 `ensureActive` | 取消之后代码照跑 |
| `catch` 的是不是具体类型 | 取消被吃掉，协程停不下来 |
| `async` 的结果有没有人 `await` | 出错了没有任何提示 |
| `stateIn` 前面有没有 `catch` | 界面停在最后一个状态 |
| 收集有没有放进 `repeatOnLifecycle` | 应用在后台也在刷新界面 |
| `StateFlow` 里有没有塞一次性事件 | 转屏之后提示重复弹出 |
| 必须做完的事有没有交给 WorkManager | 页面一关任务就没了 |

还有几句流传很广的说法，对照着前面的内容过一遍：

表：几句常见的说法

| 说法 | 实际情况 |
|---|---|
| 加了 `suspend` 就会在后台线程上跑 | 不对，`suspend` 只表示这个函数可以挂起，线程由调度器决定 |
| 挂起函数都要包一层 IO 调度器 | 只有自己写的阻塞代码需要，Room 和 Retrofit 的 suspend 函数不需要 |
| `viewModelScope` 里 `launch` 出错一定崩溃 | 没装异常处理器的话会崩溃，装了就由处理器接住 |
| `viewModelScope` 里 `async` 出错会崩溃 | 不会，异常留在 `Deferred` 里，没人 `await` 就没人知道 |
| 超时会抛异常，所以外层协程会失败 | 超时抛的是 `CancellationException` 的子类，外层只是正常结束 |
| `cancel` 之后协程马上就停了 | 要到下一个挂起点或者取消检查的地方才停 |
| 状态和提示都用 `StateFlow` 发 | 提示是事件，放进 `StateFlow` 转屏之后会重复弹 |
| `lifecycleScope` 里收集 Flow 就够了 | 还要再套一层 `repeatOnLifecycle` |
| `runBlocking` 可以随手把挂起变成同步 | 它会阻塞当前线程，在主线程上就是 ANR。只该出现在 `main` 函数、测试和非挂起的回调里 |
| `goAsync()` 之后想干多久都行 | 到 `finish()` 为止的时间仍然算在广播的时限里，前台 10 秒，后台 60 秒 |

## 10. 回到那三条反馈

开头那个订单详情页，换上协程之后只有一个地方在管加载：

```kotlin
fun load(orderId: String) {
    loadJob?.cancel()
    loadJob = viewModelScope.launch {
        _uiState.update { OrderUiState.Loading }
        try {
            val order = repository.loadOrder(orderId)
            _uiState.update { OrderUiState.Loaded(order) }
        } catch (e: IOException) {
            _uiState.update { OrderUiState.Error("订单加载失败，请稍后重试") }
        }
    }
}
```

第一条反馈，退出页面后闪退：协程跑在一个会随界面取消的作用域里。属于界面的工作放 `lifecycleScope`，属于数据的工作放 `viewModelScope`，选哪一个看它属于谁。协程取消之后不会再走到更新界面的代码，Retrofit 的请求也跟着取消。

第二条反馈，新旧结果互相覆盖：`loadJob?.cancel()` 之后旧的协程停在下一个挂起点上，新的协程从这之后才开始。

第三条反馈，转屏重新加载：加载放在 `viewModelScope` 里，转屏时 ViewModel 还在。结果放在 `StateFlow` 里，新界面一订阅就拿到当前值，不用再请求一遍。

请求编号、`isFinishing`、结果缓存，这三处补丁一行都不用写了。协程没有给每个问题各配一个工具，它给的是挂起、作用域、父子关系这三样东西，问题是在这个框架里自己消失的。

## 附：版本速查

表：已核对的版本差异

| 行为 | 版本 | 说明 |
|---|---|---|
| Room 事务所在的线程 | 2.4.3 起 | `withTransaction` 从这个版本起把事务块放到事务执行器的线程上；2.4.3 到 2.8.5 之间的七个版本都看过，更早的没有核对 |
| `withTransaction` 的位置 | 2.6.1 及以前在 `room-ktx` | 2.8.5 在 `room-runtime` 里，用哪个版本就引哪个 |
| `ViewModel` 可注入作用域 | 2.9.1 起 | 2.9.1、2.10.0、2.11.1 里都有接收 `CoroutineScope` 的构造函数 |
| `setMain` 仍是实验性 | 1.11.0 | 完整的名字是 `Dispatchers.setMain`，用的时候要加 `@OptIn` |
| `lifecycleScope` 的位置 | 2.11.1 | 定义在 `lifecycle-common` 里；更早的版本在哪个依赖里没有核对 |

表里“X 起”的意思是：我在 X 这个版本的源码里见到了对应的代码，在它的前一个版本里没有见到。

## 参考资料

源码都取自这几个库在 Maven 上的官方 sources 包，版本和开头标的一致。

- [Android 官方：Kotlin coroutines on Android](https://developer.android.google.cn/kotlin/coroutines)：main-safe 的定义，`withContext` 的基本写法
- [Android 官方：Kotlin coroutines best practices](https://developer.android.google.cn/kotlin/coroutines/coroutines-best-practices)：注入调度器、ViewModel 负责启动协程、外部作用域等九条建议
- [Android 官方：Improve app performance with Kotlin coroutines](https://developer.android.google.cn/kotlin/coroutines/coroutines-adv)：`withContext` 的性能说明，`launch` 和 `async` 的选择
- [Android 官方：Use Kotlin coroutines with lifecycle-aware components](https://developer.android.google.cn/topic/libraries/architecture/coroutines)：`LaunchedEffect` 的作用域，`collectAsStateWithLifecycle` 的默认行为
- [Android 官方：CoroutineWorker](https://developer.android.google.cn/topic/libraries/architecture/workmanager/advanced/coroutineworker)
- [Android 官方：Kotlin flows on Android](https://developer.android.google.cn/kotlin/flow)
- [Kotlin 官方：Coroutines guide](https://kotlinlang.org/docs/coroutines-guide.html)
- [Kotlin 官方：Cancellation and timeouts](https://kotlinlang.org/docs/cancellation-and-timeouts.html)
- [Kotlin 官方：Exception handling](https://kotlinlang.org/docs/exception-handling.html)
- [Kotlin 官方：Asynchronous Flow](https://kotlinlang.org/docs/flow.html)
- [kotlinx.coroutines 1.11.0 源码](https://github.com/Kotlin/kotlinx.coroutines/blob/1.11.0/README.md)：`Builders.common.kt` 里 `launch`、`async`、`withContext` 的文档，`CoroutineExceptionHandler.kt`、`Supervisor.kt`、`NonCancellable.kt`、`Timeout.kt` 里的坑都写在文档里
- AndroidX 的 sources 包：`androidx.lifecycle:lifecycle-*:2.11.1`、`androidx.room:room-runtime-android:2.8.5`、`androidx.work:work-runtime:2.12.0`、`androidx.compose.runtime:runtime-android:1.13.0`
- `com.squareup.retrofit2:retrofit:3.0.0` 的 sources 包：`KotlinExtensions.kt`、`HttpServiceMethod.java`、`Platform.java`
