import os
import subprocess
import argparse
import pathlib
import dotenv
from datetime import datetime
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings

# Load environment variables from sql_details.env
env_path = Path(__file__).resolve().parent.parent.parent.parent / 'an_tir_authorization' / 'sql_details.env'
if not env_path.exists():
    raise FileNotFoundError(f"Environment file not found at {env_path}")
dotenv.load_dotenv(env_path)

class Command(BaseCommand):
    help = "Create a MySQL database backup"

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', '-o',
            type=str,
            help='Output file path (default: backups/db_backup_YYYYMMDD_HHMMSS.sql)'
        )
        parser.add_argument(
            '--no-timestamp',
            action='store_true',
            help='Do not add timestamp to the output filename'
        )
        parser.add_argument(
            '--no-data',
            action='store_true',
            help='Only backup schema, no data'
        )

    def _get_database_config(self):
        """Get and validate database configuration."""
        try:
            db = settings.DATABASES['default']
            if not db.get('NAME'):
                raise ValueError("Database NAME is not set in settings.DATABASES['default']")
            if not db.get('USER'):
                raise ValueError("Database USER is not set in settings.DATABASES['default']")
            return db
        except KeyError as e:
            raise KeyError(f"Database configuration error: {str(e)}. Check your Django settings.")

    def handle(self, *args, **options):
        try:
            # Get and validate database settings
            db = self._get_database_config()
            
            # Create backups directory if it doesn't exist
            if not options['output']:
                backup_dir = Path('backups')
                backup_dir.mkdir(exist_ok=True)
                timestamp = '' if options['no_timestamp'] else f'_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                output_file = backup_dir / f'db_backup{db["NAME"]}{timestamp}.sql'
            else:
                output_file = Path(options['output'])
            
            # Build the mysqldump command
            cmd = [
                'mysqldump',
                f'--user={db["USER"]}',
                f'--host={db["HOST"]}',
                f'--port={db["PORT"]}',
                '--add-drop-database',
                '--routines',
                '--triggers',
                '--events',
                '--single-transaction',
                '--quick',
                '--skip-comments',
                '--add-locks',
                '--create-options',
                '--set-charset',
                '--default-character-set=utf8mb4',
                '--skip-tz-utc',
            ]
            
            # Add no-data flag if specified
            if options['no_data']:
                cmd.append('--no-data')
            
            # Add database name
            cmd.append(db['NAME'])
            
            # Add password if provided (not recommended for security reasons)
            if db.get('PASSWORD'):
                cmd.insert(1, f'--password={db["PASSWORD"]}')
            
            self.stdout.write(f'Creating backup of {db["NAME"]} to {output_file}...')
            
            # Debug: Print the command being run
            self.stdout.write(f'Running command: {" ".join(cmd[:2] + ["[PASSWORD_REDACTED]" if "--password=" in arg else arg for arg in cmd[2:]])}')
            
            try:
                with open(output_file, 'wb') as f:
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        shell=True  # Try with shell=True to help with command execution
                    )
                    
                    # Stream the output to the file
                    while True:
                        output = process.stdout.read(1024)
                        if output == b'' and process.poll() is not None:
                            break
                        if output:
                            f.write(output)
                    
                    # Get any remaining output and errors
                    stdout, stderr = process.communicate()
                    
                    # Write any remaining output
                    if stdout:
                        f.write(stdout)
                    
                    # Check for errors
                    if stderr:
                        error_msg = stderr.decode(errors='replace').strip()
                        if 'Using a password on the command line' not in error_msg:  # Ignore password warning
                            self.stderr.write(self.style.ERROR(f'Error during backup: {error_msg}'))
                            if output_file.exists():
                                output_file.unlink()  # Remove the incomplete backup file
                            return
                    
                    # Check if the file was actually written to
                    if output_file.stat().st_size == 0:
                        self.stderr.write(self.style.ERROR('Backup file is empty. The mysqldump command may have failed.'))
                        if output_file.exists():
                            output_file.unlink()
                        return
                    
                    self.stdout.write(self.style.SUCCESS(f'[OK] Successfully created backup at {output_file}'))
                    self.stdout.write(f'Size: {output_file.stat().st_size / (1024 * 1024):.2f} MB')
                    
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Error during backup execution: {str(e)}'))
                if output_file.exists():
                    output_file.unlink()  # Remove the incomplete backup file
                return
                
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error: {str(e)}'))
            if 'output_file' in locals() and output_file.exists():
                output_file.unlink()  # Remove the incomplete backup file
        
        # The main backup logic is now in the try block above
