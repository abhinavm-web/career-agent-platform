"""Submit one reviewed PDF through a saved Lever posting's visible application form."""
import base64
import binascii
import hashlib
import re
from datetime import datetime, timezone
from pathlib import PurePath

from .delivery import (application_url, check_pending, record_dispatch,
                       record_preflight, resolve_dispatch)
from .storage import Store


class NeedsUserAction(ValueError):
    pass


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


def submit(db_path, job_id, data):
    fields, content = reviewed_materials(data)
    approved_at = datetime.now(timezone.utc).isoformat()
    with Store(db_path) as store:
        job = store.get_job(job_id)
        target = application_url(job.url)
        if target != fields['destination']:
            raise ValueError('The selected destination differs from the saved opportunity.')
        check_pending(store, job_id, target)
    try:
        from playwright.sync_api import sync_playwright, Error
    except ImportError as error:
        detail = 'Install the pdf extra and Playwright Chromium on the dashboard host.'
        with Store(db_path) as store:
            record_preflight(store, job_id, target, 'failed_before_sending', detail)
        raise ValueError(detail) from error
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
                raise NeedsUserAction('Employer form or role changed. Review the posting again.')
            form.locator('input[type=file][name=resume]').set_input_files(
                {'name': fields['filename'], 'mimeType': 'application/pdf', 'buffer': content})
            parsed = form.locator('.resume-upload-success')
            if parsed.count():
                parsed.wait_for(state='visible', timeout=45000)
                if form.locator('.visible-resume-upload .filename').text_content().strip() != fields['filename']:
                    raise NeedsUserAction('The employer did not acknowledge the selected PDF.')
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
                raise NeedsUserAction('The employer requires additional answers. Review the form on the employer site.')
            with Store(db_path) as store:
                store.connection.execute('BEGIN IMMEDIATE')
                if page.url != target or not all(form.locator(f'input[name="{key}"]').input_value() == fields[key]
                                                  for key in ('name', 'email', 'phone')):
                    raise NeedsUserAction('The application changed before submission.')
                selected_digest = form.evaluate("""async (form, filename) => {
                    const files = form.elements.namedItem('resume')?.files;
                    if (files?.length !== 1 || files[0].name !== filename || files[0].size > 2097152) return null;
                    const hash = await crypto.subtle.digest('SHA-256', await files[0].arrayBuffer());
                    return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2,'0')).join('');
                }""", fields['filename'])
                if selected_digest != hashlib.sha256(content).hexdigest():
                    raise NeedsUserAction('The selected PDF changed. Review the file before approving again.')
                record_dispatch(store, job_id, fields, content, approved_at)
                store.connection.commit()
            dispatched = True
            button.click(timeout=15000)
            # A click, an upload, or a successful HTTP response alone is insufficient.
            thanks = target.removesuffix('/apply') + '/thanks'
            page.wait_for_url(re.compile(re.escape(thanks) + r'/?(?:\?[^#]*)?$'), timeout=180000)
            page.get_by_text(re.compile(r'^Thank you for applying[!.]?$', re.I)).first.wait_for(timeout=10000)
            if page.url.split('?')[0].rstrip('/') != thanks:
                return {'status': 'unconfirmed', 'detail': 'Check the employer portal or email for confirmation.'}
            with Store(db_path) as store:
                resolve_dispatch(store, job_id, 'confirmed', thanks, None, 'employer_thanks_page')
            return {'status': 'confirmed', 'receipt': thanks}
    except (Error, NeedsUserAction) as error:
        if dispatched:
            return {'status': 'unconfirmed', 'detail': 'Check the employer portal or email for confirmation.'}
        needs_action = isinstance(error, NeedsUserAction)
        detail = str(error) if needs_action else 'Could not complete form preparation in desktop Chromium.'
        with Store(db_path) as store:
            record_preflight(store, job_id, target, 'needs_user_action' if needs_action else 'failed_before_sending', detail)
        raise ValueError(detail) from error
