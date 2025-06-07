import os
import subprocess
import sys
import argparse
import pathlib
import dotenv
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection

# Load environment variables from sql_details.env
env_path = Path(__file__).resolve().parent.parent.parent.parent / 'an_tir_authorization' / 'sql_details.env'
if not env_path.exists():
    raise FileNotFoundError(f"Environment file not found at {env_path}")
dotenv.load_dotenv(env_path)

class Command(BaseCommand):
    help = "Restore a MySQL database from a backup file"

    def add_arguments(self, parser):
        parser.add_argument(
            'backup_file',
            type=str,
            help='Path to the backup file to restore from'
        )
        parser.add_argument(
            '--no-confirm',
            action='store_true',
            help='Skip confirmation prompt'
        )
        parser.add_argument(
            '--no-clear',
            action='store_true',
            help='Do not clear the database before restoring (not recommended)'
        )

    def handle(self, *args, **options):
        backup_file = Path(options['backup_file'])
        
        # Verify backup file exists
        if not backup_file.exists():
            self.stderr.write(self.style.ERROR(f'Backup file not found: {backup_file}'))
            return

        # Get database settings
        db = settings.DATABASES['default']
        
        # Confirm with user
        if not options['no_confirm']:
            confirm = input(
                f'This will restore database {db["NAME"]} from {backup_file}. ' \
                f'This operation cannot be undone. Continue? [y/N] '
            )
            if confirm.lower() != 'y':
                self.stdout.write('Restore cancelled.')
                return
        
        # Clear database if requested
        if not options['no_clear']:
            self.stdout.write('Clearing database...')
            self._clear_database(db)
        
        # Build the mysql command
        cmd = [
            'mysql',
            f'--user={db["USER"]}',
            f'--host={db["HOST"]}',
            f'--port={db["PORT"]}',
            '--default-character-set=utf8mb4',
            db['NAME']
        ]
        
        # Add password if provided
        if db['PASSWORD']:
            cmd.insert(1, f'--password={db["PASSWORD"]}')
        
        self.stdout.write(f'Restoring {backup_file} to {db["NAME"]}...')
        
        try:
            with open(backup_file, 'rb') as f:
                process = subprocess.Popen(
                    cmd,
                    stdin=f,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Wait for the process to complete and capture output
                stdout, stderr = process.communicate()
                
                if process.returncode != 0:
                    self.stderr.write(self.style.ERROR(f'Error during restore: {stderr.decode()}'))
                    return
                
                self.stdout.write(self.style.SUCCESS('✓ Database restored successfully'))
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error during restore: {str(e)}'))
    
    def _clear_database(self, db_settings):
        """Drop all tables in the database."""
        with connection.cursor() as cursor:
            # Temporarily disable foreign key checks
            cursor.execute('SET FOREIGN_KEY_CHECKS = 0;')
            
            # Get all tables
            cursor.execute('SHOW TABLES;')
            tables = [table[0] for table in cursor.fetchall()]
            
            # Drop all tables
            for table in tables:
                cursor.execute(f'DROP TABLE IF EXISTS `{table}`;')
            
            # Re-enable foreign key checks
            cursor.execute('SET FOREIGN_KEY_CHECKS = 1;')
            
            self.stdout.write(self.style.SUCCESS(f'[OK] Successfully dropped {len(tables)} tables'))
