# Sprocket Mod Manager

Sprocket 模组注册表、GitHub Pages 目录与 Windows GUI 客户端。

Registry 只保存模组级基础 meta。版本、tag、Release 资产和二进制始终来自模组自己的
GitHub 仓库。客户端读取 Releases、求解依赖、验证可用的发布者 SHA-256、静态扫描 DLL，
再将文件事务式安装到 `Mods`、`Plugins`、`UserLibs` 或受控的 `UserData` 路径。

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

## 运行

```powershell
.\.venv\Scripts\python.exe modman.py
```

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
```

在线校验仅调用 GitHub API；它不会克隆、构建或执行第三方模组代码。

## 构建 EXE

```powershell
.\build_exe.ps1
```

输出位于 `dist\SprocketModManager.exe`。构建脚本使用项目 `.venv`，并按
`requirements.txt` 安装缺失的打包依赖。

## 安全边界

- 只接受 HTTPS Registry 和 GitHub Release 下载地址。
- DLL 分类只读 PE/.NET 元数据，不使用 `Assembly.Load`。
- ZIP 限制条目数、单文件/总解压体积和压缩比，并拒绝绝对路径、`..` 与设备路径。
- 原生或无法识别的 DLL 必须由 Registry override 指定目标。
- 同一路径的不同内容、外部修改的托管文件和不同哈希的手工文件会阻止安装。
- Sprocket 运行时拒绝修改游戏目录。
- 安装状态按游戏目录隔离；卸载不会删除已被用户修改或安装前就存在的文件。

客户端自更新尚未启用。发布仓库与签名身份确定后，应使用固定公钥验证的更新清单或
可验证的 Windows 代码签名，不能只信任与 EXE 同处一个 Release 的未签名校验文件。

## Registry

元数据规范见 [sprocket-mod-spec.md](sprocket-mod-spec.md)，作者提交流程见
[CONTRIBUTING.md](CONTRIBUTING.md)。`site/` 是无需构建框架的 GitHub Pages 页面；
`.github/workflows/pages.yml` 会生成索引并部署它。

## License

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0），详见 [LICENSE](LICENSE)。
