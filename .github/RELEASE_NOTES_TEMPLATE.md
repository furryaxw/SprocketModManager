<!-- 复制到 Release 说明，替换 {占位符}，然后删掉本注释。
     Release 标题已经写了版本号，正文从 `## 中文` 开始，不再重复一级标题。
     只写客户端（这个仓库构建的那个 EXE）的行为变化。注册表条目、仓库布局、索引与 Pages 的改动
     随注册表发布生效，不进客户端的发布说明。
     签名状态按本版实情二选一：
       未签名 → 本版本 EXE 尚未进行代码签名；请使用随附的 `SprocketModManager.exe.sha256` 校验下载文件。
       已签名 → 本版本 EXE 由 SignPath.io 签名，证书由 SignPath Foundation 持有。 -->

## 中文

- **{一句话概括}**：{细节}
- **{一句话概括}**：{细节}

**要求：**Windows 10/11，并安装 Microsoft Edge WebView2 Runtime（通常已随受支持的 Windows 版本提供）。
**签名状态：**{未签名 / 已签名，措辞见模板注释}

## English

- **{short summary}**: {details}
- **{short summary}**: {details}

**Requirements:** Windows 10/11 with the Microsoft Edge WebView2 Runtime, which is normally included with supported Windows versions.
**Signing status:** {unsigned / signed, wording in the template comment}

## Code signing policy

Windows 发布产物采用 SignPath Foundation 的免费开源代码签名（申请中）：构建由 SignPath.io 签名，证书由 SignPath Foundation 持有。

Windows release artifacts use free open-source code signing provided by SignPath Foundation (application in progress): builds are signed through SignPath.io and the certificate is held by SignPath Foundation.

政策全文、提交者/批准者与隐私政策见 [Code signing policy](https://github.com/furryaxw/SprocketModManager#code-signing-policy)。
