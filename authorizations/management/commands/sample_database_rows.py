import csv
import json
import random
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections


SENSITIVE_COLUMNS = {
    "password",
    "session_data",
}


class Command(BaseCommand):
    help = "Export a small random sample of rows from every database table for manual comparison."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default", help="Database alias to sample.")
        parser.add_argument("--rows", type=int, default=3, help="Rows to sample per table.")
        parser.add_argument("--seed", type=int, default=20260507, help="Random seed for repeatable samples.")
        parser.add_argument("--output-csv", help="Write samples to this CSV file.")
        parser.add_argument("--include-sensitive", action="store_true", help="Include sensitive columns such as passwords and session data.")

    def handle(self, *args, **options):
        database = options["database"]
        rows_per_table = options["rows"]
        if rows_per_table < 1:
            raise CommandError("--rows must be at least 1.")
        if database not in connections:
            raise CommandError(f'Database alias "{database}" is not configured.')

        connection = connections[database]
        randomizer = random.Random(options["seed"])
        samples = self._sample_database(connection, rows_per_table, randomizer, options["include_sensitive"])

        if options.get("output_csv"):
            self._write_csv(Path(options["output_csv"]), samples)
            self.stdout.write(self.style.SUCCESS(f"Wrote {len(samples)} sampled rows to {options['output_csv']}.")) 
        else:
            self._write_stdout(samples)

    def _sample_database(self, connection, rows_per_table, randomizer, include_sensitive):
        samples = []
        table_names = connection.introspection.table_names()
        with connection.cursor() as cursor:
            for table_name in table_names:
                columns = self._columns(cursor, connection, table_name, include_sensitive)
                if not columns:
                    continue
                primary_key = self._primary_key(cursor, connection, table_name)
                row_count = self._row_count(cursor, connection, table_name)
                if row_count == 0:
                    samples.append(
                        {
                            "table": table_name,
                            "row_count": row_count,
                            "primary_key": primary_key or "",
                            "primary_key_value": "",
                            "row_json": "{}",
                        }
                    )
                    continue

                offsets = sorted(randomizer.sample(range(row_count), min(rows_per_table, row_count)))
                order_by = primary_key or columns[0]
                for offset in offsets:
                    row = self._row_at_offset(cursor, connection, table_name, columns, order_by, offset)
                    primary_key_value = row.get(primary_key, "") if primary_key else ""
                    samples.append(
                        {
                            "table": table_name,
                            "row_count": row_count,
                            "primary_key": primary_key or "",
                            "primary_key_value": primary_key_value,
                            "row_json": json.dumps(row, default=str, sort_keys=True),
                        }
                    )
        return samples

    def _columns(self, cursor, connection, table_name, include_sensitive):
        description = connection.introspection.get_table_description(cursor, table_name)
        columns = [column.name for column in description]
        if include_sensitive:
            return columns
        return [column for column in columns if column not in SENSITIVE_COLUMNS]

    def _primary_key(self, cursor, connection, table_name):
        constraints = connection.introspection.get_constraints(cursor, table_name)
        for name, constraint in constraints.items():
            if constraint.get("primary_key") and constraint.get("columns"):
                return constraint["columns"][0]
        return None

    def _row_count(self, cursor, connection, table_name):
        quoted_table = connection.ops.quote_name(table_name)
        cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
        return cursor.fetchone()[0]

    def _row_at_offset(self, cursor, connection, table_name, columns, order_by, offset):
        quoted_table = connection.ops.quote_name(table_name)
        quoted_columns = ", ".join(connection.ops.quote_name(column) for column in columns)
        quoted_order_by = connection.ops.quote_name(order_by)
        cursor.execute(
            f"SELECT {quoted_columns} FROM {quoted_table} ORDER BY {quoted_order_by} LIMIT 1 OFFSET %s",
            [offset],
        )
        values = cursor.fetchone()
        return dict(zip(columns, values))

    def _write_csv(self, path, samples):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=["table", "row_count", "primary_key", "primary_key_value", "row_json"],
            )
            writer.writeheader()
            writer.writerows(samples)

    def _write_stdout(self, samples):
        current_table = None
        for sample in samples:
            if sample["table"] != current_table:
                current_table = sample["table"]
                self.stdout.write("")
                self.stdout.write(f"{current_table} ({sample['row_count']} rows)")
            if sample["primary_key"]:
                self.stdout.write(f"- {sample['primary_key']}={sample['primary_key_value']}: {sample['row_json']}")
            else:
                self.stdout.write(f"- {sample['row_json']}")
