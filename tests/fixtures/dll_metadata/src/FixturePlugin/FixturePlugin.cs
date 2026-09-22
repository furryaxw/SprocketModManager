// 测试夹具：MelonPlugin + 数字版本构造重载（major/minor/revision/identifier）。
using System.Reflection;
using MelonLoader;

[assembly: AssemblyVersion("2.5.1.0")]
[assembly: AssemblyFileVersion("2.5.1.9")]
[assembly: MelonPluginInfo(
    typeof(FixturePlugin.FixturePluginMain),
    "Fixture Plugin",
    2,
    5,
    1,
    "Fixture Author")]
[assembly: AssemblyMetadata("Sprocket.Mod.Id", "fixture.sprocket-plugin")]
[assembly: AssemblyMetadata("Sprocket.Mod.DisplayName", "Fixture Plugin")]
[assembly: AssemblyMetadata("RepositoryUrl", "https://example.invalid/fixture-plugin")]

namespace FixturePlugin
{
    public class FixturePluginMain : MelonPlugin
    {
    }
}
