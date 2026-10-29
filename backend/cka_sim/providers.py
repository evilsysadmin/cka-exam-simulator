"""Switch generation providers without changing the validated scenario contract."""
from . import ollama, workers_ai
from .models import Scenario

DEFAULT_PROVIDER = "ollama"
SUPPORTED_PROVIDERS = ("ollama", "workers-ai")


def default_model(provider: str) -> str:
    if provider == "workers-ai":
        return workers_ai.DEFAULT_MODEL
    if provider == "ollama":
        return "qwen2.5:7b-instruct"
    raise ValueError("Unsupported scenario provider")


def generate(*, provider: str, model: str,
             scenario_type: str = "deployment_scale") -> Scenario:
    if provider == "ollama":
        return ollama.generate(model=model, scenario_type=scenario_type)
    if provider == "workers-ai":
        return workers_ai.generate(model=model, scenario_type=scenario_type)
    raise ValueError("Unsupported scenario provider")
