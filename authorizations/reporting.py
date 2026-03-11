from collections import defaultdict
from datetime import date

from authorizations.models import Authorization, Branch, Discipline, WeaponStyle


MARSHAL_STYLE_NAMES = {'Junior Marshal', 'Senior Marshal'}
REGION_ORDER = ['Central', 'Inlands', 'Summits', 'Tir Righ']
REGION_LIKE_TYPES = ['Region', 'Principality']

QUARTERLY_DISCIPLINE_MAP = [
    ('Armored Combat', 'Armored Combat'),
    ('Rapier Combat', 'Rapier'),
    ('Cut & Thrust', 'Cut & Thrust'),
    ('Youth Armored', 'YAC'),
    ('Youth Rapier', 'YRC'),
    ('Missile Combat', 'Missile'),
    ('Siege', 'Siege'),
    ('Equestrian', 'Equestrian'),
    ('Target Archery', 'Target Archery'),
    ('Thrown Weapons', 'Thrown Weapons'),
]

REGIONAL_DISCIPLINE_MAP = [
    ('Armored Combat', 'Armored Combat'),
    ('Rapier Combat', 'Rapier Combat'),
    ('Cut & Thrust', 'Cut & Thrust Combat'),
    ('Youth Armored', 'Youth Armored Combat'),
    ('Youth Rapier', 'Youth Rapier Combat'),
    ('Missile Combat', 'Missile'),
    ('Siege', 'Siege'),
    ('Equestrian', 'Equestrian'),
    ('Target Archery', 'Target Archery'),
    ('Thrown Weapons', 'Thrown Weapons'),
]

EQUESTRIAN_TYPE_ORDER = [
    'Junior Ground Crew',
    'Senior Ground Crew',
    'General Riding',
    'Mounted Gaming',
    'Mounted Archery',
    'Mounted Crest Combat',
    'Mounted Combat',
    'Driving',
    'Foam-Tipped Jousting',
]

EQUESTRIAN_TYPE_ALIASES = {
    'Junior Ground Crew': {'Junior Ground Crew', 'Ground Crew - Junior'},
    'Senior Ground Crew': {'Senior Ground Crew', 'Ground Crew - Senior'},
    'General Riding': {'General Riding'},
    'Mounted Gaming': {'Mounted Gaming'},
    'Mounted Archery': {'Mounted Archery'},
    'Mounted Crest Combat': {'Mounted Crest Combat', 'Crest Combat'},
    'Mounted Combat': {'Mounted Combat', 'Mounted Heavy Combat'},
    'Driving': {'Driving', 'Ground Driving'},
    'Foam-Tipped Jousting': {'Foam-Tipped Jousting', 'Jousting'},
}


class ReportingConfigurationError(Exception):
    """Raised when live reporting assumptions no longer match reference data."""

    def __init__(self, messages):
        self.messages = list(messages)
        super().__init__('; '.join(self.messages))


def _active_region_names():
    """
    Return An Tir region-like branch names in stable display order.

    Preferred order keeps legacy report alignment; additional active region-like
    branches are appended alphabetically so new regions are represented.
    """
    region_like_names = set(
        Branch.objects.filter(
            type__in=REGION_LIKE_TYPES,
            region__name='An Tir',
        ).values_list('name', flat=True)
    )
    preferred = [name for name in REGION_ORDER if name in region_like_names]
    extras = sorted(region_like_names - set(preferred))
    return preferred + extras


def validate_current_reporting_configuration():
    """
    Validate hard-coded report assumptions before building a live snapshot.

    Risks if these assumptions drift:
    - renamed/removed disciplines or styles will miscount or omit rows;
    - added regions will be excluded from regional/equestrian regional output;
    - removed regions can break region-based grouping semantics.
    """
    issues = []

    expected_disciplines = {name for name, _ in QUARTERLY_DISCIPLINE_MAP}
    existing_disciplines = set(Discipline.objects.values_list('name', flat=True))
    missing_disciplines = sorted(expected_disciplines - existing_disciplines)
    if missing_disciplines:
        issues.append(
            'Missing expected disciplines for current report generation: '
            + ', '.join(missing_disciplines)
        )

    existing_regions = set(
        Branch.objects.filter(
            type__in=REGION_LIKE_TYPES,
            region__name='An Tir',
        ).values_list('name', flat=True)
    )
    missing_regions = sorted(set(REGION_ORDER) - existing_regions)
    if missing_regions:
        issues.append(
            'Missing expected regions for current report generation: '
            + ', '.join(missing_regions)
        )

    existing_equestrian_styles = set(
        WeaponStyle.objects.filter(discipline__name='Equestrian').values_list('name', flat=True)
    )
    missing_equestrian_styles = sorted(
        label
        for label in EQUESTRIAN_TYPE_ORDER
        if not (EQUESTRIAN_TYPE_ALIASES[label] & existing_equestrian_styles)
    )
    if missing_equestrian_styles:
        issues.append(
            'Missing expected equestrian authorization types: '
            + ', '.join(missing_equestrian_styles)
        )

    return issues


