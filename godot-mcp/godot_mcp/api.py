"""공개 고수준 API (MCP Tool 과 CLI 가 동일하게 사용)."""
from .api_check import errors, syntax_check, validate
from .api_project import add_mechanic, create_game, create_project, create_scene, modify_game
from .api_runtime import game_state, play, run, screenshot, stop
from .api_view import inspect, project_state
from .build.exporter import build
from .fixing.fixer import fix
from .mechanics.registry import supported as supported_mechanics
from .testing import test

__all__ = ["add_mechanic", "build", "create_game", "create_project", "create_scene", "errors", "fix", "game_state", "inspect", "modify_game", "play",
           "project_state", "run", "screenshot", "stop", "supported_mechanics", "syntax_check", "test", "validate"]
