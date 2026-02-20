import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from authorizations.models import ReportValue, ReportingPeriod


NS = {
    'm': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'pkg': 'http://schemas.openxmlformats.org/package/2006/relationships',
}

QUARTER_RE = re.compile(r'Q([1-4])\s+(\d{4})', re.IGNORECASE)
OFFICER_SUFFIX = 'Marshal Authorization Officer - An Tir'
REGIONS = {'Central', 'Inlands', 'Summits', 'Tir Righ', 'An Tir'}


@dataclass(frozen=True)
class ParsedValue:
    year: int
    quarter: int
    officer_name: str
    report_family: str
    region_name: str
    subject_name: str
    metric_name: str
    value: int
    display_order: int


def normalize_text(value: Optional[str]) -> str:
    if value is None:
        return ''
    normalized = str(value).replace('\xa0', ' ').replace('\u200b', '')
    return re.sub(r'\s+', ' ', normalized).strip()


def parse_int(value: Optional[str]) -> Optional[int]:
    text = normalize_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def column_to_index(column: str) -> int:
    total = 0
    for char in column:
        total = (total * 26) + (ord(char) - 64)
    return total


def parse_quarter(text: str) -> Optional[Tuple[int, int]]:
    match = QUARTER_RE.search(text or '')
    if not match:
        return None
    quarter = int(match.group(1))
    year = int(match.group(2))
    return year, quarter


