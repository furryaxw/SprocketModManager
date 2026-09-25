# 提交模组

**中文** | [English](CONTRIBUTING.en.md)

Registry 只接受公开、可审计的开源 Sprocket 模组。条目是一份手写的元数据文件，通过
Pull Request 加进本仓库；不需要改动已有的 Release，也不要把二进制复制进来。

## 条目文件

条目放在 `mods/<package-id>/sprocket-mod.json`，目录名必须与条目里的 `id` 完全一致。
文件顶部用 `$schema` 指回仓库里的 schema，从 `mods/<package-id>/` 出发写成
`../../schemas/sprocket-mod.schema.json`。

`id` 用小写字母和数字，以 `.` 或 `-` 分段，至少两段；`name` 是程序集名。

一个可以直接用的最小条目（不写 `kind` 就是默认的 `modfile`）：

```json
{
  "$schema": "../../schemas/sprocket-mod.schema.json",
  "schema_version": 2,
  "id": "example.sprocket-mod",
  "name": "ExampleSprocketMod",
  "authors": ["ExampleAuthor"],
  "repository": "ExampleAuthor/ExampleSprocketMod",
  "license": "MIT",
  "display_name": {
    "en": "Example Sprocket Mod",
    "zh": "示例 Sprocket 模组"
  },
  "description": {
    "en": "An example Sprocket mod."
  },
  "release": {
    "include_prerelease": false,
    "version_pattern": "^v?([0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$",
    "assets": {
      "include": ["*.dll", "*.zip"],
      "exclude": ["*debug*", "*symbols*", "*source*"]
    }
  },
  "dependencies": [],
  "recommendations": [],
  "install": {
    "files": [
      {
        "match": "*.dll",
        "type": "melonloader:mod"
      }
    ],
    "scan_dlls": true,
    "exclude": []
  },
  "category": "utility",
  "tags": ["example"]
}
```

DLL 的具体种类也可以留到下载后按它自己的元数据决定，把类型写成 `<加载器>:*` 即可：

```json
{
  "$schema": "../../schemas/sprocket-mod.schema.json",
  "schema_version": 2,
  "id": "example.sprocket-metadata-mod",
  "name": "ExampleSprocketMetadataMod",
  "authors": ["ExampleAuthor"],
  "repository": "ExampleAuthor/ExampleSprocketMetadataMod",
  "license": "MIT",
  "display_name": {
    "en": "Example Sprocket Metadata Mod"
  },
  "release": {
    "include_prerelease": false,
    "version_pattern": "^v?([0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$",
    "assets": {
      "include": ["*.dll"],
      "exclude": []
    }
  },
  "dependencies": [],
  "install": {
    "files": [
      {
        "match": "*.dll",
        "type": "melonloader:*"
      }
    ],
    "scan_dlls": true,
    "exclude": []
  },
  "category": "utility",
  "tags": ["example"]
}
```

## 必填与可选字段

必填：`schema_version`（当前为 `2`）、`id`、`name`、`authors`、`repository`、`license`、
`display_name`、`release`、`dependencies`、`install`、`category`、`tags`。其余字段都可省略。

- `authors`：非空的字符串列表，不重复。
- `repository`：`owner/repo`，必须是公开仓库。
- `license`：SPDX 标识，例如 `MIT`、`GPL-3.0-only`。
- `display_name`：至少一种语言；`description` 整个可省略，填写时至少一种非空翻译，两种
  字段的语言不必一致。语言键用 `en`、`zh-Hans`、`pt-BR`、`x-example` 这类开放标签。
- `release`：`include_prerelease` 决定是否接受 prerelease；`version_pattern` 的第一个捕获组
  必须是 SemVer；`assets.include` 至少一项，`assets.exclude` 可为空。这里只写模式，
  条目里不出现任何版本号或下载地址（`version`、`latest_version`、`download_url`、`tag`
  都禁止出现）。
- `dependencies`：数组，每项恰好写成 `{id, version, when}`；`version` 是依赖包要满足的
  区间，`when` 是当前包自身的版本区间，`*` 表示不限，支持精确版本、比较运算符、`^` 和 `~`。
