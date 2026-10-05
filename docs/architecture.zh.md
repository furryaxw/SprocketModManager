# SprocketModManager 架构

**中文** | [English](architecture.en.md)

`sprocket_mod_manager` 按职责划分。其根目录只保留 `__init__.py`；各实现模块都归属五个
显式包之一。

注册表工具与客户端共用 `registry_core/` —— 版本、包模型、兼容性规则、诊断规则与安全包
路径。客户端的 `sprocket_mod_manager/domain/` 与 `.../utilities/package_paths.py` 是对它的
再导出门面，因此注册表构建（`gen-index.py`、`validate_registry.py`）无需客户端代码。

```text
sprocket_mod_manager/
|-- domain/          # 业务数据与规则
|-- application/     # 用例与工作流协调
|-- infrastructure/  # 文件系统、网络、持久化、外部服务
|-- presentation/    # 桌面适配器与前端资源
`-- utilities/       # 无依赖的可复用辅助函数
```

## `domain/`

- `models.py`：包、Release、求解与已准备计划的值对象
- `semver.py`：版本解析、排序与范围匹配
- `registry.py`：解析后的注册表数据与包标识符查找
- `errors.py`：共享的应用错误分类

domain 模块不得导入 application、infrastructure 或 presentation。

## `application/`

- `service.py`：resolve、install、remove 与 adopt 的公开用例门面
- `solver.py`：依赖求解
- `preparer.py`：下载校验与安装计划准备
- `adoption.py`：接管已有的游戏文件
- `local_mods.py`：由磁盘、注册表与状态汇总出的本地模组清单
- `identifiers/`：按运行时区分的模组标识符——运行时检测、某运行时已安装的供给方所提供
  的目录，以及其中条目的身份规则
- `catalog.py`：并发加载目录 Release
- `install_queue.py`：排队的安装状态与 worker 串行化

application 模块协调 domain 规则与 infrastructure 适配器。它们不得导入 presentation。

## `infrastructure/`

- `config.py`、`defaults.py`：配置与默认位置
- `state.py`、`credential_store.py`、`profiles.py`：持久化的本地状态
- `github.py`、`http_client.py`、`registry_source.py`：公开的远程传输
- `private_servers/`：开发者服务器身份、信任协商与 GitHub 登录
- `release_checksums.py`、`scanner.py`、`installer.py`：包检查与文件系统
- `file_transaction.py`、`xunity_backup.py`：回滚与翻译备份
- `log_upload.py`：外部集成
- `app_logging.py`、`desktop.py`：管理器诊断与 Windows shell 集成

infrastructure 实现 I/O，可以使用 domain 类型。它不得导入 presentation。

## `utilities/`

- `checksums.py`：流式文件哈希与常见校验和文本解析
- `archive_safety.py`：不可信 ZIP 解压的共享限制
- `dependencies.py`：按 Release 条件选择的依赖
- `urls.py`：回环检测与代理 URL 规范化
- `package_paths.py`：安全的包相对路径与安装目标路径
- `processes.py`：共享的进程状态检测
- `ui_values.py`：可复用的 UI 值规范化

utilities 只能依赖 Python 标准库与 domain 错误。产品特定的 Release 元数据与远程校验和
查找仍留在 `infrastructure/release_checksums.py`。

## `presentation/`

- `web_gui.py`：JavaScript API 门面
- `webview_app.py`：WebView2 窗口与桌面生命周期
- `api_support.py`：API 序列化与打包资源查找
- `controllers/`：按功能划分的 WebView API 实现
- `client_ui/`：HTML、CSS、JavaScript 与打包的图片资源

`modman.py` 是可执行入口。包级公开 domain 符号仍由 `sprocket_mod_manager.__init__` 导出。
