"""Check the official DOL disclosure page for supported workbook links."""
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
import re

from .h1b import DOL_URL, fiscal_year, validate_dol_download_url
from .public_sources import SourceUnavailable, public_get


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.extend(value for key, value in attrs if key == 'href' and isinstance(value, str))


def check_dol_releases():
    """Read one fixed official page; return links, never download or import a workbook."""
    status, raw, content_type = public_get(DOL_URL, {'www.dol.gov'}, maximum=2 * 1024 * 1024)
    if status != 200:
        raise SourceUnavailable(f'DOL release check failed (HTTP {status}); existing data remains unchanged. Use the official page or local import.')
    if content_type.split(';', 1)[0].strip().lower() != 'text/html':
        raise SourceUnavailable('DOL release page did not return HTML; existing data remains unchanged.')
    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise SourceUnavailable('DOL release page could not be read; existing data remains unchanged.') from None
    parser = _Links()
    parser.feed(content)
    releases = {}
    for href in parser.hrefs:
        try:
            url = validate_dol_download_url(urljoin(DOL_URL, href))
        except ValueError:
            continue
        match = re.search(r'/LCA_Disclosure_Data_FY(20\d{2})_Q([1-4])\.xlsx$', url)
        if match:
            year, quarter = int(match.group(1)), int(match.group(2))
            if year > fiscal_year():
                continue
            if (year, quarter) in releases and releases[(year, quarter)] != url:
                raise SourceUnavailable('DOL lists multiple links for the same release; inspect the official page before importing. Existing data remains unchanged.')
            releases[(year, quarter)] = url
    if not releases:
        raise SourceUnavailable('No supported DOL quarterly workbook link was found; use the official page or a local import. Existing data remains unchanged.')
    year, quarter = max(releases)
    return {'source_url': DOL_URL, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'fiscal_year': year, 'quarter': quarter, 'workbook_url': releases[(year, quarter)],
            'notice': 'Latest recognized quarterly link on the page; coverage, completeness and workbook availability are not independently verified.'}