- `recommendations`：已注册的包 id 列表，不重复、不能指向当前包；它们不参与依赖求解，
  也不会自动安装，只在安装确认页列出且默认不勾选。
- `featured`：布尔值，省略时为 `false`；设为 `true` 后 Registry 网站会给模组显示星标。
- `category`：`gameplay`、`utility`、`library`、`visual`、`audio`、`translation`、`other`。
- `tags`：小写字母、数字和连字符，不重复。

## 安装规则的类型

`install.files` 的每一行写成 `{match, type}`，可选 `subpath` 和 `layout`；用这条安装线时
必须同时给出 `scan_dlls` 和 `exclude`。

`type` 是一个**文件类型**，写成 `<加载器>:<类别>`，例如 `melonloader:mod`、
`melonloader:plugin`、`melonloader:userlib`、`bepinex:plugin`、`bepinex:core`、
`xunity:translation`。也可以写成 `<加载器>:*`（例如 `melonloader:*`）：具体类型等包下载后
由 DLL 的 PE 元数据决定。

归类只读 PE/.NET 元数据，不使用 `Assembly.Load`，也不执行下载内容：继承
`MelonLoader.MelonMod` 得 `melonloader:mod`，继承 `MelonLoader.MelonPlugin` 得
`melonloader:plugin`，继承 `BepInEx.BaseUnityPlugin` 或 `BepInEx.BasePlugin` 得
`bepinex:plugin`，其余托管程序集引用 `BepInEx*` 得 `bepinex:plugin`、否则得
`melonloader:userlib`；原生和无法解析的 DLL 必须由规则给出类型。

落地目录来自**供给这个类型的加载器的供给表**：加载器用顶层 `supply` 声明「类型 ->
`{Sprocket}` 下的哪个目录」。同一个类型可以有多个供给者，例如原生 MelonLoader 把
`melonloader:mod` 供到 `{Sprocket}/Mods`，BepInEx 桥接把它供到 `{Sprocket}/MLLoader/Mods`；
客户端只用目标游戏目录里**已经装上的那一个**，一个有多个候选而都没装时会报错并要求先装
一个加载器。所以规则里只需要挑对类型，不必写目录。

`match` 同时匹配 ZIP 条目路径和文件名，大小写不敏感，第一条命中的规则决定类型。`subpath`
在供给目录之下再细分一层。`layout` 是 `file`（默认，只取文件名）或 `tree`（保留压缩包里的
相对路径）。`exclude` 命中的文件不安装。`scan_dlls` 打开时没有规则命中的 DLL 按元数据
归类；关掉时未被规则覆盖的 DLL 会让这个包扫描失败。

## 包种类、加载器与安装线

`kind` 可省略，默认 `modfile`：

- `modfile`：普通模组，只能用 `install.files`。
- `modloader`：基础运行时，例如 MelonLoader、BepInEx Bleeding Edge。
- `loaderbridge`：把另一个加载器的模块跑在别的底座上的加载器。
- `translateloader`：翻译框架加载器，例如 XUnity AutoTranslator。
- `patch`：叠在某个加载器上的补丁，例如 Sprocket-Mod-Loader。

除 `modfile` 之外的四种可以用 `install.payload`，内容能映射到供给类型时也可以用
`install.files`；一个包只能用其中一条安装线。`install.payload` 的行写成 `{match, target}`
（可选 `subpath`、`layout`）并同样要求 `exclude`，`target` 直接是游戏目录里的一处位置，
写法与 `supply` 的值相同（`{Sprocket}`、`{Sprocket}/BepInEx/core`）。

加载器用 `supply` 声明它供给别人哪些类型、各自装在 `{Sprocket}` 下的哪个目录；
`kind` 为 `modloader` 时必须至少声明一个类型。`provides` 是可选的兼容性能力表：
能力 id -> 版本字符串，字面量 `"{version}"` 表示这条发布自己的版本；没有 `provides` 的
加载器类包供给它自己的包 id，能力 id 也可以和包 id 不同（`bepinex.bepinex-be` 用
`"provides": {"bepinex.bepinex": "{version}"}` 供给 `bepinex.bepinex` 这个能力）。

