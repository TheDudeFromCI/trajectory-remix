"""REST route for the packaged generation prompts (Settings → Prompts → Load default)."""

from .models import GenerationPrompts


def add_prompt_routes(router):
    @router.get("/generation/prompts/default")
    def default_prompts():
        """The packaged prompts. Nothing is saved; the Settings page applies them."""
        return GenerationPrompts().model_dump(mode="json")
