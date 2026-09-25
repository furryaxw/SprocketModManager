# Sprocket Mod Manager

**中文** | [English](README.en.md)

Sprocket 模组注册表、GitHub Pages 目录与 Windows GUI 客户端。

仓库只人工维护模组级基础 meta。GitHub Actions 每小时从每个模组仓库读取一次 Release，
把规范化的版本、tag 与资产写入 Pages `index.json`；网页和默认客户端不直接消耗匿名
GitHub API 配额。二进制仍始终来自模组自己的 GitHub Release。客户端使用快照求解依赖、
验证可用的发布者 SHA-256、按安装规则与 PE 元数据判断文件类型，再将文件事务式安装到供给
该类型的加载器声明的目录（`{Sprocket}/Mods`、`{Sprocket}/BepInEx/plugins` 等）。

## 当前纵向场景

```text
furryaxw.sprocket-laser-rangefinder
  -> furryaxw.sprocket-depth
  -> GitHub Releases
  -> SprocketDepth.dll              -> UserLibs/
  -> SprocketLaserRangefinder.dll   -> Mods/
```

该场景已使用两个真实 Release 通过下载、远端 digest 校验、DLL 分类、隔离目录安装、状态记录、
主包卸载和孤立依赖清理。

## 本地模组识别

客户端会静态读取已安装 DLL 的 `MelonInfo` 与 `Sprocket.Mod.*` 程序集元数据，
把它们与 Registry 条目和安装记录对齐。

读哪些目录由**运行时标识符**按当前环境决定：标识符声明的能力在场时才激活，判据是已装的
同命名空间供给者、环境里的能力，或**磁盘上检测到运行时**（管理器之外装上的加载器同样算数）。
目录来自已装供给者的 `supply` 表——桥接加载器（`provides: lavagang.melonloader`）把模组安家到
`MLLoader/Mods` 时，清单、认领与启用/禁用都跟着走；没有已装供给者时用检测到的布局。三者都
不成立时这个运行时的目录不读，也不会凭空列出文件。MelonLoader 的检测是游戏根目录的
`version.dll` 代理加 `MelonLoader/net*/MelonLoader.dll`（桥接布局是 `MLLoader/`），版本取运行时
DLL 的 PE 版本信息；BepInEx 的标识符检测 `winhttp.dll` / `doorstop_config.ini` 加
`BepInEx/core/BepInEx*.dll`，只声明 `BepInEx/plugins` 与 `BepInEx/patchers` 两个目录，不认任何
身份：那里的文件只会作为本地未知条目出现，没有名字、版本或声明 ID。

细节见 [`docs/architecture.md`](docs/architecture.md)。

## 加载器管理

加载器就是注册表里的普通条目：`mods/lavagang.melonloader/` 与 `mods/bepinex.bepinex-be/` 的
`kind` 是 `modloader`，用 `supply` 声明它供给别的包哪些类型、各自装在哪，用 `provides` 声明它的
兼容性能力。客户端在加载器页列出每个 `modloader` 包：装没装、已装版本、能装的最新版、当前环境
的兼容判定，以及它供给的类型与目录。安装、更新和卸载都走普通安装管线（解析 → 准备 → 应用），与
模组共用同一套下载主机限制、发布者 SHA-256 校验、ZIP 限制和事务安装；加载器自己的载荷按
`install.payload` 落进游戏根目录，内容映射到供给类型时也可以按 `install.files` 安装。基础运行时
不记逐文件清单：它的安装记录只留版本、发布资产和安装时落地的顶层条目（加载器自己的目录，以及
游戏根目录里的代理文件），卸载按这份清单交还整棵树与代理 DLL。

模组的安装规则里写了哪个类型，求解器就把供给该类型的加载器一起放进同一个安装计划，所以安装一个
MelonLoader 模组会在同一事务里装上 MelonLoader。加载器供给的能力版本是兼容性轴之一。

## 运行

```powershell
.\.venv\Scripts\python.exe modman.py
```

设置页可以持久启用诊断模式；也可以通过 `--debug` 为本次启动强制开启。两者按 OR 计算。
诊断模式会记录 `DEBUG` 级别日志并启用 WebView2 调试；普通启动记录 `INFO` 及以上级别：

```powershell
.\.venv\Scripts\python.exe modman.py --debug
.\SprocketModManager.exe --debug
```

管理器日志位于 `%LOCALAPPDATA%\SprocketModManager\Latest.log`。每次启动都会清空
`Latest.log`，将上一轮日志保存为带时间戳的历史文件，并只保留最新 5 份。关于页面可以直接打开
该目录。侧栏的「上传日志」列出可上传的来源：管理器日志始终可用，游戏目录里检测到的运行时
日志（`MelonLoader\Latest.log`、`BepInEx\LogOutput.log`）在文件存在时才列出；选定一项后
上传，并返回可复制的公开链接。

GUI 使用 Windows Edge WebView2 的硬件加速渲染，Python 继续负责 Registry、扫描、依赖
解析与安装。GUI 支持批量选择；单项安装和批量安装共用一个顺序下载队列。队列
运行期间仍可继续浏览并追加任务，正在执行安装事务时客户端会等待事务完成后再退出。模组列表
显示简介；详情头部集中显示名称、ID、版本和作者，正文会读取登记仓库的默认 README，使用
GitHub 渲染结果并在本地净化后显示。安装确认页会列出 Registry 声明的推荐模组，默认不勾选，
只有用户主动选择后才会一起加入安装队列。Registry 标记为“新安装推荐”的模组只会在当前
运行时的模组目录（没有桥接加载器时就是 `Mods`）中没有任何 DLL 时显示星标并固定在当前排序
顶部；已有任意模组后恢复普通排序。该标记不会弹窗、自动勾选或自动安装。

