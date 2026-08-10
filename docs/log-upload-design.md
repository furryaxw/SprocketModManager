# MelonLoader 日志上传

用户点击一次上传按钮后，客户端从用户配置的 Sprocket 目录读取 `MelonLoader/Latest.log`，上传至 Hasty Paste II Quick API `https://paste.furryaxw.top/api/q/`，不执行后台自动上传。

发送前最多保留最新 8 MiB，按 UTF-8 `text/plain` 原文发送。服务端返回 2xx 和完整 Paste URL；客户端不持久化日志副本，不自动重试。

当前使用 Hasty Paste II Quick API：`POST https://paste.furryaxw.top/api/q/`，请求体为 UTF-8 `text/plain` 原始日志，服务端返回完整 Paste URL。客户端最多上传日志尾部 8 MiB，不压缩、不脱敏。

管理器通过 `ClientApi.upload_latest_log()` 暴露单次动作；UI 按钮调用它，成功后显示返回链接。不会在启动、刷新目录或后台定时上传。
