"""
MCPBridge - Model Context Protocol integration.

Provides two-way MCP integration:
1. Consume MCP tools - use external MCP servers as tools
2. Expose agents as MCP servers - let other tools call your agents

MCP (Model Context Protocol) is an open standard for connecting
AI models to external tools and data sources.

Example - Consuming MCP tools:
    bridge = MCPBridge()
    await bridge.connect_server("filesystem", {"path": "/project"})
    tools = bridge.get_available_tools()

Example - Exposing agent as MCP server:
    bridge = MCPBridge()
    bridge.register_agent(my_agent)
    await bridge.start_server(port=8080)
"""

import asyncio
import json
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, Union


class MCPTransport(Enum):
    """MCP transport types."""
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


@dataclass
class MCPTool:
    """
    Represents an MCP tool that can be called.
    """
    name: str
    description: str
    input_schema: Dict[str, Any]
    server_name: str = ""

    def to_llm_tool(self) -> Dict[str, Any]:
        """Convert to LLM tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }


@dataclass
class MCPResource:
    """
    Represents an MCP resource.
    """
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"


@dataclass
class MCPServer:
    """
    Configuration for an MCP server.
    """
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    transport: MCPTransport = MCPTransport.STDIO
    url: Optional[str] = None  # For HTTP transport


@dataclass
class MCPCallResult:
    """Result of calling an MCP tool."""
    success: bool
    content: Any
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class MCPClient:
    """
    Client for communicating with an MCP server.

    Handles the low-level protocol details for stdio-based servers.
    """

    def __init__(self, server: MCPServer):
        self.server = server
        self._process: Optional[subprocess.Popen] = None
        self._tools: Dict[str, MCPTool] = {}
        self._resources: Dict[str, MCPResource] = {}
        self._connected = False
        self._message_id = 0

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """
        Connect to the MCP server.

        Returns:
            True if connection successful
        """
        if self._connected:
            return True

        try:
            if self.server.transport == MCPTransport.STDIO:
                return await self._connect_stdio()
            elif self.server.transport == MCPTransport.HTTP:
                return await self._connect_http()
            else:
                return False

        except Exception as e:
            self._connected = False
            return False

    async def _connect_stdio(self) -> bool:
        """Connect via stdio transport."""
        env = {**subprocess.os.environ, **self.server.env}

        self._process = subprocess.Popen(
            [self.server.command] + self.server.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        # Initialize connection
        init_result = await self._send_request("initialize", {
            "protocolVersion": "0.1.0",
            "clientInfo": {
                "name": "open-interpreter",
                "version": "0.1.0",
            },
            "capabilities": {},
        })

        if init_result:
            self._connected = True
            # Fetch available tools
            await self._discover_tools()
            return True

        return False

    async def _connect_http(self) -> bool:
        """Connect via HTTP transport."""
        # HTTP connections don't maintain persistent state
        # Just verify the server is reachable
        try:
            import urllib.request
            url = self.server.url or f"http://localhost:8080"
            req = urllib.request.Request(f"{url}/health")
            with urllib.request.urlopen(req, timeout=5) as response:
                self._connected = response.status == 200
                if self._connected:
                    await self._discover_tools()
                return self._connected
        except Exception:
            return False

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

        self._connected = False
        self._tools.clear()
        self._resources.clear()

    async def _send_request(self, method: str, params: Dict) -> Optional[Dict]:
        """Send a JSON-RPC request to the server."""
        if not self._process:
            return None

        self._message_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._message_id,
            "method": method,
            "params": params,
        }

        try:
            request_line = json.dumps(request) + "\n"
            self._process.stdin.write(request_line.encode())
            self._process.stdin.flush()

            # Read response
            response_line = self._process.stdout.readline()
            if response_line:
                response = json.loads(response_line.decode())
                if "result" in response:
                    return response["result"]
                elif "error" in response:
                    return {"error": response["error"]}

        except Exception as e:
            return {"error": str(e)}

        return None

    async def _discover_tools(self) -> None:
        """Discover available tools from the server."""
        result = await self._send_request("tools/list", {})
        if result and "tools" in result:
            for tool_data in result["tools"]:
                tool = MCPTool(
                    name=tool_data["name"],
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                    server_name=self.server.name,
                )
                self._tools[tool.name] = tool

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
    ) -> MCPCallResult:
        """
        Call an MCP tool.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            MCPCallResult with the result
        """
        if not self._connected:
            return MCPCallResult(
                success=False,
                content=None,
                error="Not connected to server",
            )

        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        if result is None:
            return MCPCallResult(
                success=False,
                content=None,
                error="No response from server",
            )

        if "error" in result:
            return MCPCallResult(
                success=False,
                content=None,
                error=str(result["error"]),
            )

        return MCPCallResult(
            success=True,
            content=result.get("content", result),
        )

    def get_tools(self) -> List[MCPTool]:
        """Get all available tools."""
        return list(self._tools.values())

    def get_tool(self, name: str) -> Optional[MCPTool]:
        """Get a specific tool by name."""
        return self._tools.get(name)


class MCPToolAdapter:
    """
    Adapts MCP tools for use with Open Interpreter.

    Wraps MCP tools to be callable as standard tools within
    the interpreter's execution context.
    """

    def __init__(self, client: MCPClient):
        self.client = client

    def create_tool_function(self, tool: MCPTool) -> Callable:
        """
        Create a callable function for an MCP tool.

        Args:
            tool: The MCP tool to wrap

        Returns:
            Async callable function
        """
        async def tool_func(**kwargs) -> str:
            result = await self.client.call_tool(tool.name, kwargs)
            if result.success:
                if isinstance(result.content, str):
                    return result.content
                return json.dumps(result.content, indent=2)
            else:
                return f"Error: {result.error}"

        tool_func.__name__ = tool.name
        tool_func.__doc__ = tool.description
        return tool_func

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        Get tool definitions for LLM consumption.

        Returns:
            List of tool definitions in standard format
        """
        return [tool.to_llm_tool() for tool in self.client.get_tools()]