加载目录和刷新“已安装”页面时，客户端会扫描活跃运行时标识符的目录中尚未受控的 DLL。
只有文件名、静态安装目标和 GitHub Release 提供的 SHA-256 完全匹配且结果唯一时才会自动接管；
未知、本地修改、缺少摘要或存在多重匹配的文件保持不受控。接管后的模组可以正常更新和卸载；
其余 DLL 会在“已安装”页显示为“无法识别”，只提供文件名和路径，不能更新或卸载。

CLI 使用本地 Registry：

```powershell
.\.venv\Scripts\python.exe modman.py --index-file index.json packages
.\.venv\Scripts\python.exe modman.py --index-file index.json plan furryaxw.sprocket-laser-rangefinder --scan
.\.venv\Scripts\python.exe modman.py --index-file index.json --game-path G:\Sprocket install furryaxw.sprocket-laser-rangefinder
```

CLI 全局参数必须写在子命令前。远端 Registry 默认地址为
`https://sprocketmods.furryaxw.top/index.json`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe validate_registry.py --mods-dir mods --offline
.\.venv\Scripts\python.exe validate_registry.py --mods-dir mods
.\.venv\Scripts\python.exe gen-index.py --mods-dir mods --output index.json
.\.venv\Scripts\python.exe gen-index.py --mods-dir mods --output index.json --fetch-releases
```

在线校验仅调用 GitHub API；它不会克隆、构建或执行第三方模组代码。
`--fetch-releases` 使用 `GITHUB_TOKEN` 时生成与 Pages 相同的嵌入式 Release 快照。

## 构建 EXE

```powershell
.\build_exe.ps1
```

输出位于 `dist\SprocketModManager.exe`。构建脚本使用项目 `.venv`，并按
`requirements.txt` 安装缺失的打包依赖。GUI 需要 Windows 10/11 与 Edge WebView2
Runtime；受支持的 Windows 和当前 Microsoft Edge 通常已预装该 Runtime。

## 安全边界

- 只接受 HTTPS Registry 与 Release 下载地址：GitHub 来源的资产必须落在模组自己仓库的 releases 下，外部来源的资产必须落在条目声明的主机白名单里；只有 `kind` 为 `modloader` 的包可以声明外部来源。
- 文件类型与 DLL 归类只读 PE/.NET 元数据，不使用 `Assembly.Load`。
- ZIP 限制条目数、单文件/总解压体积和压缩比，并拒绝绝对路径、`..` 与设备路径。
- 文件只能落在某个加载器供给表声明的目录，或加载器自己 `install.payload` 的 `target` 里：一个类型可以有几个供给者，用已装上的那个，`subpath` 不得越出游戏目录。
- “翻译”分类是 `patch` 包：整体接管 `xunity:translation` 的供给目录，安装前把整个目录归档（保留最新 5 份）后清空，卸载时整目录还原。
- 原生或无法静态归类的 DLL 必须由安装规则显式给出类型。
- 同一路径的不同内容、外部修改的托管文件和不同哈希的手工文件会阻止安装；补丁模式按它的替换语义直接覆盖，被替换的原件先归档到 `SprocketModManager/backup/patched`。
- Sprocket 运行时拒绝修改游戏目录。
- 加载器与模组走同一条安装管线：同样的下载主机限制、发布者摘要校验、ZIP 限制、事务安装与失败回滚。
- README 只能从该模组登记的 GitHub 仓库读取；显示前会移除脚本、表单、嵌入内容、不安全 URL
  和非 GitHub 图片资源。
- 安装状态按游戏目录隔离；卸载不会删除已被用户修改的文件。普通安装前已存在的文件仍受保护；
  通过 Release 哈希自动接管的文件会成为受管文件，并且仅在内容未变化时允许卸载删除。
- 本地测试开发者服务器支持私有 ZIP/DLL 的授权下载、整包/文件 SHA-256 校验和事务安装；客户端已通过
  GitHub Device Flow 换取服务器 session token，服务端负责验证 GitHub 身份。私有 manifest 使用
  Ed25519 detached canonical-JSON 签名，并在首次使用时确认服务器公钥指纹；当前服务端仍只是协议技术验证，
  不适合作为公网正式分发后台。客户端签名、信任协商、key-status 和轮换规则见
  [私有服务器签名协议](docs/private-server-signatures.md)。

模组管理器自身的更新：启动时查一次 GitHub Release（tag `v<版本>`，资产 `SprocketModManager.exe`）。
有新版本就弹窗给两条路 —— **立即更新**把新 EXE 下载到同目录、核对 GitHub 给出的资产 SHA-256，
再交给一个换壳子进程重启（Windows 下运行中的 EXE 不能覆盖自己，由子进程等旧进程退出后替换）；
**暂缓**则这次会话继续用，下次启动重新问一次。源码运行或没打包成单文件时不给自更新，只把人带到发布页。

这条链路的信任到「GitHub 的 HTTPS + GitHub 自己算的资产摘要」为止。要防到「发布账号被拿走」这一层，
还需要固定公钥验证的更新清单或可验证的 Windows 代码签名。

## Registry

元数据规范见 [sprocket-mod-spec.md](sprocket-mod-spec.md)，作者提交流程见
[CONTRIBUTING.md](CONTRIBUTING.md)。`site/` 是无需构建框架的 GitHub Pages 页面；
`.github/workflows/pages.yml` 会在提交后及每小时生成带 Release 快照的索引并部署它。

## License

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0），详见 [LICENSE](LICENSE)。
