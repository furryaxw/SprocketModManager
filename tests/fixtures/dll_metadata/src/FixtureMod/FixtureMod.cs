// 测试夹具：一个「正常」的 MelonMod，带 MelonInfo、额外 credits 和全套 Sprocket.Mod.* 元数据。
using System.Reflection;
using MelonLoader;

[assembly: AssemblyVersion("1.2.0.0")]
[assembly: AssemblyFileVersion("1.2.3.4")]
[assembly: MelonInfo(
    typeof(FixtureMod.FixtureModMain),
    "Fixture Mod",
    "1.2.3",
    "Fixture Author",
    "https://example.invalid/fixture-mod")]
[assembly: MelonAdditionalCredits("Fixture Helper")]
// 依赖夹具：故意包含空白与重复项，用来验证读取端的裁剪与去重。
[assembly: MelonAdditionalDependencies("SprocketDepth", " SprocketModAPI ", "SprocketDepth")]
[assembly: MelonIncompatibleAssemblies("LegacyOverhaul")]
[assembly: AssemblyMetadata("Sprocket.Mod.Id", "fixture.sprocket-mod")]
[assembly: AssemblyMetadata("Sprocket.Mod.KeybindingModId", "sprocket-fixture")]
[assembly: AssemblyMetadata("Sprocket.Mod.DisplayName", "Fixture Mod")]
[assembly: AssemblyMetadata("Sprocket.Mod.Description", "测试用夹具模组。")]
[assembly: AssemblyMetadata("Sprocket.Mod.Authors", "Fixture Author,Second Author")]
[assembly: AssemblyMetadata("Sprocket.Mod.Homepage", "https://example.invalid/")]
[assembly: AssemblyMetadata("Sprocket.Mod.Repository", "fixture/FixtureMod")]
[assembly: AssemblyMetadata("Sprocket.Mod.Category", "utility")]
[assembly: AssemblyMetadata("Sprocket.Mod.Tags", "fixture,test")]
[assembly: AssemblyMetadata("Sprocket.Mod.License", "AGPL-3.0-only")]
[assembly: AssemblyMetadata("Sprocket.Mod.ExperimentalFlag", "not-a-known-key")]
[assembly: AssemblyMetadata("RepositoryUrl", "https://example.invalid/fixture-mod.git")]

namespace FixtureMod
{
    public class FixtureModMain : MelonMod
    {
    }
}