def completed_quarter(year: int, quarter: int, today: date) -> bool:
    current_quarter = ((today.month - 1) // 3) + 1
    if year < today.year:
        return True
    if year > today.year:
        return False
    return quarter < current_quarter


def read_workbook(path: Path) -> List[Tuple[str, Dict[int, Dict[int, Optional[str]]], int]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings: List[str] = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared_root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            for si in shared_root.findall('m:si', NS):
                shared_strings.append(''.join((t.text or '') for t in si.findall('.//m:t', NS)))

        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        rid_to_target = {
            rel.attrib['Id']: rel.attrib['Target']
            for rel in rels.findall('pkg:Relationship', NS)
        }

        sheets: List[Tuple[str, Dict[int, Dict[int, Optional[str]]], int]] = []
        for sheet in workbook.findall('m:sheets/m:sheet', NS):
            sheet_name = sheet.attrib['name']
            rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = rid_to_target[rid]
            if not target.startswith('xl/'):
                target = f'xl/{target}'
            xml = ET.fromstring(archive.read(target))

            rows: Dict[int, Dict[int, Optional[str]]] = defaultdict(dict)
            max_row = 0
            for cell in xml.findall('.//m:sheetData/m:row/m:c', NS):
                reference = cell.attrib.get('r', '')
                match = re.match(r'([A-Z]+)(\d+)', reference)
                if not match:
                    continue
                col = column_to_index(match.group(1))
                row = int(match.group(2))
                max_row = max(max_row, row)

                value = None
                value_node = cell.find('m:v', NS)
                cell_type = cell.attrib.get('t')
                if cell_type == 's' and value_node is not None and value_node.text is not None:
                    idx = int(value_node.text)
                    value = shared_strings[idx] if idx < len(shared_strings) else None
                elif cell_type == 'inlineStr':
                    inline_node = cell.find('m:is/m:t', NS)
                    value = inline_node.text if inline_node is not None else None
                elif value_node is not None:
                    value = value_node.text

                rows[row][col] = value

            sheets.append((sheet_name, rows, max_row))
        return sheets


def sheet_officer_name(rows: Dict[int, Dict[int, Optional[str]]]) -> str:
    for row_num in sorted(rows.keys())[:10]:
        text = normalize_text(rows[row_num].get(1))
        if OFFICER_SUFFIX in text:
            return normalize_text(text.replace(OFFICER_SUFFIX, ''))
    return ''


def sheet_period(sheet_name: str, rows: Dict[int, Dict[int, Optional[str]]]) -> Optional[Tuple[int, int]]:
    from_sheet = parse_quarter(sheet_name)
    if from_sheet:
        return from_sheet
    for row_num in sorted(rows.keys())[:8]:
        from_title = parse_quarter(normalize_text(rows[row_num].get(1)))
        if from_title:
            return from_title
    return None


def parse_quarterly_marshal_sheet(
    rows: Dict[int, Dict[int, Optional[str]]],
    max_row: int,
    year: int,
    quarter: int,
    officer_name: str,
) -> List[ParsedValue]:
    header_row = None
    for row_num in range(1, max_row + 1):
        if (
            normalize_text(rows.get(row_num, {}).get(1)) == 'Discipline'
            and normalize_text(rows.get(row_num, {}).get(2)) == 'Authorization Detail'
        ):
            header_row = row_num
            break
    if header_row is None:
        raise CommandError(f'Unable to find data header in quarterly marshal sheet for Q{quarter} {year}.')

    values: List[ParsedValue] = []
    current_subject = ''
    for row_num in range(header_row + 1, max_row + 1):
        row = rows.get(row_num, {})
        discipline = normalize_text(row.get(1))
        detail = normalize_text(row.get(2))
        current_value = parse_int(row.get(3))

        if discipline:
            current_subject = discipline
        if not current_subject or not detail:
            continue

        value = 0 if current_value is None else current_value
        values.append(
            ParsedValue(
                year=year,
                quarter=quarter,
                officer_name=officer_name,
                report_family=ReportValue.ReportFamily.QUARTERLY_MARSHAL,
                region_name='',
                subject_name=current_subject,
                metric_name=detail,
                value=value,
                display_order=row_num,
            )
        )
    return values


def parse_regional_breakdown_sheet(
    rows: Dict[int, Dict[int, Optional[str]]],
    max_row: int,
    year: int,
    quarter: int,
    officer_name: str,
) -> List[ParsedValue]:
    header_row = None
    for row_num in range(1, max_row + 1):
        if normalize_text(rows.get(row_num, {}).get(1)) == 'Description':
            header_row = row_num
            break
    if header_row is None:
        raise CommandError(f'Unable to find data header in regional breakdown sheet for Q{quarter} {year}.')

    metric_headers = {
        col: normalize_text(rows[header_row].get(col))
        for col in range(2, 8)
    }
    values: List[ParsedValue] = []
    current_region = ''
    for row_num in range(header_row + 1, max_row + 1):
        row = rows.get(row_num, {})
        description = normalize_text(row.get(1))
        if not description:
            continue

        row_values = {col: parse_int(row.get(col)) for col in range(2, 8)}
        has_any_numeric = any(v is not None for v in row_values.values())

        if not has_any_numeric:
            current_region = description
            continue

        if not current_region:
            raise CommandError(
                f'Regional breakdown row "{description}" in Q{quarter} {year} appeared before a region heading.'
            )

        for col in range(2, 8):
            metric_name = metric_headers.get(col, '')
            metric_value = row_values[col]
            if not metric_name or metric_value is None:
                continue
            values.append(
                ParsedValue(
                    year=year,
                    quarter=quarter,
                    officer_name=officer_name,
                    report_family=ReportValue.ReportFamily.REGIONAL_BREAKDOWN,
                    region_name=current_region,
                    subject_name=description,
                    metric_name=metric_name,
                    value=metric_value,
                    display_order=(row_num * 10) + col,
                )
            )
    return values


def parse_equestrian_sheet(
    rows: Dict[int, Dict[int, Optional[str]]],
    max_row: int,
    year: int,
    quarter: int,
    officer_name: str,
) -> List[ParsedValue]:
    values: List[ParsedValue] = []
    current_region = 'An Tir'
    in_data_section = False
    skip_current_section = False

    for row_num in range(1, max_row + 1):
        row = rows.get(row_num, {})
        label = normalize_text(row.get(1))
        if not label:
            continue

        # Region anchors are usually "Centralback to top" in exported sheets.
        if 'back to top' in label:
            region_candidate = normalize_text(label.replace('back to top', ''))
            if region_candidate in REGIONS:
                current_region = region_candidate
                skip_current_section = False
            else:
                # Legacy sheets include an "Nback to top" cumulative section.
                # It duplicates kingdom totals and should not be imported.
                skip_current_section = True
            in_data_section = False
            continue

        if label == 'An Tir Equestrian Authorizations':
            current_region = 'An Tir'
            skip_current_section = False
            in_data_section = False
            continue

        if label in REGIONS and all(parse_int(row.get(col)) is None for col in (2, 3, 4)):
            current_region = label
            skip_current_section = False
            in_data_section = False
            continue

        if label == 'Authorization Type' and normalize_text(row.get(2)) == 'Reporting Quarter':
            in_data_section = True
            continue

        if not in_data_section or skip_current_section:
            continue

        value = parse_int(row.get(2))
        if value is None:
            continue

        values.append(
            ParsedValue(
                year=year,
                quarter=quarter,
                officer_name=officer_name,
                report_family=ReportValue.ReportFamily.EQUESTRIAN,
                region_name=current_region,
                subject_name=label,
                metric_name='Reporting Quarter',
                value=value,
                display_order=row_num,
            )
        )
    return values


class Command(BaseCommand):
    help = (
        'Import legacy quarterly reporting data from XLSX files into '
        'ReportingPeriod and ReportValue.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--base-dir',
            default=str(Path(settings.BASE_DIR) / 'tmp'),
            help='Directory containing the legacy report XLSX files.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and validate files, but do not write to the database.',
        )
        parser.add_argument(
            '--include-current-quarter',
            action='store_true',
            help='Import current in-progress quarter sheets if present.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        base_dir = Path(options['base_dir']).resolve()
        dry_run = options['dry_run']
        include_current = options['include_current_quarter']
        today = date.today()

        file_map = {
            ReportValue.ReportFamily.QUARTERLY_MARSHAL: base_dir / 'Quarterly_Marshal_Statistics.xlsx',
            ReportValue.ReportFamily.REGIONAL_BREAKDOWN: base_dir / 'Regional_Breakdown.xlsx',
            ReportValue.ReportFamily.EQUESTRIAN: base_dir / 'Equestrian_Breakdown.xlsx',
        }

        for report_family, path in file_map.items():
            if not path.exists():
                raise CommandError(f'File not found for {report_family}: {path}')

        parsed_values: List[ParsedValue] = []
        periods_seen: set[Tuple[int, int]] = set()
        skipped_current_or_future = 0

        for report_family, path in file_map.items():
            sheets = read_workbook(path)
            for sheet_name, rows, max_row in sheets:
                period = sheet_period(sheet_name, rows)
                if period is None:
                    raise CommandError(f'Unable to parse quarter from sheet "{sheet_name}" in {path.name}.')
                year, quarter = period

                if not include_current and not completed_quarter(year, quarter, today):
                    skipped_current_or_future += 1
                    continue

                officer_name = sheet_officer_name(rows)
                if not officer_name:
                    raise CommandError(
                        f'Unable to parse authorization officer name in {path.name} / sheet "{sheet_name}".'
                    )

                periods_seen.add((year, quarter))

                if report_family == ReportValue.ReportFamily.QUARTERLY_MARSHAL:
                    parsed_values.extend(
                        parse_quarterly_marshal_sheet(rows, max_row, year, quarter, officer_name)
                    )
                elif report_family == ReportValue.ReportFamily.REGIONAL_BREAKDOWN:
                    parsed_values.extend(
                        parse_regional_breakdown_sheet(rows, max_row, year, quarter, officer_name)
                    )
                elif report_family == ReportValue.ReportFamily.EQUESTRIAN:
                    parsed_values.extend(
                        parse_equestrian_sheet(rows, max_row, year, quarter, officer_name)
                    )

        if not periods_seen:
            raise CommandError('No reporting periods were parsed from the provided files.')

        deduped_values: Dict[Tuple[int, int, str, str, str, str], ParsedValue] = {}
        duplicate_rows = 0
        for value in parsed_values:
            key = (
                value.year,
                value.quarter,
                value.report_family,
                value.region_name,
                value.subject_name,
                value.metric_name,
            )
            if key in deduped_values:
                duplicate_rows += 1
                continue
            deduped_values[key] = value
        parsed_values = list(deduped_values.values())

        period_officer_names: Dict[Tuple[int, int], set[str]] = defaultdict(set)
        for value in parsed_values:
            period_officer_names[(value.year, value.quarter)].add(value.officer_name)

        for (year, quarter), names in period_officer_names.items():
            if len(names) > 1:
                raise CommandError(
                    f'Conflicting officer names for Q{quarter} {year}: {sorted(names)}'
                )

        if dry_run:
            self.stdout.write(self.style.SUCCESS('Dry-run successful.'))
            self.stdout.write(f'Periods parsed: {len(periods_seen)}')
            self.stdout.write(f'Rows parsed: {len(parsed_values)}')
            self.stdout.write(f'Duplicate rows collapsed: {duplicate_rows}')
            self.stdout.write(f'Sheets skipped (current/future): {skipped_current_or_future}')
            return

        period_models: Dict[Tuple[int, int], ReportingPeriod] = {}
        for year, quarter in sorted(periods_seen):
            officer_name = next(iter(period_officer_names[(year, quarter)]))
            period, _ = ReportingPeriod.objects.get_or_create(
                year=year,
                quarter=quarter,
                defaults={'authorization_officer_name': officer_name},
            )
            if period.authorization_officer_name != officer_name:
                period.authorization_officer_name = officer_name
                period.save(update_fields=['authorization_officer_name', 'updated_at'])
            period_models[(year, quarter)] = period

        # Full replace for these families keeps reruns deterministic and idempotent.
        ReportValue.objects.filter(report_family__in=file_map.keys()).delete()

        create_rows = [
            ReportValue(
                reporting_period=period_models[(value.year, value.quarter)],
                report_family=value.report_family,
                region_name=value.region_name,
                subject_name=value.subject_name,
                metric_name=value.metric_name,
                value=value.value,
                display_order=value.display_order,
            )
            for value in parsed_values
        ]
        ReportValue.objects.bulk_create(create_rows, batch_size=2000)

        by_family = defaultdict(int)
        for value in parsed_values:
            by_family[value.report_family] += 1

        self.stdout.write(self.style.SUCCESS('Legacy reports imported successfully.'))
        self.stdout.write(f'Reporting periods processed: {len(periods_seen)}')
        self.stdout.write(f'Total report values inserted: {len(parsed_values)}')
        self.stdout.write(f'Duplicate rows collapsed: {duplicate_rows}')
        self.stdout.write(f'Sheets skipped (current/future): {skipped_current_or_future}')
        for report_family in sorted(by_family.keys()):
            self.stdout.write(f'  {report_family}: {by_family[report_family]} rows')
