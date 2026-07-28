# 提交模组

**中文** | [English](CONTRIBUTING.en.md)

Registry 只接受公开、可审计的开源 Sprocket 模组。无需修改已有 Release，也无需把二进制
复制到本仓库。

1. 在 Pages 的“提交模组”对话框填写基础信息并生成 `sprocket-mod.json`。
2. 通过页面打开 GitHub 新建文件流程，路径保持为
   `mods/<package-id>/sprocket-mod.json`。
3. 提交 Pull Request。
4. 等待 Registry CI 和维护者审核。

`display_name` 至少填写一种语言。`description` 可完全省略，也可自由选择与显示名称
不同的语言；语言键使用 `en`、`zh-Hans`、`pt-BR` 等开放语言标签。

CI 会检查：

- meta 符合 `schemas/sprocket-mod.schema.json`，且不含版本号或下载地址；
- GitHub 仓库公开、未归档，并具有 GitHub 可识别的 SPDX 开源许可证；
- 仓库包含 `LICENSE`/`COPYING` 和实际源文件；
- 至少一个非草稿 Release 的 tag 可解析为 SemVer；
- 至少一个 Release 资产匹配 meta 的 include/exclude 规则；
- 所有依赖均已注册且依赖图无环；
- 安装 override 只落入 `Mods`、`Plugins`、`UserLibs` 或 `UserData`。

校验过程不会运行提交仓库中的任何代码。公开源码本身不证明 Release 二进制由该源码构建；
可复现构建与 GitHub Artifact Attestation 将作为独立的可信度标记处理。
