// 测试夹具：没有 MelonInfo / 不引用 MelonLoader 的纯托管库，只带 AssemblyMetadata。
using System.Reflection;

[assembly: AssemblyVersion("3.0.0.0")]
[assembly: AssemblyFileVersion("3.0.0.1")]
[assembly: AssemblyMetadata("Sprocket.Mod.Id", "fixture.sprocket-library")]
[assembly: AssemblyMetadata("Sprocket.Mod.DisplayName", "Fixture Library")]
[assembly: AssemblyMetadata("RepositoryUrl", "https://example.invalid/fixture-library")]

namespace FixtureLibrary
{
    public static class FixtureLibraryType
    {
        public static int Answer()
        {
            return 42;
        }
    }
}