class MCPServerHandler:
    """
    Handles incoming MCP requests when exposing agents as servers.
    """

    def __init__(self):
        self._agents: Dict[str, Any] = {}
        self._tools: Dict[str, Callable] = {}

    def register_agent(
        self,
        agent: Any,
        name: Optional[str] = None,
        exposed_methods: Optional[List[str]] = None,
    ) -> None:
        """
        Register an agent to be exposed via MCP.

        Args:
            agent: The agent to expose
            name: Optional custom name
            exposed_methods: Methods to expose (default: execute)
        """
        agent_name = name or getattr(agent, "name", "agent")
        self._agents[agent_name] = agent

        # Create tools for exposed methods
        methods = exposed_methods or ["execute"]
        for method_name in methods:
            if hasattr(agent, method_name):
                tool_name = f"{agent_name}_{method_name}"
                self._tools[tool_name] = getattr(agent, method_name)

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        input_schema: Optional[Dict] = None,
    ) -> None:
        """
        Register a standalone tool.

        Args:
            name: Tool name
            func: Callable function
            description: Tool description
            input_schema: JSON schema for inputs
        """
        self._tools[name] = func

    def get_tools_list(self) -> List[Dict]:
        """Get list of available tools in MCP format."""
        tools = []
        for name, func in self._tools.items():
            tools.append({
                "name": name,
                "description": getattr(func, "__doc__", "") or f"Tool: {name}",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            })
        return tools

    async def handle_request(self, request: Dict) -> Dict:
        """
        Handle an incoming MCP request.

        Args:
            request: JSON-RPC request

        Returns:
            JSON-RPC response
        """
        method = request.get("method", "")
        params = request.get("params", {})
        request_id = request.get("id")

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "0.1.0",
                    "serverInfo": {
                        "name": "open-interpreter-agent",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "tools": True,
                    },
                }

            elif method == "tools/list":
                result = {"tools": self.get_tools_list()}

            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                if tool_name not in self._tools:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Tool not found: {tool_name}",
                        },
                    }

                func = self._tools[tool_name]

                # Call the tool (handle both sync and async)
                if asyncio.iscoroutinefunction(func):
                    content = await func(**arguments)
                else:
                    content = func(**arguments)

                result = {"content": content}

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }

        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": str(e),
                },
            }


