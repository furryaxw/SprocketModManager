// 测试夹具：一个 BepInEx 插件，带插件 GUID、硬/软依赖、不兼容声明与全套 Sprocket.Mod.*。
using System.Reflection;
using BepInEx;

[assembly: AssemblyVersion("3.1.4.0")]
[assembly: AssemblyFileVersion("3.1.4.0")]
[assembly: AssemblyMetadata("Sprocket.Mod.Id", "fixture.bepinex-plugin")]
[assembly: AssemblyMetadata("Sprocket.Mod.DisplayName", "Fixture BepInEx Plugin")]
[assembly: AssemblyMetadata("Sprocket.Mod.Description", "BepInEx 插件夹具。")]
[assembly: AssemblyMetadata("Sprocket.Mod.Authors", "Fixture Author")]
[assembly: AssemblyMetadata("Sprocket.Mod.Repository", "fixture/BepInExFixture")]
[assembly: AssemblyMetadata("Sprocket.Mod.Category", "utility")]
[assembly: AssemblyMetadata("Sprocket.Mod.License", "MIT")]

namespace BepInExFixture
{
    [BepInPlugin("fixture.bepinex-plugin", "Fixture BepInEx Plugin", "3.1.4")]
    // 三种依赖写法各来一份：单 GUID、带版本、带标志位（软依赖不算必需）。
    [BepInDependency("fixture.bepinex-dependency")]
    [BepInDependency("fixture.versioned-dependency", "2.0.0")]
    [BepInDependency("fixture.soft-dependency", DependencyFlags.SoftDependency)]
    [BepInIncompatibility("fixture.incompatible-plugin")]
    public class FixturePlugin : BasePlugin
    {
    }
}
