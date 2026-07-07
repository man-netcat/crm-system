import json
import os
import sys

import click

from .schema import SchemaDef
from .db import create_database, insert_extracted, query_data
from .extractor import extract_from_email, generate_schema_from_prompt
from .email_input import from_text, from_eml, from_stdin, IMAPWatcher
from .auth import PlainAuth, OAuth2DeviceAuth


@click.group()
def cli():
    """email-parser — Extract structured data from emails using local AI."""


@cli.command()
@click.argument("schema_file", type=click.Path(exists=True), required=False)
@click.option("--prompt", "-p", help="Infer schema from a natural language prompt")
@click.option("--output", "-o", default="inferred_schema.yaml", show_default=True, help="Save inferred schema to this file")
@click.option("--model", "-m", default="llama3.2", show_default=True, help="Ollama model name")
@click.option("--ollama-host", default="http://localhost:11434", show_default=True, help="Ollama server URL")
def init(schema_file, prompt, output, model, ollama_host):
    """Create the database. Provide a schema YAML file OR use --prompt to infer one."""
    if prompt:
        click.echo(f"Inferring schema from prompt using {model} ...")
        yaml_content = generate_schema_from_prompt(prompt, model=model, ollama_host=ollama_host)
        with open(output, "w") as f:
            f.write(yaml_content)
        click.echo(f"Schema saved to {output}")
        schema = SchemaDef.from_yaml(output)
    elif schema_file:
        schema = SchemaDef.from_yaml(schema_file)
    else:
        click.echo("Provide a schema YAML file or use --prompt to infer one.", err=True)
        sys.exit(1)

    db_path = create_database(schema)
    tables = [t.name for t in schema.tables]
    click.echo(f"Created database: {db_path}")
    click.echo(f"Tables: {', '.join(tables)}")


@cli.command()
@click.argument("schema_file", type=click.Path(exists=True))
@click.option("--text", "-t", help="Email content as a text string")
@click.option("--file", "-f", "filepath", type=click.Path(exists=True), help="Path to a .eml file")
@click.option("--stdin", "-s", "read_stdin", is_flag=True, help="Read email from stdin")
@click.option("--model", "-m", default="llama3.2", show_default=True, help="Ollama model name")
@click.option("--ollama-host", default="http://localhost:11434", show_default=True, help="Ollama server URL")
def parse(schema_file, text, filepath, read_stdin, model, ollama_host):
    """Extract data from an email and store it in the database."""
    schema = SchemaDef.from_yaml(schema_file)

    if text:
        content = from_text(text)
    elif filepath:
        content = from_eml(filepath)
    elif read_stdin:
        content = from_stdin()
    else:
        click.echo("Provide email content via --text, --file, or --stdin", err=True)
        sys.exit(1)

    if not content:
        click.echo("No email content found.", err=True)
        sys.exit(1)

    click.echo(f"Extracting data using {model} ...")
    try:
        extracted = extract_from_email(schema, content, model=model, ollama_host=ollama_host)
    except Exception as e:
        click.echo(f"Extraction failed: {e}", err=True)
        sys.exit(1)

    counts = insert_extracted(schema, extracted)
    total_rows = sum(counts.values())
    for table_name, count in counts.items():
        if count:
            click.echo(f"  {table_name}: {count} row(s) inserted")

    click.echo(f"\nDone. {total_rows} total row(s) inserted.")


@cli.command()
@click.option("--provider", "-p", required=True, help="Auth provider (gmail, outlook)")
@click.option("--user", "-u", help="Email address (will prompt if not provided)")
def connect(provider, user):
    """Authorize with an OAuth2 provider and store credentials for later use."""
    try:
        auth = OAuth2DeviceAuth(provider, email=user)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"\nConnected as {auth.get_username()}")
    click.echo(f"Provider: {auth.name()}")
    click.echo(f"IMAP: {auth.imap_server}:{auth.imap_port}")
    click.echo("\nCredentials saved. You can now use:")
    click.echo(f"  email-parser watch <schema.yaml> --provider {auth.name()} --user {auth.get_username()}")


@cli.command()
@click.argument("schema_file", type=click.Path(exists=True))
@click.option("--provider", help="Auth provider (gmail, outlook) — overrides --server/--user/--password")
@click.option("--server", help="IMAP server address (required without --provider)")
@click.option("--user", help="IMAP username")
@click.option("--password", help="IMAP password (defaults to IMAP_PASSWORD env var)")
@click.option("--port", default=993, type=int, show_default=True, help="IMAP server port")
@click.option("--no-ssl", "use_ssl", flag_value=False, default=True, help="Disable SSL (use plain IMAP)")
@click.option("--folder", default="INBOX", show_default=True, help="IMAP folder")
@click.option("--interval", default=60, type=int, show_default=True, help="Poll interval in seconds")
@click.option("--model", "-m", default="llama3.2", show_default=True, help="Ollama model name")
@click.option("--ollama-host", default="http://localhost:11434", show_default=True, help="Ollama server URL")
def watch(schema_file, provider, server, user, password, port, use_ssl, folder, interval, model, ollama_host):
    """Watch an IMAP inbox and extract data from incoming emails."""
    schema = SchemaDef.from_yaml(schema_file)
    auth_provider = None

    if provider:
        auth_provider = OAuth2DeviceAuth(provider, email=user)
        user = auth_provider.get_username()
        server = auth_provider.imap_server
        port = auth_provider.imap_port
        use_ssl = auth_provider.use_ssl
    else:
        if not server:
            click.echo("Either --provider or --server is required.", err=True)
            sys.exit(1)
        password = password or os.environ.get("IMAP_PASSWORD")
        if not password:
            click.echo("Password required via --password or IMAP_PASSWORD env var", err=True)
            sys.exit(1)
        auth_provider = PlainAuth(user or "", password) if password else None

    def process_email(email_data: dict):
        body = email_data.get("body", "")
        if not body:
            return
        click.echo(f"\nNew email: {email_data.get('subject', '(no subject)')}")
        try:
            extracted = extract_from_email(schema, body, model=model, ollama_host=ollama_host)
            counts = insert_extracted(schema, extracted)
            for table_name, count in counts.items():
                if count:
                    click.echo(f"  {table_name}: {count} row(s) inserted")
        except Exception as e:
            click.echo(f"  Failed: {e}")

    watcher = IMAPWatcher(
        server=server,
        user=user or "",
        password=password or "",
        folder=folder,
        port=port,
        use_ssl=use_ssl,
        interval=interval,
        on_email=process_email,
        auth_provider=auth_provider,
    )
    watcher.run()


@cli.command()
@click.argument("schema_file", type=click.Path(exists=True))
@click.option("--table", "-t", help="Show only this table")
@click.option("--limit", "-l", default=50, type=int, show_default=True, help="Max rows per table")
@click.option("--json", "-j", "as_json", is_flag=True, help="Output as JSON")
def list(schema_file, table, limit, as_json):
    """Display extracted data stored in the database."""
    schema = SchemaDef.from_yaml(schema_file)
    results = query_data(schema, table_name=table, limit=limit)

    if as_json:
        click.echo(json.dumps(results, indent=2, default=str))
        return

    for table_name, rows in results.items():
        click.echo(f"\n=== {table_name} ({len(rows)} rows) ===")
        if not rows:
            click.echo("  (empty)")
            continue
        col_names = [c for c in rows[0].keys() if c != "id"]
        header = " | ".join(col_names)
        click.echo(header)
        click.echo("-" * len(header))
        for row in rows:
            vals = " | ".join(str(row.get(c, "")) for c in col_names)
            click.echo(vals)
