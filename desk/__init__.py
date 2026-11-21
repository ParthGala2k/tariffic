"""Brewline Tariff Desk - the system of work.

Phase 1: work items, permissions, transitions, append-only audit log,
the gateway, a minimal sync layer and the Seat 1 agent harness.
"""

__all__ = ["config", "db", "perms", "workflow", "gateway", "sync", "llm", "agent"]
