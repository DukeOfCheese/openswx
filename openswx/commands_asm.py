"""Assembly configuration and display-state commands (same registry as session.py, which re-exports them).
The configurations themselves are added / activated / deleted with the commands parts use."""
from __future__ import annotations

from .session import APP, command

__all__ = ["suppress_component", "save_display_state", "activate_display_state", "delete_display_state"]


@command(category="assembly")
def suppress_component(name: str, suppressed: bool = True, configuration: str | None = None) -> dict:
    """Suppress (or, with suppressed=False, bring back) a component in a configuration of the assembly
    (default: the active one). A suppressed component is not loaded into that configuration: it takes no part
    in mates, mass, the BOM, interference checks or drawings, and the mates on it are suppressed with it.
    Other configurations are not affected - make them with add_configuration."""
    APP.asm.suppress_component(name, suppressed, configuration)
    return APP.asm.summary()


@command(category="assembly")
def save_display_state(name: str, hidden: list[str] | None = None) -> dict:
    """Display State: save which components are hidden under a name (an existing state of that name is
    replaced) and make it the active one. Hidden components are only not drawn - unlike suppressed ones they
    stay in the assembly, its mates and its BOM."""
    APP.asm.save_display_state(name, list(hidden or []))
    return APP.asm.summary()


@command(category="assembly")
def activate_display_state(name: str | None = None) -> dict:
    """Show the assembly as a saved display state has it (name=None: every component shown again)."""
    APP.asm.activate_display_state(name)
    return APP.asm.summary()


@command(category="assembly")
def delete_display_state(name: str) -> dict:
    """Delete a saved display state."""
    APP.asm.delete_display_state(name)
    return APP.asm.summary()