def _equestrian_bucket_for_style(style_name):
    for report_label, aliases in EQUESTRIAN_TYPE_ALIASES.items():
        if style_name in aliases:
            return report_label
    return None


def _person_region_name(person):
    branch = getattr(person, 'branch', None)
    if not branch:
        return None
    if branch.type in ['Kingdom', 'Principality', 'Region']:
        return branch.name
    if branch.region:
        return branch.region.name
    return None


def build_current_report_snapshot(as_of=None):
    """
    Build all three current reports from live authorization data without persistence.

    This intentionally depends on the static mappings above; validate assumptions first
    so config drift fails safely with an actionable message instead of silent bad data.
    """
    if as_of is None:
        as_of = date.today()

    issues = validate_current_reporting_configuration()
    if issues:
        raise ReportingConfigurationError(issues)
    active_regions = _active_region_names()
    active_region_set = set(active_regions)

    active_auths = (
        Authorization.objects.with_effective_expiration()
        .select_related('person__user', 'person__branch__region', 'style__discipline')
        .filter(status__name='Active', effective_expiration_date__gte=as_of)
    )

    disc_people = defaultdict(set)
    disc_combatants = defaultdict(set)
    disc_minors = defaultdict(set)
    disc_juniors = defaultdict(set)
    disc_seniors = defaultdict(set)
    disc_person_styles = defaultdict(lambda: defaultdict(set))

    region_disc_people = defaultdict(set)
    region_disc_combatants = defaultdict(set)
    region_disc_minors = defaultdict(set)
    region_disc_juniors = defaultdict(set)
    region_disc_seniors = defaultdict(set)
    region_disc_person_styles = defaultdict(lambda: defaultdict(set))

    equestrian_people = defaultdict(set)
    equestrian_region_people = defaultdict(set)

    for auth in active_auths:
        person_id = auth.person_id
        style_name = auth.style.name
        discipline_name = auth.style.discipline.name
        is_minor = auth.person.is_minor
        region_name = _person_region_name(auth.person)

        disc_key = discipline_name
        disc_people[disc_key].add(person_id)
        disc_person_styles[disc_key][person_id].add(style_name)
        if style_name not in MARSHAL_STYLE_NAMES:
            disc_combatants[disc_key].add(person_id)
        if is_minor:
            disc_minors[disc_key].add(person_id)
        if style_name == 'Junior Marshal':
            disc_juniors[disc_key].add(person_id)
        if style_name == 'Senior Marshal':
            disc_seniors[disc_key].add(person_id)

        if region_name in active_region_set:
            region_disc_key = (region_name, discipline_name)
            region_disc_people[region_disc_key].add(person_id)
            region_disc_person_styles[region_disc_key][person_id].add(style_name)
            if style_name not in MARSHAL_STYLE_NAMES:
                region_disc_combatants[region_disc_key].add(person_id)
            if is_minor:
                region_disc_minors[region_disc_key].add(person_id)
            if style_name == 'Junior Marshal':
                region_disc_juniors[region_disc_key].add(person_id)
            if style_name == 'Senior Marshal':
                region_disc_seniors[region_disc_key].add(person_id)

        equestrian_bucket = _equestrian_bucket_for_style(style_name) if discipline_name == 'Equestrian' else None
        if equestrian_bucket:
            equestrian_people[equestrian_bucket].add(person_id)
            if region_name in active_region_set:
                equestrian_region_people[(region_name, equestrian_bucket)].add(person_id)

    disc_nf_juniors = defaultdict(set)
    disc_nf_seniors = defaultdict(set)
    for discipline_name, person_styles in disc_person_styles.items():
        for person_id, style_set in person_styles.items():
            if 'Junior Marshal' in style_set and len(style_set) == 1:
                disc_nf_juniors[discipline_name].add(person_id)
            if 'Senior Marshal' in style_set and len(style_set) == 1:
                disc_nf_seniors[discipline_name].add(person_id)

    region_disc_nf_juniors = defaultdict(set)
    region_disc_nf_seniors = defaultdict(set)
    for region_disc_key, person_styles in region_disc_person_styles.items():
        for person_id, style_set in person_styles.items():
            if 'Junior Marshal' in style_set and len(style_set) == 1:
                region_disc_nf_juniors[region_disc_key].add(person_id)
            if 'Senior Marshal' in style_set and len(style_set) == 1:
                region_disc_nf_seniors[region_disc_key].add(person_id)

    quarterly_rows = []
    display_order = 0
    for discipline_name, report_label in QUARTERLY_DISCIPLINE_MAP:
        metric_values = [
            ('Total Participants', len(disc_people[discipline_name])),
            ('Total Combatants', len(disc_combatants[discipline_name])),
            ('Minors Fighting', len(disc_minors[discipline_name])),
            ('Junior Marshals', len(disc_juniors[discipline_name])),
            ('Non-Fighting Junior Marshals', len(disc_nf_juniors[discipline_name])),
            ('Senior Marshals', len(disc_seniors[discipline_name])),
            ('Non-Fighting Senior Marshals', len(disc_nf_seniors[discipline_name])),
        ]
        for metric_name, value in metric_values:
            display_order += 1
            quarterly_rows.append(
                {
                    'region_name': '',
                    'subject_name': report_label,
                    'metric_name': metric_name,
                    'value': value,
                    'display_order': display_order,
                }
            )

    regional_rows = []
    display_order = 0
    for region_name in active_regions:
        total_authorizations = 0
        discipline_metric_rows = []
        for discipline_name, report_label in REGIONAL_DISCIPLINE_MAP:
            region_key = (region_name, discipline_name)
            combatants_count = len(region_disc_combatants[region_key])
            total_authorizations += combatants_count
            discipline_metric_rows.extend(
                [
                    ('Combatants', report_label, combatants_count),
                    ('Minors', report_label, len(region_disc_minors[region_key])),
                    ('Seniors', report_label, len(region_disc_seniors[region_key])),
                    ('Juniors', report_label, len(region_disc_juniors[region_key])),
                    ('NF Jr Marshals', report_label, len(region_disc_nf_juniors[region_key])),
                    ('NF Sr Marshals', report_label, len(region_disc_nf_seniors[region_key])),
                ]
            )

        display_order += 1
        regional_rows.append(
            {
                'region_name': region_name,
                'subject_name': 'Total Authorizations',
                'metric_name': 'Combatants',
                'value': total_authorizations,
                'display_order': display_order,
            }
        )
        for metric_name, subject_name, value in discipline_metric_rows:
            display_order += 1
            regional_rows.append(
                {
                    'region_name': region_name,
                    'subject_name': subject_name,
                    'metric_name': metric_name,
                    'value': value,
                    'display_order': display_order,
                }
            )

    equestrian_rows = []
    display_order = 0
    an_tir_total = 0
    for auth_type in EQUESTRIAN_TYPE_ORDER:
        value = len(equestrian_people[auth_type])
        an_tir_total += value
        display_order += 1
        equestrian_rows.append(
            {
                'region_name': 'An Tir',
                'subject_name': auth_type,
                'metric_name': 'Reporting Quarter',
                'value': value,
                'display_order': display_order,
            }
        )
    display_order += 1
    equestrian_rows.append(
        {
            'region_name': 'An Tir',
            'subject_name': 'Total',
            'metric_name': 'Reporting Quarter',
            'value': an_tir_total,
            'display_order': display_order,
        }
    )

    for region_name in active_regions:
        region_total = 0
        for auth_type in EQUESTRIAN_TYPE_ORDER:
            value = len(equestrian_region_people[(region_name, auth_type)])
            region_total += value
            display_order += 1
            equestrian_rows.append(
                {
                    'region_name': region_name,
                    'subject_name': auth_type,
                    'metric_name': 'Reporting Quarter',
                    'value': value,
                    'display_order': display_order,
                }
            )
        display_order += 1
        equestrian_rows.append(
            {
                'region_name': region_name,
                'subject_name': f'Total {region_name} Authorizations',
                'metric_name': 'Reporting Quarter',
                'value': region_total,
                'display_order': display_order,
            }
        )

    return {
        'quarterly_marshal': quarterly_rows,
        'regional_breakdown': regional_rows,
        'equestrian': equestrian_rows,
    }
