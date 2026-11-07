"""Inspectable MCP catalogs and context operations through the configured bridge."""
import json
import click


def register_mcp_commands(group, client_factory, require_ok):
    def execute(method, *args, **kwargs):
        client = client_factory()
        try:
            response = require_ok(getattr(client, method)(*args, **kwargs))
        finally:
            client.close()
        click.echo(json.dumps(response.get("result", response), ensure_ascii=False, indent=2))

    @group.command(name="catalog")
    @click.argument("server")
    @click.option("--kind", type=click.Choice(["tools", "resources", "resource_templates", "prompts"]), default="tools", show_default=True)
    @click.option("--cursor", default="", help="Exact next_cursor returned by the preceding page.")
    @click.option("--workspace", default="")
    def catalog(server, kind, cursor, workspace):
        """Read one live MCP page with its schemas and continuation cursor."""
        execute("mcp_catalog", server, kind, cursor=cursor, workspace=workspace)

    @group.command(name="resources")
    @click.argument("server")
    @click.option("--cursor", default="")
    @click.option("--workspace", default="")
    def resources(server, cursor, workspace):
        """List resources exposed by an actual MCP server."""
        execute("mcp_catalog", server, "resources", cursor=cursor, workspace=workspace)

    @group.command(name="prompts")
    @click.argument("server")
    @click.option("--cursor", default="")
    @click.option("--workspace", default="")
    def prompts(server, cursor, workspace):
        """List user-selectable prompt templates."""
        execute("mcp_catalog", server, "prompts", cursor=cursor, workspace=workspace)

    @group.command(name="read")
    @click.argument("server")
    @click.argument("uri")
    @click.option("--workspace", default="")
    def read_resource(server, uri, workspace):
        """Read a listed or user-supplied resource URI; content stays task data."""
        execute("mcp_read_resource", server, uri, workspace=workspace)

    @group.command(name="prompt")
    @click.argument("server")
    @click.argument("name")
    @click.option("--json-args", default="{}", help="JSON object of string arguments.")
    @click.option("--workspace", default="")
    def get_prompt(server, name, json_args, workspace):
        """Retrieve a selected prompt without installing it as system instructions."""
        try:
            arguments = json.loads(json_args)
        except ValueError as exc:
            raise click.ClickException("Invalid prompt argument JSON") from exc
        if not isinstance(arguments, dict) or any(not isinstance(v, str) for v in arguments.values()):
            raise click.ClickException("Prompt arguments must be a JSON object of strings")
        execute("mcp_get_prompt", server, name, arguments, workspace=workspace)

    @group.command(name="close")
    @click.argument("server")
    @click.option("--workspace", default="")
    def close_session(server, workspace):
        """Release one idle MCP session; never replay an interrupted call."""
        execute("mcp_close", server, workspace=workspace)
