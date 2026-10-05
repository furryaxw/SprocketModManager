# dll_metadata 测试夹具

`dll/` 里的极小托管 DLL 由 `src/` 的 C# 源码编译而来，用于离线测试
`sprocket_mod_manager.infrastructure.dll_metadata`。测试只读这些字节，不加载程序集。

| 夹具 | 内容 | 覆盖点 |
| --- | --- | --- |
| `MelonLoaderStub.dll` | 只声明 `MelonMod` / `MelonPlugin` / `MelonInfoAttribute` / `MelonPluginInfoAttribute` / `MelonAdditionalCreditsAttribute` 的 stub | 作为被引用程序集，让特性类型解析走真实的 MemberRef 路径 |
| `FixtureMod.dll` | `FixtureModMain : MelonMod` + `MelonInfo`（文本版本重载）+ `MelonAdditionalCredits` + 全套 `Sprocket.Mod.*` + `RepositoryUrl` | `melon_kind = "Mods"`、MelonInfo 五个字段、全部规范化 Sprocket 键、UTF-8 描述 |
| `FixturePlugin.dll` | `FixturePluginMain : MelonPlugin` + `MelonPluginInfo`（数字版本重载 `2, 5, 1`） | `melon_kind = "Plugins"`、数字版本构造重载拼出 `2.5.1` |
| `FixtureLibrary.dll` | 不引用 MelonLoader 的纯库，只带 `AssemblyMetadata` | 没有 MelonInfo 时 `melon_kind is None` 且其余字段照常 |
| `BepInExStub.dll` | 只声明 `BepInPlugin` / `BepInDependency` / `BepInIncompatibility` / `DependencyFlags` / `BasePlugin` 的 stub | 与 `MelonLoaderStub.dll` 同理，供 BepInEx 插件夹具引用 |
| `BepInExFixture.dll` | `FixturePlugin : BasePlugin` + `BepInPlugin` + 三种 `BepInDependency` 写法（单 GUID / 带版本 / 软依赖）+ `BepInIncompatibility` + 全套 `Sprocket.Mod.*` | `plugin_guid` / `plugin_name` / `plugin_version`、硬依赖进 `required_dependencies` 而软依赖不进、不兼容插件 GUID、枚举参数解码 |
| `BepInExDependency.dll` | 带 `BepInPlugin` 的最小插件，GUID 与 `BepInExFixture` 的硬依赖同名 | 本地清单按插件 GUID 解析依赖 |

每个 DLL 都小于 16 KB（约 4 KB），并已提交进仓库，所以 **CI 不需要 dotnet SDK**。

## 重新生成

```powershell
.\tests\fixtures\dll_metadata\build_fixtures.ps1
```

脚本用 `dotnet build` 编译 `src/` 下的七个工程（`net10.0`），把 DLL 复制到 `dll/`。
找不到 dotnet 时脚本直接跳过（DLL 已提交，测试不需要重建）。改动夹具源码后请重新运行并
把新的 DLL 一起提交。
