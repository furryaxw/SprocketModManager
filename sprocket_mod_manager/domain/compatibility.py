"""门面：实现放在 `registry_core`，注册表工具与客户端共用同一份领域规则。

客户端各处按这个路径 import，所以这里只做再导出；新代码直接 import `registry_core`。
"""

from registry_core.compatibility import *  # noqa: F401,F403
