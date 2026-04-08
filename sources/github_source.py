"""
sources/github_source.py — GitHub REST API source

Re-exports GitHubSource from database_source for clean imports.
In a larger codebase this would be its own file with the full implementation.
"""
from sources.database_source import GitHubSource

__all__ = ["GitHubSource"]
