"""Constrained scenario DSL; models cannot emit executable commands."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Trusted fixture and grader constants: the generator cannot override these.
HEALTHY_CPU_REQUEST = "10m"
HEALTHY_CPU_LIMIT = "250m"
BROKEN_CPU_REQUEST = "16000m"
BROKEN_CPU_LIMIT = "16000m"


class Domain(str, Enum):
    workloads = "workloads"
    networking = "networking"


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    slug: str = Field(min_length=3, max_length=44, pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$")
    title: str = Field(min_length=8, max_length=100)
    story: str = Field(min_length=12, max_length=320)
    difficulty: Difficulty
    minutes: int = Field(ge=3, le=20)
    namespace: str = Field(min_length=7, max_length=55, pattern=r"^cka-[a-z][a-z0-9-]*[a-z0-9]$")
    deployment: str = Field(min_length=3, max_length=45, pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$")
    scenario_type: Literal["deployment_scale", "service_selector", "deployment_resources"]
    initial_replicas: int = Field(ge=1, le=3)
    target_replicas: int = Field(ge=1, le=4)
    initial_selector: str = Field(min_length=3, max_length=45, pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$")
    target_selector: str = Field(min_length=3, max_length=45, pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$")

    @model_validator(mode="after")
    def validate_impact(self):
        if self.scenario_type == "deployment_scale":
            if self.initial_replicas == self.target_replicas:
                raise ValueError("Scale scenario must start in an incorrect state")
            if self.initial_selector != self.target_selector:
                raise ValueError("Scale scenario selectors must remain correct")
        if self.scenario_type == "deployment_resources":
            if self.initial_replicas != self.target_replicas:
                raise ValueError("CPU scheduling scenario must preserve replicas")
            if self.initial_selector != self.target_selector:
                raise ValueError("CPU scheduling scenario must preserve selectors")
        if self.scenario_type == "service_selector":
            if self.initial_selector == self.target_selector:
                raise ValueError("Service scenario must start with a broken selector")
            if self.initial_replicas != self.target_replicas:
                raise ValueError("Service scenario replicas must remain correct")
        suspicious = (chr(96) * 3, "http://", "https://", "<script", "</", "sudo ", "curl ", "wget ")
        if any(fragment in (self.title + " " + self.story).lower() for fragment in suspicious):
            raise ValueError("Scenario display text contains forbidden elements")
        return self

    @property
    def domain(self) -> Domain:
        return Domain.networking if self.scenario_type == "service_selector" else Domain.workloads

    @property
    def service(self) -> str:
        return f"{self.deployment}-svc"

    @property
    def statement(self) -> str:
        if self.scenario_type == "deployment_scale":
            return (f"En el namespace {self.namespace}, ajusta el Deployment "
                    f"{self.deployment} a {self.target_replicas} réplicas disponibles.")
        if self.scenario_type == "deployment_resources":
            return (f"En {self.namespace}, los pods del Deployment {self.deployment} "
                    "no se pueden programar por una petición de CPU excesiva. "
                    f"Ajusta el contenedor web a requests.cpu={HEALTHY_CPU_REQUEST} "
                    f"y limits.cpu={HEALTHY_CPU_LIMIT}, sin cambiar el número de "
                    "réplicas ni el selector. Deja todas las réplicas disponibles.")
        return (f"En {self.namespace}, repara el Service {self.service} para "
                f"enrutar tráfico a los pods del Deployment {self.deployment}. "
                "No alteres las réplicas ni elimines el Service.")

    def candidate_view(self) -> dict:
        return {
            "slug": self.slug, "title": self.title, "story": self.story,
            "difficulty": self.difficulty.value, "minutes": self.minutes,
            "domain": self.domain.value, "statement": self.statement,
            "namespace": self.namespace,
        }
