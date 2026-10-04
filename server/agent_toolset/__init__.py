"""Agent 工具集：每个工具一份宿主无关声明，外部 Agent 经远程 adapter（``remote``）投影。

声明与统一调用入口不依赖任何宿主 SDK；远程 adapter 只负责项目如何确定、结果如何装进信封。
"""
