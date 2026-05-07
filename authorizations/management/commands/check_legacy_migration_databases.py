from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Verify legacy migration source and target database aliases are distinct before import work."

    def add_arguments(self, parser):
        parser.add_argument("--source-db", default="legacy", help="Read-only legacy source database alias.")
        parser.add_argument("--target-db", default="trial", help="Writable target database alias.")

    def handle(self, *args, **options):
        source_alias = options["source_db"]
        target_alias = options["target_db"]

        if source_alias not in connections:
            raise CommandError(f'Source database alias "{source_alias}" is not configured.')
        if target_alias not in connections:
            raise CommandError(f'Target database alias "{target_alias}" is not configured.')

        source_config = connections.databases[source_alias]
        target_config = connections.databases[target_alias]
        source_signature = self._connection_signature(source_config)
        target_signature = self._connection_signature(target_config)

        self.stdout.write(f"Source alias: {source_alias}")
        self.stdout.write(f"Source database: {source_config.get('NAME')}")
        self.stdout.write(f"Source host: {source_config.get('HOST') or 'localhost'}")
        self.stdout.write(f"Target alias: {target_alias}")
        self.stdout.write(f"Target database: {target_config.get('NAME')}")
        self.stdout.write(f"Target host: {target_config.get('HOST') or 'localhost'}")

        if source_alias == target_alias:
            raise CommandError("Source and target aliases are identical. Refusing to continue.")
        if source_signature == target_signature:
            raise CommandError("Source and target point to the same database. Refusing to continue.")
        if target_config.get("NAME") == "antir_auth_legacy":
            raise CommandError("Target database is the legacy database. Refusing to continue.")
        if source_config.get("NAME") != "antir_auth_legacy":
            raise CommandError("Source database is not antir_auth_legacy. Refusing to continue.")
        if target_config.get("NAME") != "test_antir_auth_local":
            raise CommandError("Trial target database must be test_antir_auth_local. Refusing to continue.")

        self._assert_can_connect(source_alias, "source")
        self._assert_can_connect(target_alias, "target")

        self.stdout.write(self.style.SUCCESS("Legacy migration database separation check passed."))

    def _connection_signature(self, config):
        return (
            config.get("ENGINE"),
            config.get("HOST") or "localhost",
            str(config.get("PORT") or ""),
            config.get("NAME"),
        )

    def _assert_can_connect(self, alias, label):
        try:
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT DATABASE()")
                database_name = cursor.fetchone()[0]
        except Exception as exc:
            raise CommandError(f"Could not connect to {label} database alias {alias}: {exc}") from exc
        self.stdout.write(f"Connected {label} database: {database_name}")
