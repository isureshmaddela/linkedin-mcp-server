"""``python -m aira_cloud``: run the hosted shell.

serve        the web tier (add --worker to run a worker pool and the scheduler in the same process)
worker       a worker pool and the scheduler tick
tick         one scheduler pass (billing, retention, due work), then exit
migrate      apply control-plane migrations, then exit
migrate-to-postgres  copy the SQLite control plane into an empty PostgreSQL database
operator     create a platform operator:  operator --email you@vendor.test --role platform_admin
backup-org   write one organization's backup archive;  restore-org restores it (only that organization)
rotate-keys  rotate one organization's data key
init-storage create the documents bucket on an S3-compatible store if it is missing
rewrap-keys  re-wrap every data key under a new master key (local file, AWS KMS or Vault); --verify-only checks first
seed-demo    create demo organizations with every role (development only)
check        print readiness information
broker       the sign-in broker: streams a candidate's private browser to them for LinkedIn sign-in
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import signal
import sys
import threading

from aira.observability import configure_logging


def _app(args: argparse.Namespace):  # noqa: ANN202
    from aira_cloud.app import CloudApp
    from aira_cloud.config import CloudConfig

    config = CloudConfig()
    if getattr(args, "data", None):
        from pathlib import Path

        config.data_root = Path(args.data)
    if getattr(args, "port", None):
        config.port = args.port
        config.base_url = (
            f"http://{config.host}:{config.port}"
            if "AIRA_CLOUD_BASE_URL" not in os.environ
            else config.base_url
        )
    return CloudApp(config)


def cmd_serve(args: argparse.Namespace) -> int:
    from aira_cloud.gateway import Gateway, make_server
    from aira_cloud.worker import Worker

    app = _app(args)
    gateway = Gateway(app)
    server = make_server(gateway)
    worker = None
    agent = None
    if args.worker:
        agent = _start_agent(app, gateway, args)
        if agent:
            app.agents.local_url = agent.url  # this process is both web tier and worker: its own sessions run in-process
        worker = Worker(app, threads=args.threads, agent=agent)
        worker.start()
    scheme = "https" if app.config.tls_cert else "http"
    logging.getLogger("aira_cloud").info(
        "Monosage cloud serving on %s://%s:%d (region %s)",
        scheme,
        app.config.host,
        server.server_address[1],
        app.config.region,
    )
    stop = threading.Event()

    def shutdown(*_args: object) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (ValueError, AttributeError):
        pass
    try:
        server.serve_forever()
    finally:
        if worker:
            worker.stop()
        if agent:
            agent.stop()
        server.server_close()
        app.close()
    return 0


def _start_agent(app, gateway, args: argparse.Namespace):  # noqa: ANN001, ANN202
    """A worker holds candidates' LinkedIn sessions, so it also runs an agent the web tier can forward to."""
    from aira_cloud.agent import AgentServer

    explicit = getattr(args, "agent_port", None)
    if explicit is None and not app.config.agent_port:
        return None
    port = explicit if explicit is not None else app.config.agent_port
    return AgentServer(
        app,
        gateway,
        host=app.config.agent_host,
        port=port,
        advertise=app.config.agent_advertise_url,
    ).start()


def cmd_worker(args: argparse.Namespace) -> int:
    from aira_cloud.gateway import Gateway
    from aira_cloud.worker import Worker

    app = _app(args)
    agent = _start_agent(app, Gateway(app), args)
    worker = Worker(app, threads=args.threads, agent=agent)
    worker.start()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    except (ValueError, AttributeError):
        pass
    stop.wait()
    worker.stop()
    if agent:
        agent.stop()
    app.close()
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    app = _app(args)
    print(json.dumps(app.lifecycle.tick(), indent=2, default=str))
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    app = _app(args)
    print(f"control plane at schema version {app.db.version()}")
    return 0


