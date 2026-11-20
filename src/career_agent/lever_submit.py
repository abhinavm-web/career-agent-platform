"""Submit one reviewed PDF through a saved Lever posting's visible application form."""
import base64
import binascii
import hashlib
import re
from pathlib import PurePath
from urllib.parse import urlsplit

from .storage import Store

POSTING = re.compile(r'/[a-z0-9_-]+/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)


def application_url(value):
    parts = urlsplit(value)
    path = parts.path.rstrip('/').removesuffix('/apply')
    if (parts.scheme != 'https' or parts.netloc not in {'jobs.lever.co', 'jobs.eu.lever.co'}
            or not POSTING.fullmatch(path)):
        raise ValueError('Choose a saved Lever-hosted posting.')
    return f'https://{parts.netloc}{path}/apply'


def reviewed_materials(data):
    if not isinstance(data, dict) or data.get('approved') is not True:
        raise ValueError('Approve this application and reviewed PDF first.')
    fields = {}
    for key in ('name', 'email', 'phone', 'filename', 'destination'):
        value = data.get(key)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 512 or not value.isprintable():
            raise ValueError(f'Invalid {key}.')
        fields[key] = value.strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', fields['email']):
        raise ValueError('Check the contact email.')
    name = fields['filename']
    if not name.lower().endswith('.pdf') or PurePath(name).name != name or re.search(r'[/\\:]', name):
        raise ValueError('Choose a reviewed PDF.')
    try:
        content = base64.b64decode(data['content'], validate=True)
    except (KeyError, ValueError, TypeError, binascii.Error) as error:
        raise ValueError('Invalid PDF attachment.') from error
    if not content.startswith(b'%PDF-') or len(content) > 2 * 1024 * 1024:
        raise ValueError('Choose a PDF of up to 2 MiB.')
    return fields, content


def check_pending(store, job_id, target):
    if store.archive_state(job_id) or store.stage_of(job_id) == 'submitted':
        raise ValueError('This opportunity is archived or already submitted.')
    if store.connection.execute('SELECT 1 FROM lever_dispatches WHERE destination=? OR job_id=?',
                                (target, job_id)).fetchone():
        raise ValueError('Submission was already requested. Check the employer confirmation before taking further action.')


def submit(db_path, job_id, data):
    fields, content = reviewed_materials(data)
    with Store(db_path) as store:
        job = store.get_job(job_id)
        target = application_url(job.url)
        if target != fields['destination']:
            raise ValueError('The selected destination differs from the saved opportunity.')
        check_pending(store, job_id, target)
    try:
        from playwright.sync_api import sync_playwright, Error
    except ImportError as error:
        raise ValueError('Install the pdf extra and Playwright Chromium on the dashboard host.') from error
    dispatched = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(target, wait_until='domcontentloaded')
            form = page.locator('form#application-form')
            button = form.get_by_role('button', name='Submit application', exact=True)
            if (page.url != target or page.locator('h2').first.inner_text().strip().casefold() != job.title.strip().casefold()
                    or not form.evaluate("(f)=>f.method==='post' && f.action===location.href && !f.querySelector('input[type=password]')")
                    or button.get_attribute('id') != 'btn-submit' or button.get_attribute('formaction') is not None):
                raise ValueError('Employer form or role changed. Review the posting again.')
            form.locator('input[type=file][name=resume]').set_input_files(
                {'name': fields['filename'], 'mimeType': 'application/pdf', 'buffer': content})
            parsed = form.locator('.resume-upload-success')
            if parsed.count():
                parsed.wait_for(state='visible', timeout=45000)
                if form.locator('.visible-resume-upload .filename').inner_text().strip() != fields['filename']:
                    raise ValueError('The employer did not acknowledge the selected PDF.')
            # Lever may derive a current employer, location and links from past roles.
            # These optional answers are outside the approved contact information.
            for optional in form.locator('input[name=org], input[name=location], input[name^="urls["]').all():
                if optional.is_visible() and optional.input_value():
                    optional.fill('')
            for key in ('name', 'email', 'phone'):
                form.locator(f'input[name="{key}"]').fill(fields[key])
            if not form.evaluate("""f => f.checkValidity() && Array.from(f.elements).every(e => {
                if (!e.name || e.disabled || !e.getClientRects().length || e.type === 'hidden' ||
                    ['name','phone','email','resume'].includes(e.name) || e.type === 'submit') return true;
                return ['radio','checkbox'].includes(e.type) ? !e.checked : !e.value;
            })"""):
                raise ValueError('The employer requires additional answers. Complete the form using the browser companion.')
            with Store(db_path) as store:
                store.connection.execute('BEGIN IMMEDIATE')
                check_pending(store, job_id, target)
                if page.url != target or not all(form.locator(f'input[name="{key}"]').input_value() == fields[key]
                                                  for key in ('name', 'email', 'phone')):
                    raise ValueError('The application changed before submission.')
                store.connection.execute('INSERT INTO lever_dispatches(job_id,destination,resume_sha256) VALUES(?,?,?)',
                                         (job_id, target, hashlib.sha256(content).hexdigest()))
                store.connection.commit()
            dispatched = True
            button.click(timeout=15000)
            # A click, an upload, or a successful HTTP response alone is insufficient.
            thanks = target.removesuffix('/apply') + '/thanks'
            page.wait_for_url(re.compile(re.escape(thanks) + r'/?(?:\?[^#]*)?$'), timeout=180000)
            page.get_by_text(re.compile(r'^Thank you for applying[!.]?$', re.I)).first.wait_for(timeout=10000)
            if page.url.split('?')[0].rstrip('/') != thanks:
                raise ValueError('Employer confirmation could not be verified.')
            with Store(db_path) as store:
                store.connection.execute("""INSERT INTO applications(job_id,stage,receipt) VALUES(?,'submitted',?)
                    ON CONFLICT(job_id) DO UPDATE SET stage='submitted',receipt=excluded.receipt,
                    updated_at=CURRENT_TIMESTAMP""", (job_id, thanks))
                store.connection.commit()
            return {'status': 'confirmed', 'receipt': thanks}
    except Error as error:
        if dispatched:
            return {'status': 'unconfirmed', 'detail': 'Career Agent requested submission. Check your employer email or portal before retrying.'}
        raise ValueError('Could not open or complete the recognized employer form in desktop Chromium. No submission was requested.') from error
