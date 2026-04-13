"""bitbucket-dc-mcp: a hardened MCP server for Bitbucket Data Center.

Public API exposed here is limited and subject to change until 1.0.
Most users should invoke the server via the console script
`bitbucket-dc-mcp`, which calls `bitbucket_dc_mcp.server.run`.
"""

from importlib.metadata import PackageNotFoundError, version

from .config import ConfigError, ServerConfig, load_config
from .server import run, serve
from .validation import ValidationError

try:
    __version__ = version("bitbucket-dc-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "ConfigError",
    "ServerConfig",
    "ValidationError",
    "__version__",
    "load_config",
    "run",
    "serve",
]
