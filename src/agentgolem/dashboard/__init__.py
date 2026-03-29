"""AgentGolem dashboard — FastAPI REST API for monitoring and control."""
from agentgolem.dashboard.api import DashboardState, create_app

__all__ = ["create_app", "DashboardState"]