"""Developer commands:

    flask --app wsgi usage                 API usage totals (no message content)
    flask --app wsgi usage --rows          + per-conversation counters
    flask --app wsgi prompt plato          print the runtime system prompt + size
    flask --app wsgi compare-controller plato "그냥 네가 정해줘. 헤어질까?"
                                           controller on vs off (mock unless --live)
"""

from __future__ import annotations

import json

import click
from flask import Flask, current_app

from .ai import prompts
from .data import get_repository
from .philosophers import get_philosopher, philosopher_ids
from .usage import aggregate, rows_without_ids


def init_app(app: Flask) -> None:
    @app.cli.command("usage")
    @click.option("--rows", is_flag=True, help="Show per-conversation counters.")
    @click.option("--limit", default=500, show_default=True)
    def usage_command(rows, limit):
        """Show Anthropic API usage counters stored on conversations."""
        repo = get_repository()
        click.echo(json.dumps(aggregate(repo, limit=limit), ensure_ascii=False, indent=2))
        if rows:
            for row in rows_without_ids(repo, limit=50):
                click.echo(json.dumps(row, ensure_ascii=False))

    @app.cli.command("prompt")
    @click.argument("philosopher_id", type=click.Choice(philosopher_ids()))
    @click.option("--no-controller", is_flag=True)
    def prompt_command(philosopher_id, no_controller):
        """Print the runtime system prompt (development only)."""
        text = prompts.build_system_prompt(get_philosopher(philosopher_id),
                                           controller_enabled=not no_controller,
                                           controller_version=current_app.config["CONTROLLER_VERSION"])
        click.echo(text)
        click.echo(f"\n--- {len(text)} chars", err=True)

    @app.cli.command("compare-controller")
    @click.argument("philosopher_id", type=click.Choice(philosopher_ids()))
    @click.argument("message")
    @click.option("--live", is_flag=True,
                  help="Call the real API (requires AI_MODE=anthropic). Costs 2 requests.")
    def compare_controller(philosopher_id, message, live):
        """Same message with controller_enabled=True vs False (class evaluation)."""
        from .ai import get_provider
        from .ai.mock import MockProvider

        cfg = current_app.config
        if live and cfg["AI_MODE"] != "anthropic":
            raise click.UsageError("--live requires AI_MODE=anthropic")
        provider = get_provider() if live else MockProvider()
        p = get_philosopher(philosopher_id)
        for enabled in (True, False):
            system = prompts.build_system_prompt(p, controller_enabled=enabled,
                                                 controller_version=cfg["CONTROLLER_VERSION"])
            reply = provider.reply(system=system, messages=[{"role": "user", "content": message}],
                                   philosopher=p, max_tokens=cfg["MAX_OUTPUT_TOKENS"])
            click.echo(f"\n[controller_enabled={enabled}] provider={reply.provider} "
                       f"in={reply.usage.input_tokens} out={reply.usage.output_tokens}")
            click.echo(reply.text)
