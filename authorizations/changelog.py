import re
from types import SimpleNamespace

UNRELEASED_HEADING_RE = re.compile(r'^##\s+\[?Unreleased\]?.*$', re.IGNORECASE | re.MULTILINE)
SECTION_HEADING_RE = re.compile(r'^##\s+', re.MULTILINE)
DISPLAYABLE_HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)
VERSION_HEADING_RE = re.compile(r'^##\s+\[?(\d+)(?:\.\d+)*(?:\])?.*$', re.MULTILINE)


def get_unreleased_section(text):
    if not text:
        return ''

    match = UNRELEASED_HEADING_RE.search(text)
    if not match:
        return ''

    next_section = SECTION_HEADING_RE.search(text, match.end())
    section_end = next_section.start() if next_section else len(text)
    return text[match.start():section_end].strip()


def section_has_displayable_entries(section):
    section = DISPLAYABLE_HTML_COMMENT_RE.sub('', section or '')
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            continue
        return True
    return False


def unreleased_has_displayable_entries(text):
    return section_has_displayable_entries(get_unreleased_section(text))


def build_changelog_sections(text, *, include_unreleased=False, render_markdown):
    if not text:
        return []

    grouped_sections = []
    unreleased_section = get_unreleased_section(text)
    if include_unreleased and section_has_displayable_entries(unreleased_section):
        grouped_sections.append(
            SimpleNamespace(
                major='Unreleased',
                html=render_markdown(unreleased_section),
            )
        )

    matches = list(VERSION_HEADING_RE.finditer(text))
    if not matches:
        if grouped_sections:
            return grouped_sections
        return [SimpleNamespace(major='All', html=render_markdown(text))]

    current_major = None
    current_parts = []

    for index, match in enumerate(matches):
        major = match.group(1)
        section_start = match.start()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[section_start:section_end].strip()

        if current_major is None:
            current_major = major

        if major != current_major:
            grouped_sections.append(
                SimpleNamespace(
                    major=current_major,
                    html=render_markdown('\n\n'.join(current_parts)),
                )
            )
            current_major = major
            current_parts = []

        current_parts.append(section)

    if current_parts:
        grouped_sections.append(
            SimpleNamespace(
                major=current_major,
                html=render_markdown('\n\n'.join(current_parts)),
            )
        )

    return grouped_sections
