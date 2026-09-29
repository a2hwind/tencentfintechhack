"""Platform adapters: one interface per platform, mock and real behind the same shape."""

from .base import AdapterError, HttpAdapter, PlatformAdapter
from .confluence import ConfluenceAdapter
from .gdrive import GoogleDriveAdapter
from .jira import JiraAdapter
from .slack import SlackAdapter

__all__ = [
    "AdapterError",
    "HttpAdapter",
    "PlatformAdapter",
    "ConfluenceAdapter",
    "JiraAdapter",
    "SlackAdapter",
    "GoogleDriveAdapter",
]
