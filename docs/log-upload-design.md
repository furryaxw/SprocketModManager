# 日志上传

用户在侧栏「上传日志」菜单里选一项后，客户端读取那一项对应的日志文件（管理器自身日志，或某个已安装运行时的日志），上传至 Hasty Paste II Quick API `https://paste.furryaxw.top/api/q/`，不执行后台自动上传。

发送前最多保留最新 8 MiB，按 UTF-8 `text/plain` 原文发送。服务端返回 2xx 和完整 Paste URL；客户端不持久化日志副本，不自动重试。

管理器通过 `ClientApi.get_log_sources()` 列出可上传的日志：管理器日志恒在且恒可用，运行时日志的路径由标识符按检测到的运行时布局给出、文件存在才算可用。`ClientApi.upload_log(source_id)` 上传其中一项；成功后显示返回链接。不会在启动、刷新目录或后台定时上传。
