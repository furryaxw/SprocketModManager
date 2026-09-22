// 测试夹具：MelonLoader 的最小 stub（构造函数签名与 MelonLoader 官方源码一致）。
using System;

namespace MelonLoader
{
    public abstract class MelonMod
    {
    }

    public abstract class MelonPlugin
    {
    }

    [AttributeUsage(AttributeTargets.Assembly)]
    public class MelonInfoAttribute : Attribute
    {
        public MelonInfoAttribute(Type type, string name, string version, string author = null, string downloadLink = null)
        {
        }

        public MelonInfoAttribute(
            Type type,
            string name,
            int versionMajor,
            int versionMinor,
            int versionRevision,
            string author,
            string downloadLink = null)
        {
        }
    }

    [AttributeUsage(AttributeTargets.Assembly)]
    public class MelonPluginInfoAttribute : Attribute
    {
        public MelonPluginInfoAttribute(
            Type type,
            string name,
            string version,
            string author = null,
            string downloadLink = null)
        {
        }

        public MelonPluginInfoAttribute(
            Type type,
            string name,
            int versionMajor,
            int versionMinor,
            int versionRevision,
            string author,
            string downloadLink = null)
        {
        }
    }

    [AttributeUsage(AttributeTargets.Assembly)]
    public class MelonAdditionalCreditsAttribute : Attribute
    {
        public MelonAdditionalCreditsAttribute(string credits)
        {
        }
    }

    [AttributeUsage(AttributeTargets.Assembly)]
    public class MelonAdditionalDependenciesAttribute : Attribute
    {
        public MelonAdditionalDependenciesAttribute(params string[] assemblyNames)
        {
            AssemblyNames = assemblyNames;
        }

        public string[] AssemblyNames { get; internal set; }
    }

    [AttributeUsage(AttributeTargets.Assembly)]
    public class MelonIncompatibleAssembliesAttribute : Attribute
    {
        public MelonIncompatibleAssembliesAttribute(params string[] assemblyNames)
        {
            AssemblyNames = assemblyNames;
        }

        public string[] AssemblyNames { get; internal set; }
    }
}
