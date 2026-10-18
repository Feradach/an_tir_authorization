"""Eligibility from recorded validity periods, including prerequisite gaps.

Never infer past eligibility from today's expiration or status. A renewal can
replace those fields, while an inactive or merged authorization retains history.
"""

from datetime import timedelta

from .models import AuthorizationValidityInterval, Sanction


def prerequisite_style_groups(discipline, style_name, available_names):
    """Each group is an alternative set; every group must be satisfied."""
    if style_name in ['Junior Marshal', 'Senior Marshal']:
        return []
    if discipline == 'Rapier Combat' and style_name != 'Single Sword':
        return [{'Single Sword'}]
    if discipline == 'Youth Rapier' and not style_name.endswith('Single Sword'):
        names = {'Single Sword'}
        for category in ['Lion', 'Gryphon', 'Dragon']:
            if style_name.startswith(f'{category} - '):
                names.add(f'{category} - Single Sword')
        return [names]
    if discipline == 'Cut & Thrust' and style_name == 'Spear':
        return [set(available_names) - {'Spear', 'Junior Marshal', 'Senior Marshal'}]
    if discipline == 'Equestrian':
        if style_name == 'Mounted Gaming':
            return [{'General Riding'}]
        if style_name in ['Mounted Heavy Combat', 'Mounted Combat', 'Mounted Archery',
                          'Crest Combat', 'Mounted Crest Combat', 'Jousting', 'Foam-Tipped Jousting']:
            return [{'Mounted Gaming'}]
    return []


def _intersect(left, right):
    return [(max(a, c), min(b, d)) for a, b in left for c, d in right
            if max(a, c) <= min(b, d)]


def _subtract(windows, start, end):
    result = []
    for a, b in windows:
        if end < a or start > b:
            result.append((a, b))
        else:
            if a < start:
                result.append((a, start - timedelta(days=1)))
            if b > end:
                result.append((end + timedelta(days=1), b))
    return result


def was_authorized_during(person, start, end, *, discipline=None,
                          style_names=None, exclude_style_names=None):
    """Whether any fully valid authorization overlaps this inclusive window.

    Stored membership/background-check gaps remain gaps. Dependent styles must
    overlap their prerequisite history, even if an old imported interval was too
    broad. Sanctions remove eligibility for their recorded dates. Missing history
    fails closed instead of inventing a date from an 'expired' display sentinel.
    """
    if person.user.merged_into_id or start > end:
        return False
    query = AuthorizationValidityInterval.objects.filter(
        authorization__person=person, start_date__lte=end, end_date__gte=start,
    ).select_related('authorization__style__discipline')
    sanctions = Sanction.objects.filter(person=person, start_date__lte=end, end_date__gte=start)
    if discipline is not None:
        if isinstance(discipline, str):
            query = query.filter(authorization__style__discipline__name=discipline)
            sanctions = sanctions.filter(discipline__name=discipline)
        else:
            query = query.filter(authorization__style__discipline=discipline)
            sanctions = sanctions.filter(discipline=discipline)
    by_discipline = {}
    for interval in query:
        style = interval.authorization.style
        entry = by_discipline.setdefault(style.discipline_id, (style.discipline.name, {}))[1]
        entry.setdefault(style.name, []).append(
            (max(start, interval.start_date), min(end, interval.end_date))
        )
    sanctions = list(sanctions.select_related('style'))
    for discipline_id, (discipline_name, styles) in by_discipline.items():
        cache = {}

        def valid_windows(name):
            if name in cache:
                return cache[name]
            windows = styles.get(name, [])
            for group in prerequisite_style_groups(discipline_name, name, styles):
                prerequisite_windows = [window for other in group for window in valid_windows(other)]
                windows = _intersect(windows, prerequisite_windows)
            for sanction in sanctions:
                if sanction.discipline_id != discipline_id:
                    continue
                if sanction.style_id and sanction.style.name != name:
                    continue
                sanction_end = sanction.end_date
                if sanction.lifted_at:
                    sanction_end = min(sanction_end, sanction.lifted_at.date() - timedelta(days=1))
                if sanction.start_date <= sanction_end:
                    windows = _subtract(windows, sanction.start_date, sanction_end)
            cache[name] = windows
            return windows

        for name in styles:
            if style_names is not None and name not in style_names:
                continue
            if exclude_style_names and name in exclude_style_names:
                continue
            if valid_windows(name):
                return True
    return False
