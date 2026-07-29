# Sprocket Mod Manager

**中文** | [English](README.en.md)

Sprocket 模组注册表、GitHub Pages 目录与 Windows GUI 客户端。

仓库只人工维护模组级基础 meta。GitHub Actions 每小时从每个模组仓库读取一次 Release，
把规范化的版本、tag 与资产写入 Pages `index.json`；网页和默认客户端不直接消耗匿名
GitHub API 配额。二进制仍始终来自模组自己的 GitHub Release。客户端使用快照求解依赖、
验证可用的发布者 SHA-256、静态扫描 DLL，再将文件事务式安装到 `Mods`、`Plugins`、
`UserLibs` 或受控的 `UserData` 路径。

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

## MelonLoader 管理

客户端设置页会检测当前 Sprocket 目录中的 MelonLoader 及其版本，并读取
`LavaGang/MelonLoader` 的最新正式 GitHub Release。安装或更新时，管理器只选择官方
`MelonLoader.x64.zip`，验证 Release 提供的 SHA-256，然后安全解压到 Sprocket 根目录。
覆盖操作带有回滚保护，并保留 `MelonLoader/Il2CppAssemblies`、日志、配置以及 ZIP 中未包含的
其他本地文件。

安装单个模组、批量安装或执行全部更新前，如果当前游戏目录没有 MelonLoader，客户端会询问是否
立即安装。选择立即安装时，MelonLoader 安装成功后才会继续模组任务；选择暂不安装时会按确认继续，
但模组在安装 MelonLoader 前不能被游戏加载。

## 运行

```powershell
.\.venv\Scripts\python.exe modman.py
```

GUI 使用 Windows Edge WebView2 的硬件加速渲染，Python 继续负责 Registry、扫描、依赖
解析与安装。GUI 支持批量选择；单项安装、批量安装和全部更新共用一个顺序下载队列。队列
运行期间仍可继续浏览并追加任务，正在执行安装事务时客户端会等待事务完成后再退出。模组列表
显示简介；详情头部集中显示名称、ID、版本和作者，正文会读取登记仓库的默认 README，使用
GitHub 渲染结果并在本地净化后显示。安装确认页会列出 Registry 声明的推荐模组，默认不勾选，
只有用户主动选择后才会一起加入安装队列。Registry 标记为“新安装推荐”的模组只会在
`Mods` 中没有任何 DLL 时显示星标并固定在当前排序顶部；已有任意模组后恢复普通排序。
该标记不会弹窗、自动勾选或自动安装。

加载目录和刷新“已安装”页面时，客户端会扫描 `Mods` 中尚未受控的 DLL。只有文件名、静态安装
目标和 GitHub Release 提供的 SHA-256 完全匹配且结果唯一时才会自动接管；未知、本地修改、
缺少摘要或存在多重匹配的文件保持不受控。接管后的模组可以正常更新和卸载；其余 `Mods`
DLL 会在“已安装”页显示为“无法识别”，只提供文件名和路径，不能更新或卸载。

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

- 只接受 HTTPS Registry 和 GitHub Release 下载地址。
- DLL 分类只读 PE/.NET 元数据，不使用 `Assembly.Load`。
- ZIP 限制条目数、单文件/总解压体积和压缩比，并拒绝绝对路径、`..` 与设备路径。
- 原生或无法识别的 DLL 必须由 Registry override 指定目标。
- 同一路径的不同内容、外部修改的托管文件和不同哈希的手工文件会阻止安装。
- Sprocket 运行时拒绝修改游戏目录。
- MelonLoader 只从 `LavaGang/MelonLoader` 的最新正式 Release 获取精确命名的 Windows x64 ZIP；
  解压同样限制条目数、体积、压缩比和目标路径，并在覆盖失败时恢复原文件。
- README 只能从该模组登记的 GitHub 仓库读取；显示前会移除脚本、表单、嵌入内容、不安全 URL
  和非 GitHub 图片资源。
- 安装状态按游戏目录隔离；卸载不会删除已被用户修改的文件。普通安装前已存在的文件仍受保护；
  通过 Release 哈希自动接管的文件会成为受管文件，并且仅在内容未变化时允许卸载删除。

模组管理器自身的更新目前只检查 GitHub Release 并提供更新入口，不会自动下载或覆盖 EXE。
若未来启用自身自动更新，应使用固定公钥验证的更新清单或可验证的 Windows 代码签名，不能只
信任与 EXE 同处一个 Release 的未签名校验文件。

## Registry

元数据规范见 [sprocket-mod-spec.md](sprocket-mod-spec.md)，作者提交流程见
[CONTRIBUTING.md](CONTRIBUTING.md)。`site/` 是无需构建框架的 GitHub Pages 页面；
`.github/workflows/pages.yml` 会在提交后及每小时生成带 Release 快照的索引并部署它。

## License

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0），详见 [LICENSE](LICENSE)。
