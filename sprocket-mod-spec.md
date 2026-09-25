# Sprocket Mod Registry 规范 v1

**中文** | [English](sprocket-mod-spec.en.md)

Registry 托管在 GitHub Pages，只保存模组级基础元数据。版本、tag、Release
资产和二进制文件始终以模组自己的 GitHub 仓库为准；声明外部下载来源的条目把版本和资产写进条目。

## 数据流

```text
Pages index.json
  -> mods/<id>/sprocket-mod.json
  -> GitHub API /repos/<owner>/<repo>/releases（外部来源用条目自带的 releases）
  -> 选择兼容 tag 和 Release assets
  -> 下载
  -> modfile：按 install.files 归类（没命中规则时按 PE 元数据分类 DLL），类型 -> 供给该类型的包声明的目录
  -> 加载器类：按 install.payload 的 target、subpath 与 layout，或按 install.files 的类型落进供给目录
```

默认 GitHub 来源的条目，Pages 不保存以下字段：

- 当前或历史版本号
- tag 列表
- Release 下载地址
- Release 资产摘要
- 模组二进制

外部来源没有可以查询的 API，这些数据反过来由条目自己提供，见「外部下载来源」。

## 基础 meta

```jsonc
{
  "schema_version": 2,
  "id": "furryaxw.sprocket-laser-rangefinder",
  "name": "SprocketLaserRangefinder",
  "authors": ["furryAxw"],
  "repository": "furryaxw/SprocketLaserRangefinder",
  "license": "GPL-3.0-only",
  "kind": "modfile",

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
  "recommendations": ["furryaxw.sprocket-jitter-fix"],
  "featured": true,

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

## 包种类

`kind` 是顶层字符串，默认 `modfile`：

- `modfile`：普通模组，按 `install.files` 安装（类型驱动）。
- `modloader`：基础运行时，例如 MelonLoader、BepInEx Bleeding Edge。
- `loaderbridge`：把另一个加载器的模块跑在别的底座上的加载器。
- `translateloader`：翻译框架加载器，例如 XUnity AutoTranslator。
- `patch`：叠在某个加载器上的补丁，例如 Sprocket-Mod-Loader。

`modfile` 必须用 `install.files`。`modloader`、`loaderbridge`、`translateloader`、`patch`
可以用 `install.payload`，内容映射到供给类型时也可以用 `install.files`；一个包不能同时用两条
安装行，`modfile` 不能用 `install.payload`。`install.mode`（`standard`、`patch`）与两条安装行
都搭配使用。只有 `kind` 为 `modloader` 的包可以声明外部
`release.source`；也只有它列在客户端的加载器页上，并可以在那里充当兼容性供给者。
`loaderbridge`、`translateloader`、`patch` 是可以直接安装的普通条目，只带一个种类徽标。
顶层 `provides` 声明这个包供给哪些兼容性能力，见「兼容性」。

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
写多条规则。依赖 id 要么是已注册的包，要么是一个能力（见「兼容性」）；Registry CI 拒绝其他
id，以及真实包之间的静态依赖环。

安装规则里静态写下的类型必须有人供给，并且它是一条隐含依赖：模组写
`melonloader:mod` 就必须有包供给这个类型，求解时把它一起放进同一个安装计划，
卸载时也按同一张依赖图判定能不能删。通配类型按它名字空间下的供给者解析。一个包自己供给的
类型不算它的依赖。

## 兼容性

兼容性就是包对能力的依赖区间。能力是一个版本化的名字，写法与包 id 相同；一根兼容性轴就是
一个能力 id。能力有两种来源：

- **本机能力**由机器自己供给，今天只有一个：游戏 `hamish.sprocket`，版本就是本机游戏版本。
- **供给能力**由加载器包用顶层 `provides` 声明。供给能力和包自己的 id 可以不同：桥接加载器
  `1499501762.bepinex-melonloader-loader` 的发布版本是 `2.3.9`，它供给的 `lavagang.melonloader`
  是 `0.7.3`。

`provides` 是可选的顶层对象，键是能力 id（写法与包 id 相同），值是版本字符串；字面量
`"{version}"` 表示这个包这一次发布的版本。没有 `provides` 的加载器类包供给自己的包 id，版本
是它这次发布的版本。模组的包 id 也是别人可以依赖的包，但它不是能力轴：模组之间靠包依赖
相连，能力轴只描述这台机器能跑什么。

客户端为已装的包建立 `{能力 id: 版本}` 表：本机检测到的 Sprocket 版本放在游戏能力 id 下，
每个已装加载器包的 `provides` 各占一项，其中的 `"{version}"` 换成已装版本。

Release 在正文里用注释块声明它落在哪些区间：

```html
<!-- sp-compat {"hamish.sprocket": [">=0.2.55.5"], "lavagang.melonloader": [">=0.7.0"]} -->
```

键就是能力 id；值是版本区间字符串或它们的列表。构建时规范成 `>=a <=b`（多段用 `||` 连接）
后写进索引，每条都成为对该能力 id 的一条依赖。游戏能力轴的精确值与通配用四段版本
（`0.2.55.5`、`0.2.55.x`），供给能力轴用三段（`0.7.0`）；带比较符的边界可以少写段数
（`<0.2.54`）。发布说明里没有可用声明的 release 沿用比它旧、最近一个有可用声明的区间；
声明写坏时该 release 记作未声明。

索引顶层用 `game` 写出游戏能力：

```json
{
  "game": { "id": "hamish.sprocket", "name": "Sprocket" }
}
```

注册表根目录的 `providers.json` 记「某个加载器类包的某段版本能跑哪段游戏版本」，行写成
`loader`、`version`、`sprocket` 三个区间，`loader` 是注册表里某个加载器类包的 id；索引在
`providers` 下带上这张表，`providers_warnings` 放在旁边：

```json
{
  "schema_version": 2,
  "entries": [
    {
      "loader": "lavagang.melonloader",
      "version": ">=0.7.0 <0.8.0",
      "sprocket": "<0.2.54"
    },
    {
      "loader": "bepinex.bepinex-be",
      "version": ">=6.0.0-be.785",
      "sprocket": ">=0.2.54"
    },
    {
      "loader": "1499501762.bepinex-melonloader-loader",
      "version": ">=2.3.0",
      "sprocket": ">=0.2.54"
    }
  ]
}
```

`0.2.53.x` 及更早由官方 MelonLoader 承担；`0.2.54` 起是 BepInEx Bleeding Edge 加
BepInEx/MelonLoader 桥接：桥接按 `0.7.3` 供给 `lavagang.melonloader`，所以声明依赖这个能力
的模组在 `0.2.54` 起仍然兼容。加载器类包没有对应行就是「这层不知道」——它既不算兼容也不算
冲突。`version` 是标准 SemVer 区间，可以带预发布段（`6.0.0-be.785`）。

客户端判定一个 release 时：任一轴不通过就是不兼容；没有可评估的轴是未知；表说这段
加载器还不支持本机游戏版本时，环境自身矛盾，判定为未知。翻译包不判自己的兼容声明，
它依赖的包照常判。

## 翻译包

翻译包是 `"category": "translation"` 的 `patch` 包：用 `install.replace` 整体接管
`xunity:translation` 的供给目录，文件规则也用这个类型。`xunity:translation` 的供给者是 XUnity
框架包，类型本身就带一条隐含依赖，框架会跟翻译包进同一个安装计划。整个目录的备份、清空与还原
见「补丁包」。

```json
{
  "kind": "patch",
  "category": "translation",
  "install": {
    "mode": "patch",
    "files": [
      {
        "match": "**",
        "type": "xunity:translation",
        "layout": "tree"
      }
    ],
    "replace": ["xunity:translation"],
    "scan_dlls": false,
    "exclude": []
  }
}
```

## 推荐模组

`recommendations` 是可省略的模组 ID 列表。推荐项必须已在 Registry 注册，不能重复或
指向当前包。它们不参与依赖求解，也不会自动安装；客户端只在安装确认页列出复选框，且
默认不勾选。用户主动选择后，推荐模组才会作为独立安装根加入队列并解析自己的依赖。

## 新安装推荐

`featured` 是可省略的布尔值，默认为 `false`。设为 `true` 后，Registry 网站会给模组显示
星标。客户端仅在 `Mods` 中没有任何 DLL 时显示该星标、将模组
固定在当前排序方式顶部，并在详情页标记“新安装推荐”；已有任意模组后恢复普通展示和排序。
这不会弹窗、自动勾选或自动安装模组。

## 文件类型与供给

文件类型写成 `<加载器>:<类别>`，例如 `melonloader:core`、`melonloader:mod`、
`melonloader:plugin`、`melonloader:userlib`、`bepinex:core`、`bepinex:plugin`、
`bepinex:patchers`、`xunity:translation`。
类型也可以写成 `<加载器>:*`（例如 `melonloader:*`）：具体类型等包下载后由 DLL 的 PE 元数据
决定。注册表构建时校验规则里的类型有人供给：具体类型要求至少一个供给者，通配类型只要求该
名字空间下有供给者；没有规则命中的 DLL 在 `scan_dlls` 打开时按 PE 元数据归类。

加载器用顶层 `supply` 声明它供给别的包哪些类型、各自装在游戏根目录（`{Sprocket}`）下的哪个目录：

```json
"supply": {
  "melonloader:core": "{Sprocket}/MelonLoader",
  "melonloader:mod": "{Sprocket}/Mods",
  "melonloader:plugin": "{Sprocket}/Plugins",
  "melonloader:userlib": "{Sprocket}/UserLibs"
}
```

一个类型可以有多个供给者：例如 MelonLoader 本体，和把 MelonLoader 模组目录重新安家到
`MLLoader` 的 BepInEx 桥接加载器，它们供给同一批 `melonloader` 类型。安装规则里静态写下的
每个类型都必须至少有一个供给者，这一点在注册表构建时校验。

### 多个供给者

同一个类型有多个供给者时，客户端只用**已经装在目标游戏目录里的那一个**：安装位置由它的
`supply` 决定（原生 MelonLoader 落在 `{Sprocket}/Mods`，BepInEx 桥接落在
`{Sprocket}/MLLoader/Mods`），一个类型最多只能装一个供给者。有多个候选而一个都没装时，
客户端**拒绝猜测**，直接报错并列出候选包，要求用户先去加载器页装一个；只有一个候选时照旧
把它作为隐含依赖自动装上。

`install.files` 里第一条命中的规则决定文件归入哪个类型，目标目录再由该类型的供给者决定：

```json
"install": {
  "files": [
    {
      "match": "assets/*.bundle",
      "type": "bepinex:plugin",
      "subpath": "Bundles"
    },
    {
      "match": "**",
      "type": "bepinex:core",
      "layout": "tree"
    }
  ],
  "scan_dlls": false,
  "exclude": ["**/*.pdb"]
}
```

- `match` 同时按 ZIP 条目路径和它的文件名匹配，大小写不敏感。
- `subpath` 在供给目录之下再细分一层。
- `layout` 是 `file`（默认，只取文件名放进类型的目录）或 `tree`（保留压缩包里的相对路径）。
  两种安装行共用同一套取值。
- `exclude` 命中的文件不安装。
- 没有规则命中的 DLL 在 `scan_dlls` 打开时按元数据归类；关掉 `scan_dlls` 时，没有被规则
  覆盖的 DLL 会让这个包的扫描失败。

归类只读 PE/.NET 元数据，不使用 `Assembly.Load`，也不执行下载内容：

1. 继承 `MelonLoader.MelonMod` -> `melonloader:mod`。
2. 继承 `MelonLoader.MelonPlugin` -> `melonloader:plugin`。
3. 继承 `BepInEx.BaseUnityPlugin` 或 `BepInEx.BasePlugin` -> `bepinex:plugin`。
4. 其余托管程序集按它引用的程序集归类：引用 `BepInEx*` -> `bepinex:plugin`（BepInEx 从
   `BepInEx/plugins` 加载），否则 -> `melonloader:userlib`。
5. 原生 DLL 和无法解析的 DLL 没有自动归类，必须由安装规则给出类型。

目标路径 = 供给目录 + `subpath` + 文件名（`layout: "file"`）或压缩包内相对路径
（`layout: "tree"`）。绝对路径、`..` 和 Windows 设备路径均无效。

## 加载器包

`modloader`、`loaderbridge`、`translateloader`、`patch` 可以用 `install.payload` 安装，
内容映射到供给类型时也可以用 `install.files`；一个包不能两条都用。`payload` 行写成：

```json
"install": {
  "payload": [
    {
      "match": "**",
      "target": "{Sprocket}",
      "layout": "tree"
    }
  ],
  "exclude": []
}
```

`payload[].target` 是游戏目录里的一处位置，写法与 `supply` 的值相同（`{Sprocket}`、
`{Sprocket}/BepInEx/core`）。目标路径 = `target` 目录 + 可选的 `subpath` + （`layout` 为
`file` 或省略时取文件名，为 `tree` 时取压缩包内相对路径）。用 `install.files` 时按类型安装，
目标目录由该类型的供给者决定。

`kind` 为 `modloader` 的包是基础运行时，例如 MelonLoader、BepInEx Bleeding Edge。
它必须用 `supply` 声明至少一个类型，说明供给别的包哪些类型、各自装在 `{Sprocket}` 下的哪个
目录；用它自己的 `payload` 落进游戏根目录，用 `provides` 声明兼容性能力：

```json
{
  "kind": "modloader",
  "supply": {
    "melonloader:core": "{Sprocket}/MelonLoader",
    "melonloader:mod": "{Sprocket}/Mods",
    "melonloader:plugin": "{Sprocket}/Plugins",
    "melonloader:userlib": "{Sprocket}/UserLibs"
  },
  "install": {
    "payload": [
      {
        "match": "**",
        "target": "{Sprocket}",
        "layout": "tree"
      }
    ],
    "exclude": []
  }
}
```

没有 `provides` 的加载器供给自己的包 id。供给能力和包自己的 id 可以不同：`bepinex.bepinex-be`
的发布版本是它自己的包版本，它用 `"provides": {"bepinex.bepinex": "{version}"}` 供给
`bepinex.bepinex` 这个能力。

加载器不走特殊通道：它和普通模组共用同一套解析（含版本区间）、发布者摘要校验、ZIP 限制和事务安装。
`kind` 为 `modloader` 的包不记逐文件清单：安装记录只留版本、发布资产与安装时落地的顶层条目
（`install.payload` 落下的顶层目录，以及游戏根目录里的代理文件；后者带安装时的摘要），卸载按这份
清单交还加载器自己的树与代理 DLL。它列在加载器页；`loaderbridge`、`translateloader`、`patch`
不列在那里，只作为普通条目出现。

供给表不等于加载器：翻译框架也供给类型（`xunity:translation`），但它是 `translateloader`。

## 补丁包

`kind` 为 `patch` 的包用 `"install.mode": "patch"` 和 `install.files`，不声明 `supply`，
分两种：

- **按文件打补丁**：不写 `replace`。第一条命中的规则决定文件类型，目标目录由该类型的供给者
  决定，`subpath` 在目录下再细分一层。
- **整体接管某个类型的目录**：写 `"replace": ["<类型>"]`，`install.files` 必须用上每个被接管
  的类型。安装时把该类型的供给目录整个打包归档（每种类型各留最新 5 份），清空目录，再装入这个
  包的文件；卸载时整目录从归档还原。`replace` 只能与 `"install.mode": "patch"` 同时出现。

归档放在 `<游戏目录>/SprocketModManager/backup/replaced/<类型>/`，其中类型里的 `:` 换成 `-`，
例如 `xunity:translation` 落在 `replaced/xunity-translation/`。安装一个新的接管包会顶掉之前接管
同一类型的已装包；被顶掉的包若还被别的已装包依赖，就拒绝安装。

按文件打补丁的例子是 Sprocket 兼容补丁 `hans21223.sprocket-mod-loader`：它依赖
`bepinex.bepinex-be`，把归档里的 `Patch/BepInEx/core/*.dll` 落进 `bepinex:core` 的供给目录；
`bepinex:core` 指向 `BepInEx/core`，不需要 `subpath`。

```json
{
  "kind": "patch",
  "install": {
    "mode": "patch",
    "files": [
      {
        "match": "Patch/BepInEx/core/*.dll",
        "type": "bepinex:core"
      }
    ],
    "scan_dlls": false,
    "exclude": []
  }
}
```

按文件打补丁时，如果补丁和它替换的包出现在同一个安装计划里，同一路径以补丁的内容为准，被盖掉的
那份内容（计划里其他包的文件，或磁盘上原有的文件）复制到
`<游戏目录>/SprocketModManager/backup/patched/<相对路径>`，每个路径只保留第一份原件。
补丁不受「目标已存在不同内容」和「受管文件被外部修改」两条冲突检查限制；非补丁文件之间
内容不一致仍然是计划自身的冲突。

卸载按文件补丁时，凡是归档里存有原件、且磁盘内容仍与补丁装进去的一致，就把原件放回去（原件继续
归它原来的归属；原本没有归属的还原后不再受管理）；补丁自己新增的文件随补丁删除；手工改过的
文件保留并给出告警。
归档条目只在没有任何补丁包引用它时清除。

## 外部下载来源

`release.source` 选择二进制从哪里来，默认是包自己的 GitHub Releases：

```json
"release": {
  "source": {
    "type": "external",
    "hosts": ["example.org"]
  }
}
```

只有 `kind` 为 `modloader` 的包可以声明外部来源；模组的二进制只能来自它自己的 GitHub
Releases。外部来源没有可以查询的 API，因此这样的条目必须另外自带顶层 `releases` 数组，
里面的 `download_url` 必须落在 `hosts` 列出的主机之一。这些主机是下载白名单：客户端只从这里
下载资产，发布者摘要仍按 SHA-256 一节的方式校验。工具或上游文档固定了某个具体构建时，
这样的条目可以直接钉住该构建的名称、URL 与摘要。

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
- 每个依赖 id 都是已注册的包或一个能力，真实包之间无环；
- 每个静态声明的安装类型都有供给者，供给目录与 `subpath` 不越出游戏目录。

验证过程不能构建或执行第三方代码。公开源码不能证明 Release 二进制一定来自该
源码；由公开 CI 构建或带 Artifact Attestation 的项目可另外显示 Verified Build。
