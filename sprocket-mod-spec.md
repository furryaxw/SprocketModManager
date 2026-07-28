# Sprocket Mod Registry 规范 v1

**中文** | [English](sprocket-mod-spec.en.md)

Registry 托管在 GitHub Pages，只保存模组级基础元数据。版本、tag、Release
资产和二进制文件始终以模组自己的 GitHub 仓库为准。

## 数据流

```text
Pages index.json
  -> mods/<id>/sprocket-mod.json
  -> GitHub API /repos/<owner>/<repo>/releases
  -> 选择兼容 tag 和 Release assets
  -> 下载并静态扫描 DLL
  -> Mods / Plugins / UserLibs
```

Pages 不保存以下字段：

- 当前或历史版本号
- tag 列表
- Release 下载地址
- Release 资产摘要
- 模组二进制

## 基础 meta

```jsonc
{
  "schema_version": 1,
  "id": "furryaxw.sprocket-laser-rangefinder",
  "name": "SprocketLaserRangefinder",
  "authors": ["furryAxw"],
  "repository": "furryaxw/SprocketLaserRangefinder",
  "license": "GPL-3.0-only",

  "display_name": {
    "en": "Sprocket Laser Rangefinder",
    "zh-Hans": "Sprocket 激光测距仪"
  },
  "description": {
    "en": "Laser rangefinder and ballistic sight."
  },

  "release": {
    "include_prerelease": false,
    "version_pattern": "^v?([0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$",
    "assets": {
      "include": ["*.dll", "*.zip"],
      "exclude": ["*debug*", "*symbols*", "*source*"]
    }
  },

  "dependencies": [
    {
      "id": "furryaxw.sprocket-depth",
      "version": ">=0.1.0 <1.0.0",
      "when": "*"
    }
  ],

  "install": {
    "scan_dlls": true,
    "exclude": [],
    "overrides": []
  },

  "category": "utility",
  "tags": ["optics", "rangefinder"]
}
```

`display_name` 必填，但只需至少一种语言。`description` 整个字段可省略；如果填写，
至少包含一种非空翻译，而且其语言集合不必与 `display_name` 相同。

本地化键使用开放的 BCP 47 风格语言标签，例如 `en`、`ja`、`zh-Hans`、
`zh-Hant`、`pt-BR` 或私有标签 `x-example`。Registry 不维护固定语言列表，因此未来
增加语言时无需修改 schema 或客户端。客户端依次尝试完整界面语言、同语系翻译、英文，
最后使用第一项可用翻译；只有显示名称完全不存在时才回退到程序集名。

`version_pattern` 的第一个捕获组必须是 SemVer。客户端忽略 draft；
`include_prerelease` 决定是否允许 GitHub prerelease 和带预发布后缀的版本。

## 依赖

`version` 约束依赖包版本，`when` 约束当前包版本：

```json
{
  "id": "example.shared-library",
  "version": ">=2.0.0 <3.0.0",
  "when": ">=1.5.0"
}
```

支持 `*`、精确版本、比较运算符、`^` 和 `~`。依赖关系可以按当前包版本
写多条规则。Registry CI 拒绝不存在的依赖和静态依赖环。

## DLL 分类

客户端只读取 PE/.NET 元数据，不使用 `Assembly.Load`，也不执行下载内容。

1. `install.overrides` 显式规则优先。
2. ZIP 顶层已有 `Mods`、`Plugins` 或 `UserLibs` 时保留该意图。
3. 继承 `MelonLoader.MelonMod` 的程序集进入 `Mods`。
4. 继承 `MelonLoader.MelonPlugin` 的程序集进入 `Plugins`。
5. 其他托管程序集进入 `UserLibs`。
6. 原生 DLL 和非 DLL 文件默认不安装，必须通过 override 指定。

override 示例：

```json
{
  "match": "assets/*.bundle",
  "target": "UserData/MyMod/assets"
}
```

目标只能位于 `Mods`、`Plugins`、`UserLibs` 或 `UserData`。绝对路径、`..`
和 Windows 设备路径均无效。

## SHA-256

客户端按顺序使用：

1. GitHub Release Asset API 的 `digest`；
2. `<asset>.sha256`；
3. `SHA256SUMS`；
4. `checksums.txt`。

没有发布者摘要时允许安装，但界面必须显示“未由发布者校验”。客户端仍计算
本地 SHA-256，用于资产变更检测、安全卸载和文件所有权记录。

## 开源准入

提交通过 Registry 仓库 PR 完成。CI 至少检查：

- GitHub 仓库公开；
- 仓库包含 SPDX 开源许可证和 LICENSE 文件；
- 仓库中存在源码/工程文件，而不是仅保存 DLL；
- Release tag 可解析并至少包含一个可扫描资产；
- 依赖存在且无环；
- 安装规则不能越出允许目录。

验证过程不能构建或执行第三方代码。公开源码不能证明 Release 二进制一定来自该
源码；由公开 CI 构建或带 Artifact Attestation 的项目可另外显示 Verified Build。
