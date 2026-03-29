"""Identity subsystem — soul and heartbeat management."""
from agentgolem.identity.heartbeat import HeartbeatManager, HeartbeatSummary, HeartbeatEntry

__all__ = ["HeartbeatManager", "HeartbeatSummary", "HeartbeatEntry"]

try:
    from agentgolem.identity.soul import SoulManager, SoulUpdate, SoulVersion
    __all__ += ["SoulManager", "SoulUpdate", "SoulVersion"]
except ImportError:
    pass