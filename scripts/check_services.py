"""
Service Connection Checker
Verifies all configured API connections are working.

Usage:
    python scripts/check_services.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

console = Console()


def check_blockfrost() -> tuple[bool, str]:
    """Check Blockfrost API connection."""
    try:
        from src.blockchain import CardanoClient
        client = CardanoClient()
        healthy = client.check_health()
        if healthy:
            block = client.get_latest_block()
            return True, f"Connected ({client.network}) — Block #{block['height']}"
        return False, "API returned unhealthy"
    except ValueError as e:
        return False, f"Not configured — {e}"
    except Exception as e:
        return False, f"Error — {e}"


def check_supabase() -> tuple[bool, str]:
    """Check Supabase connection."""
    try:
        from src.utils import get_settings
        settings = get_settings()
        if not settings.supabase_url or not settings.supabase_anon_key:
            return False, "Not configured (set SUPABASE_URL and SUPABASE_ANON_KEY in .env)"
        from supabase import create_client
        client = create_client(settings.supabase_url, settings.supabase_anon_key)
        return True, f"Connected — {settings.supabase_url}"
    except ImportError:
        return False, "supabase package not installed"
    except Exception as e:
        return False, f"Error — {e}"


def check_mongodb() -> tuple[bool, str]:
    """Check MongoDB Atlas connection."""
    try:
        from src.utils import get_settings
        settings = get_settings()
        if not settings.mongodb_uri:
            return False, "Not configured (set MONGODB_URI in .env)"
        from pymongo import MongoClient
        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        return True, "Connected"
    except ImportError:
        return False, "pymongo package not installed"
    except Exception as e:
        return False, f"Error — {e}"


def check_sentry() -> tuple[bool, str]:
    """Check Sentry DSN configuration."""
    try:
        from src.utils import get_settings
        settings = get_settings()
        if not settings.sentry_dsn:
            return False, "Not configured (set SENTRY_DSN in .env)"
        return True, f"Configured — {settings.sentry_dsn[:40]}..."
    except Exception as e:
        return False, f"Error — {e}"


def main():
    console.print("\n[bold cyan]Service Connection Check[/bold cyan]\n")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Service", style="bold", width=20)
    table.add_column("Status", width=10)
    table.add_column("Details", width=60)

    checks = [
        ("Blockfrost (Cardano)", check_blockfrost),
        ("Supabase (PostgreSQL)", check_supabase),
        ("MongoDB Atlas", check_mongodb),
        ("Sentry (Errors)", check_sentry),
    ]

    all_ok = True
    for name, check_fn in checks:
        ok, detail = check_fn()
        status = "[green]✓ OK[/green]" if ok else "[yellow]✗ Skip[/yellow]"
        if not ok:
            all_ok = False
        table.add_row(name, status, detail)

    console.print(table)

    if all_ok:
        console.print("\n[bold green]All services connected![/bold green]\n")
    else:
        console.print(
            "\n[yellow]Some services are not configured yet.[/yellow]"
            "\nEdit your [bold].env[/bold] file to add missing API keys.\n"
        )


if __name__ == "__main__":
    main()
