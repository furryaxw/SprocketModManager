// 测试夹具：BepInEx 的最小 stub（构造函数签名与 BepInEx 6 的 BepInEx.Core 一致）。
using System;

namespace BepInEx
{
    [Flags]
    public enum DependencyFlags
    {
        None = 0,
        HardDependency = 1,
        SoftDependency = 2,
    }

    public abstract class BasePlugin
    {
    }

    [AttributeUsage(AttributeTargets.Class)]
    public class BepInPlugin : Attribute
    {
        public BepInPlugin(string guid, string name, string version)
        {
        }
    }

    [AttributeUsage(AttributeTargets.Class, AllowMultiple = true)]
    public class BepInDependency : Attribute
    {
        public BepInDependency(string guid)
        {
        }

        public BepInDependency(string guid, DependencyFlags flags)
        {
        }

        public BepInDependency(string guid, string version)
        {
        }
    }

    [AttributeUsage(AttributeTargets.Class, AllowMultiple = true)]
    public class BepInIncompatibility : Attribute
    {
        public BepInIncompatibility(string guid)
        {
        }
    }
}
