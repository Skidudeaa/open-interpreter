"""
Open Interpreter SDK - Build Custom AI Coding Agents.

This SDK provides tools to create specialized agents using
Open Interpreter as a foundation:

- AgentBuilder: Create custom agents with specialized capabilities
- AgentPlugin: Extend agent behavior with hooks
- MCPBridge: Expose agents as MCP servers or consume MCP tools

Example:
    from interpreter.sdk import AgentBuilder

    builder = AgentBuilder()
    agent = builder.create_agent(
        name="code-reviewer",
        system_prompt="You are a code review specialist...",
        tools=["read", "grep", "glob"],
    )
    result = await agent.execute("Review the authentication module")
"""

from .agent_builder import AgentBuilder, Agent, Swarm
from .plugins import AgentPlugin, PluginRegistry, PluginContext
from .mcp_bridge import MCPBridge, MCPToolAdapter

__all__ = [
    # Agent Building
    "AgentBuilder",
    "Agent",
    "Swarm",
    # Plugins
    "AgentPlugin",
    "PluginRegistry",
    "PluginContext",
    # MCP Integration
    "MCPBridge",
    "MCPToolAdapter",
]