只有 `kind` 为 `modloader` 的包可以声明外部 `release.source`：模组的二进制只能来自它
自己的 GitHub Releases，版本、tag 与发布者摘要都以那个仓库为准。外部来源没有可查询的
API，所以这样的条目必须自己带上顶层 `releases` 数组，其中的 `download_url` 要落在
`release.source.hosts` 列出的主机上。

## 依赖与能力

依赖 id 要么是一个已注册的包，要么是一个**能力**（见下节）。Registry CI 会拒绝其他 id，
以及真实包之间的静态依赖环。安装规则里静态写下的类型也是一条隐含依赖：类型必须有供给者，
求解时会把它对应的加载器放进同一个安装计划。

## 兼容性声明

Release 在正文里用注释块声明它落在哪些版本区间：

```html
<!-- sp-compat {"hamish.sprocket": ">=0.2.55.5"} -->
```

- 键是能力 id。游戏能力是 `hamish.sprocket`；其余由加载器类包的 `provides` 声明，例如
  MelonLoader 的 `lavagang.melonloader`、`bepinex.bepinex-be` 供给的 `bepinex.bepinex`。
  块里出现未知能力会被忽略并给出告警。
- 值是版本区间，可以是一个字符串，也可以是字符串的列表；列表里的每一项都算一段，构建时
  合并并规范成 `>=a <=b`（多段用 `||` 连接）。
- 游戏能力轴写四段：精确值和通配要写满（`0.2.55.5`、`0.2.55.x`），带比较符的边界可以少
  写尾段（`<0.2.54`）。加载器供给的能力轴写三段（`0.7.0`、`0.7.x`）。
- 整个块缺失就是「没有声明」：这条 release 沿用比它旧、最近一个有可用声明的 release 的
  区间。声明写坏时这条 release 记作未声明，其他 release 不受影响。

只声明游戏轴：

```html
<!-- sp-compat {"hamish.sprocket": ">=0.2.55.5"} -->
```

游戏轴加加载器轴：

```html
<!-- sp-compat {"hamish.sprocket": [">=0.2.55.5"], "lavagang.melonloader": [">=0.7.0"]} -->
```

多段区间列表：

```html
<!-- sp-compat {"hamish.sprocket": [">=0.2.50 <0.2.54", ">=0.2.55.5"], "lavagang.melonloader": ">=0.7.0"} -->
```

`0.2.53.x` 及更早由官方 MelonLoader 承担；`0.2.54` 起是 BepInEx Bleeding Edge 加
BepInEx/MelonLoader 桥接。桥接按 `0.7.3` 供给 `lavagang.melonloader`，所以在桥上跑的
模组只要声明 `lavagang.melonloader`，不需要另写 BepInEx 的轴。

## 提交 Pull Request

1. 新建分支，按 `mods/<package-id>/sprocket-mod.json` 加好文件。
2. 提交 Pull Request。
3. 等待 Registry CI 和维护者审核。

CI 会检查：

- meta 符合 `schemas/sprocket-mod.schema.json`，目录名与 `id` 一致，且不含版本号或下载地址；
- GitHub 仓库公开、未归档；
- 仓库包含 `LICENSE`/`COPYING` 和实际源文件；许可证以条目的 SPDX 标识为准，GitHub 认不出该文件时也算通过；
- 至少一个非草稿 Release 的 tag 可解析为 SemVer；
- 至少一个 Release 资产匹配 meta 的 include/exclude 规则；
- 每个依赖 id 都是已注册的包或一个能力，真实包之间的依赖图无环；
- 所有推荐模组均已注册；
- 每条安装规则声明的具体类型都至少有一个加载器供给（通配类型只要求该名字空间下有供给者），
  落地位置由实际装上的那个加载器的 `supply` 决定；
- `providers.json` 的每一行都有效。

校验过程不会运行提交仓库中的任何代码。公开源码本身不证明 Release 二进制由该源码构建；
可复现构建与 GitHub Artifact Attestation 将作为独立的可信度标记处理。