def cmd_migrate_to_postgres(args: argparse.Namespace) -> int:
    """Copy this installation's SQLite control plane into an empty PostgreSQL database."""
    from pathlib import Path

    from aira_cloud.config import CloudConfig
    from aira_cloud.migrate_pg import MigrationError, migrate

    config = CloudConfig()
    if getattr(args, "data", None):
        config.data_root = Path(args.data)
    source = Path(args.sqlite) if args.sqlite else config.db_path
    if not (config.database_url and config.database_admin_url):
        print(
            "Set AIRA_CLOUD_DATABASE_URL, AIRA_CLOUD_DATABASE_PLATFORM_URL and AIRA_CLOUD_DATABASE_ADMIN_URL first.",
            file=sys.stderr,
        )
        return 2
    try:
        result = migrate(
            source,
            config.database_url,
            config.database_platform_url,
            config.database_admin_url,
            verify_only=args.verify_only,
        )
    except MigrationError as error:
        print(f"migration failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


def cmd_operator(args: argparse.Namespace) -> int:
    app = _app(args)
    password = (
        args.password
        or os.getenv("AIRA_CLOUD_OPERATOR_PASSWORD")
        or getpass.getpass("Password (12+ characters): ")
    )
    operator_id = app.identity.create_operator(
        args.email, password, role=args.role, name=args.name or "", by="cli"
    )
    print(
        f"created {args.role} {args.email} ({operator_id}); sign in at /#/platform and set up MFA"
    )
    return 0


def cmd_broker(args: argparse.Namespace) -> int:
    from aira_cloud.config import CloudConfig
    from aira_cloud.streaming import run_forever

    config = CloudConfig()
    if args.host:
        config.broker_host = args.host
    if args.port:
        config.broker_port = args.port
    if not config.broker_secret:
        print(
            "AIRA_CLOUD_BROKER_SECRET must be set (the same value as the web tier).",
            file=sys.stderr,
        )
        return 2
    run_forever(config)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    app = _app(args)
    print(
        json.dumps(
            {
                "schema_version": app.db.version(),
                "region": app.config.region,
                "data_root": str(app.config.data_root),
                "billing": app.config.billing_provider,
                "storage": app.config.storage_backend,
                "model": app.config.model_provider,
                "queue": app.queue.stats()["by_status"],
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_backup_org(args: argparse.Namespace) -> int:
    from pathlib import Path

    app = _app(args)
    data = app.backup.backup_org(args.org)
    Path(args.out).write_bytes(data)
    print(
        f"wrote {args.out} ({len(data):,} bytes): {args.org}. Keep it apart from the master key; it holds encrypted data and wrapped keys."
    )
    return 0


def cmd_restore_org(args: argparse.Namespace) -> int:
    from pathlib import Path

    app = _app(args)
    data = Path(args.file).read_bytes()
    if args.verify_only:
        print(
            json.dumps(
                app.backup.verify(data, allow_unsigned=args.allow_unsigned),
                indent=2,
                default=str,
            )
        )
        return 0
    print(
        json.dumps(
            app.backup.restore_org(
                data, replace=args.replace, allow_unsigned=args.allow_unsigned
            ),
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_init_storage(args: argparse.Namespace) -> int:
    from aira_cloud.config import CloudConfig
    from aira_cloud.storage import ensure_bucket

    try:
        print(ensure_bucket(CloudConfig()))
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def cmd_rotate_keys(args: argparse.Namespace) -> int:
    app = _app(args)
    print(json.dumps(app.lifecycle.rotate_org_keys(args.org), indent=2))
    return 0


def cmd_rewrap_keys(args: argparse.Namespace) -> int:
    from aira_cloud.kms import KmsSettings, build_master_key

    app = _app(
        args
    )  # built with the NEW master key (the current AIRA_CLOUD_KMS / AIRA_CLOUD_MASTER_KEY settings)
    old_settings = KmsSettings.from_old_environment()
    old_file = app.config.data_root / "secrets" / "master.key"
    if (
        old_settings.kind == "local"
        and not old_settings.master_key
        and not old_file.exists()
    ):
        print(
            "No old master key: set AIRA_CLOUD_MASTER_KEY_OLD (or AIRA_CLOUD_KMS_OLD and its settings).",
            file=sys.stderr,
        )
        return 2
    old = build_master_key(old_settings, app.config.data_root)
    result = app.secrets.rewrap_all(
        old, app.secrets.provider, verify_only=args.verify_only
    )
    if result["applied"]:
        app.platform_audit.record(
            "platform.keys_rewrapped",
            operator_label="cli",
            source="cli",
            detail={
                "from": old_settings.kind,
                "to": app.config.kms,
                "keys": result["keys"],
                "organizations": result["organizations"],
            },
        )
    print(json.dumps(result, indent=2))
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from aira_cloud.demo import seed_demo

    app = _app(args)
    print(json.dumps(seed_demo(app), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aira_cloud",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data", help="data directory (default: AIRA_CLOUD_DATA or ~/.aira-cloud)"
    )
    parser.add_argument(
        "--log-level", default=os.getenv("AIRA_CLOUD_LOG_LEVEL", "INFO")
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        default=os.getenv("AIRA_CLOUD_JSON_LOGS", "0") == "1",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the web tier")
    serve.add_argument("--port", type=int)
    serve.add_argument(
        "--worker", action="store_true", help="also run a worker pool and the scheduler"
    )
    serve.add_argument("--threads", type=int, default=None)
    serve.add_argument(
        "--agent-port",
        type=int,
        default=None,
        help="with --worker: also run a worker agent on this port",
    )
    serve.set_defaults(handler=cmd_serve)
    worker = commands.add_parser("worker", help="run a worker pool")
    worker.add_argument("--threads", type=int, default=None)
    worker.add_argument(
        "--agent-port",
        type=int,
        default=None,
        help="run a worker agent on this port (0 = AIRA_CLOUD_AGENT_PORT)",
    )
    worker.set_defaults(handler=cmd_worker)
    commands.add_parser("tick", help="one scheduler pass").set_defaults(
        handler=cmd_tick
    )
    commands.add_parser("migrate", help="apply migrations").set_defaults(
        handler=cmd_migrate
    )
    to_pg = commands.add_parser(
        "migrate-to-postgres",
        help="copy the SQLite control plane into an empty PostgreSQL database",
    )
    to_pg.add_argument(
        "--sqlite", help="path to control.sqlite3 (default: under the data directory)"
    )
    to_pg.add_argument(
        "--verify-only", action="store_true", help="compare row counts, write nothing"
    )
    to_pg.set_defaults(handler=cmd_migrate_to_postgres)
    commands.add_parser("check", help="print readiness information").set_defaults(
        handler=cmd_check
    )
    operator = commands.add_parser("operator", help="create a platform operator")
    operator.add_argument("--email", required=True)
    operator.add_argument(
        "--role", choices=["support", "platform_admin"], default="platform_admin"
    )
    operator.add_argument("--name")
    operator.add_argument("--password")
    operator.set_defaults(handler=cmd_operator)
    backup = commands.add_parser(
        "backup-org", help="write one organization's backup archive"
    )
    backup.add_argument("--org", required=True)
    backup.add_argument("--out", required=True)
    backup.set_defaults(handler=cmd_backup_org)
    restore = commands.add_parser(
        "restore-org",
        help="restore one organization from a backup (touches only that organization)",
    )
    restore.add_argument("--file", required=True)
    restore.add_argument(
        "--replace", action="store_true", help="overwrite the organization if it exists"
    )
    restore.add_argument("--verify-only", action="store_true")
    restore.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="accept a backup this deployment did not sign (only if you made it elsewhere and trust it)",
    )
    restore.set_defaults(handler=cmd_restore_org)
    commands.add_parser(
        "init-storage",
        help="create the documents bucket on an S3-compatible store if it is missing",
    ).set_defaults(handler=cmd_init_storage)
    rotate = commands.add_parser(
        "rotate-keys", help="rotate one organization's data key"
    )
    rotate.add_argument("--org", required=True)
    rotate.set_defaults(handler=cmd_rotate_keys)
    rewrap = commands.add_parser(
        "rewrap-keys",
        help="re-wrap every organization's data key from the old master key (AIRA_CLOUD_*_OLD) to the current one",
    )
    rewrap.add_argument(
        "--verify-only",
        action="store_true",
        help="prove the old key opens every key and the new key round-trips, without changing anything",
    )
    rewrap.set_defaults(handler=cmd_rewrap_keys)
    broker = commands.add_parser(
        "broker", help="run the LinkedIn sign-in broker (streams a private browser)"
    )
    broker.add_argument("--host")
    broker.add_argument("--port", type=int)
    broker.set_defaults(handler=cmd_broker)
    seed = commands.add_parser(
        "seed-demo", help="create demo organizations (development only)"
    )
    seed.set_defaults(handler=cmd_seed_demo)
    args = parser.parse_args(argv)
    configure_logging(args.log_level, json_output=args.json_logs)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
