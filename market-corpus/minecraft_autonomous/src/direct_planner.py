"""Thin selection adapter; all provider protocols and OAuth live in the shared package."""

from dataclasses import asdict

from agent_mc_autonomy.model_config import AuthConfig, HttpPlannerConfig, planner_settings
from agent_mc_autonomy.planner import HttpPlanner

from .neko_planner import NekoPlanner


def public_planner(raw):
    parsed = planner_settings(raw)
    if parsed:
        return {"route": "direct", **asdict(parsed)}
    return {"route": "host", **asdict(HttpPlannerConfig()), **raw, "auth": asdict(AuthConfig.parse(raw.get("auth", {})))}


def create_planner(raw, models, settings):
    direct = planner_settings(raw.get("planner", {}))
    if direct is None:
        return NekoPlanner(models, settings.max_output_tokens)
    planner = HttpPlanner(direct, settings.max_output_tokens, settings.model_timeout_seconds)
    if settings.fast.mode == "host":
        return DirectWithHostFast(planner, NekoPlanner(models, settings.max_output_tokens))
    return planner


class DirectWithHostFast:
    """A separate fast model slot remains owned by Neko when explicitly selected."""

    def __init__(self, planner, host):
        self.planner, self.host = planner, host

    async def decide(self, messages, tools):
        return await self.planner.decide(messages, tools)

    def validate_profiles(self,policy):
        self.planner.validate_profiles(policy)

    async def decide_profile(self,messages,tools,profile):
        return await self.planner.decide_profile(messages,tools,profile)

    async def select_fast(self, state):
        return await self.host.select_fast(state)

    async def aclose(self):
        try:
            await self.planner.aclose()
        finally:
            await self.host.aclose()
