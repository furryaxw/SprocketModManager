// 测试夹具：GUID 与 BepInExFixture 的硬依赖同名的最小插件，用来验证按插件 GUID 解析依赖。
using System.Reflection;
using BepInEx;

[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: AssemblyMetadata("Sprocket.Mod.Id", "fixture.bepinex-dependency")]
[assembly: AssemblyMetadata("Sprocket.Mod.DisplayName", "Fixture BepInEx Dependency")]
[assembly: AssemblyMetadata("Sprocket.Mod.Authors", "Fixture Author")]
[assembly: AssemblyMetadata("Sprocket.Mod.Repository", "fixture/BepInExDependency")]
[assembly: AssemblyMetadata("Sprocket.Mod.Category", "library")]
[assembly: AssemblyMetadata("Sprocket.Mod.License", "MIT")]

namespace BepInExDependency
{
    [BepInPlugin("fixture.bepinex-dependency", "Fixture BepInEx Dependency", "1.0.0")]
    public class FixtureDependency : BasePlugin
    {
    }
}