class MCPBridge:
    """
    Main class for MCP integration.

    Provides methods to:
    - Connect to and use external MCP servers
    - Expose agents as MCP servers
    - Manage tool adapters

    Example:
        bridge = MCPBridge()

        # Connect to an MCP server
        await bridge.connect_server(MCPServer(
            name="filesystem",
            command="npx",
            args=["-y", "@anthropic/mcp-filesystem"],
        ))

        # Get available tools
        tools = bridge.get_all_tools()

        # Call a tool
        result = await bridge.call_tool("filesystem", "read_file", {"path": "/etc/hosts"})
    """

    def __init__(self):
        self._clients: Dict[str, MCPClient] = {}
        self._adapters: Dict[str, MCPToolAdapter] = {}
        self._server_handler = MCPServerHandler()
        self._server_running = False

    async def connect_server(
        self,
        server: Union[MCPServer, Dict[str, Any]],
    ) -> bool:
        """
        Connect to an MCP server.

        Args:
            server: MCPServer config or dict with server details

        Returns:
            True if connection successful
        """
        if isinstance(server, dict):
            server = MCPServer(**server)

        client = MCPClient(server)
        success = await client.connect()

        if success:
            self._clients[server.name] = client
            self._adapters[server.name] = MCPToolAdapter(client)

        return success

    async def disconnect_server(self, name: str) -> None:
        """
        Disconnect from an MCP server.

        Args:
            name: Server name
        """
        if name in self._clients:
            await self._clients[name].disconnect()
            del self._clients[name]
            del self._adapters[name]

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for name in list(self._clients.keys()):
            await self.disconnect_server(name)

    def get_connected_servers(self) -> List[str]:
        """Get list of connected server names."""
        return list(self._clients.keys())

    def get_all_tools(self) -> List[MCPTool]:
        """Get all available tools from all servers."""
        tools = []
        for client in self._clients.values():
            tools.extend(client.get_tools())
        return tools

    def get_tools_for_server(self, server_name: str) -> List[MCPTool]:
        """Get tools from a specific server."""
        if server_name in self._clients:
            return self._clients[server_name].get_tools()
        return []

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get all tool definitions in LLM format."""
        definitions = []
        for adapter in self._adapters.values():
            definitions.extend(adapter.get_tool_definitions())
        return definitions

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> MCPCallResult:
        """
        Call a tool on a specific server.

        Args:
            server_name: Name of the MCP server
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            MCPCallResult with the result
        """
        if server_name not in self._clients:
            return MCPCallResult(
                success=False,
                content=None,
                error=f"Server not connected: {server_name}",
            )

        return await self._clients[server_name].call_tool(tool_name, arguments)

    async def call_tool_any(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> MCPCallResult:
        """
        Call a tool, searching across all servers.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            MCPCallResult with the result
        """
        for client in self._clients.values():
            if client.get_tool(tool_name):
                return await client.call_tool(tool_name, arguments)

        return MCPCallResult(
            success=False,
            content=None,
            error=f"Tool not found: {tool_name}",
        )

    def register_agent(
        self,
        agent: Any,
        name: Optional[str] = None,
    ) -> None:
        """
        Register an agent to be exposed as MCP tools.

        Args:
            agent: The agent to expose
            name: Optional custom name
        """
        self._server_handler.register_agent(agent, name)

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
    ) -> None:
        """
        Register a tool to be exposed via MCP.

        Args:
            name: Tool name
            func: Callable function
            description: Tool description
        """
        self._server_handler.register_tool(name, func, description)

    async def start_stdio_server(self) -> None:
        """
        Start as an MCP server using stdio transport.

        Reads JSON-RPC requests from stdin and writes responses to stdout.
        """
        import sys

        self._server_running = True

        while self._server_running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                request = json.loads(line)
                response = await self._server_handler.handle_request(request)

                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

            except json.JSONDecodeError:
                continue
            except Exception as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": str(e),
                    },
                }
                sys.stdout.write(json.dumps(error_response) + "\n")
                sys.stdout.flush()

    def stop_server(self) -> None:
        """Stop the MCP server."""
        self._server_running = False

    def create_interpreter_tools(self) -> Dict[str, Callable]:
        """
        Create tool functions for use with Open Interpreter.

        Returns:
            Dict of tool_name -> callable function
        """
        tools = {}

        for adapter in self._adapters.values():
            for tool in adapter.client.get_tools():
                tools[tool.name] = adapter.create_tool_function(tool)

        return tools


# Convenience functions

async def connect_mcp_server(
    name: str,
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
) -> MCPBridge:
    """
    Quick connect to an MCP server.

    Args:
        name: Server name
        command: Command to run
        args: Command arguments
        env: Environment variables

    Returns:
        Connected MCPBridge
    """
    bridge = MCPBridge()
    server = MCPServer(
        name=name,
        command=command,
        args=args or [],
        env=env or {},
    )
    await bridge.connect_server(server)
    return bridge


def create_mcp_server_from_agent(agent: Any, name: Optional[str] = None) -> MCPBridge:
    """
    Create an MCP server from an agent.

    Args:
        agent: The agent to expose
        name: Optional server name

    Returns:
        MCPBridge configured as server
    """
    bridge = MCPBridge()
    bridge.register_agent(agent, name)
    return bridge
