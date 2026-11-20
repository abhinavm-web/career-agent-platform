"""Local dispatch evidence and receipt reconciliation. All times are UTC."""
import hashlib
import json
import re
from urllib.parse import urlsplit


POSTING = re.compile(r'/[a-z0-9_-]+/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)


def application_url(value):
    parts = urlsplit(value)
    path = parts.path.rstrip('/').removesuffix('/apply')
    if (parts.scheme != 'https' or parts.netloc not in {'jobs.lever.co', 'jobs.eu.lever.co'}
            or not POSTING.fullmatch(path)):
        raise ValueError('Choose a saved Lever-hosted posting.')
    return f'https://{parts.netloc}{path}/apply'


def delivery_index(rows):
    """Read status metadata once for a board or tracker, without loading private materials."""
    by_job, by_destination = {}, {}
    for row in rows:
        if not row['delivery_state']:
            continue
        dispatched = row['destination'] is not None
        by_job[row['job_id']] = dict(state=row['delivery_state'], dispatched=dispatched,
                                     has_pdf=bool(row['has_pdf']), updated_at=row['delivery_updated_at'])
        if dispatched:
            by_destination[row['destination']] = row['job_id']
    return by_job, by_destination


def job_delivery(index, job_id, url):
    by_job, by_destination = index
    if by_destination:
        try:
            owner = by_destination.get(application_url(url))
        except ValueError:
            owner = None
        if owner and owner != job_id and by_job.get(job_id, {}).get('state') != 'confirmed':
            return dict(state='duplicate', owner_id=owner, dispatched=False, has_pdf=False)
    return by_job.get(job_id)


def delivery_for_job(store, job):
    try:
        target = application_url(job.url)
    except ValueError:
        target = None
    rows = store.connection.execute('SELECT * FROM delivery_status WHERE job_id=? OR destination=?',
                                    (job.id, target))
    return job_delivery(delivery_index(rows), job.id, job.url)


def check_pending(store, job_id, target):
    if store.archive_state(job_id) or store.stage_of(job_id) == 'submitted':
        raise ValueError('This opportunity is archived or already submitted.')
    if store.connection.execute('SELECT 1 FROM lever_dispatches WHERE destination=? OR job_id=?',
                                (target, job_id)).fetchone():
        raise ValueError('Submission was already requested. Review the existing delivery record.')


def record_preflight(store, job_id, target, state, detail):
    with store.connection:
        store.connection.execute('BEGIN IMMEDIATE')
        check_pending(store, job_id, target)
        store.connection.execute("""INSERT INTO delivery_records(job_id,destination,state,source,note)
            VALUES(?,?,?,'form_preparation',?) ON CONFLICT(job_id) DO UPDATE SET
            destination=excluded.destination,state=excluded.state,note=excluded.note,
            updated_at=CURRENT_TIMESTAMP""", (job_id, target, state, detail))


def record_dispatch(store, job_id, fields, content, approved_at):
    """Caller holds the write transaction through the guard and snapshot inserts."""
    target = fields['destination']
    check_pending(store, job_id, target)
    store.connection.execute('INSERT INTO lever_dispatches(job_id,destination,resume_sha256) VALUES(?,?,?)',
                             (job_id, target, hashlib.sha256(content).hexdigest()))
    answers = {key: fields[key] for key in ('name', 'email', 'phone')}
    store.connection.execute("""INSERT INTO delivery_records
        (job_id,destination,state,source,approved_at,filename,pdf,answers)
        VALUES(?,?,'awaiting_confirmation','dispatch',?,?,?,?)
        ON CONFLICT(job_id) DO UPDATE SET destination=excluded.destination,state=excluded.state,
        source=excluded.source,approved_at=excluded.approved_at,filename=excluded.filename,
        pdf=excluded.pdf,answers=excluded.answers,note=NULL,updated_at=CURRENT_TIMESTAMP""",
        (job_id, target, approved_at, fields['filename'], content, json.dumps(answers)))


def evidence(store, job_id):
    row = store.connection.execute("""SELECT COALESCE(d.destination,r.destination) AS destination,
        d.requested_at,d.resume_sha256,r.approved_at,r.filename,r.answers,r.note,r.source,r.state,
        r.updated_at,a.stage,a.receipt,a.updated_at AS application_updated_at
        FROM jobs j LEFT JOIN lever_dispatches d USING(job_id) LEFT JOIN delivery_records r USING(job_id)
        LEFT JOIN applications a USING(job_id) WHERE j.job_id=?""", (job_id,)).fetchone()
    if not row or not (row['requested_at'] or row['state'] or row['stage'] == 'submitted'):
        return None
    confirmed = row['stage'] == 'submitted'
    source = row['source']
    if confirmed and source not in ('employer_thanks_page', 'candidate_verification'):
        source = 'historical_submission' if row['requested_at'] else 'manual_attestation'
    history = store.connection.execute("""SELECT state,source,receipt,note,verified_at
        FROM delivery_verifications WHERE job_id=? ORDER BY id""", (job_id,))
    return dict(job_id=job_id, state='confirmed' if confirmed else row['state'] or 'awaiting_confirmation',
                source=source or 'historical_dispatch', destination=row['destination'],
                requested_at=row['requested_at'], approved_at=row['approved_at'],
                confirmed_at=row['application_updated_at'] if confirmed else None,
                updated_at=(row['application_updated_at'] if confirmed else row['updated_at'] or row['requested_at']),
                receipt=row['receipt'], note=row['note'], filename=row['filename'],
                resume_sha256=row['resume_sha256'], answers=json.loads(row['answers']) if row['answers'] else None,
                verifications=[dict(item) for item in history])


class DeliveryConflict(ValueError):
    pass


def resolve_dispatch(store, job_id, state, receipt, note, source):
    with store.connection:
        store.connection.execute('BEGIN IMMEDIATE')
        record = evidence(store, job_id)
        if not record or not record['requested_at']:
            raise DeliveryConflict('Only an existing dispatch can be reconciled.')
        if record['state'] == state:
            if state == 'confirmed' and source == 'employer_thanks_page':
                # A candidate may reconcile while the desktop window is still waiting.
                # Preserve both the first confirmation and the observed employer receipt.
                if not any(v['source'] == source and v['receipt'] == receipt for v in record['verifications']):
                    store.connection.execute("""INSERT INTO delivery_verifications(job_id,state,source,receipt)
                        VALUES(?,?,?,?)""", (job_id, state, source, receipt))
                return
            if record['receipt'] == receipt and record['note'] == note:
                return
            raise DeliveryConflict('A different outcome is already recorded.')
        if record['state'] == 'confirmed':
            raise DeliveryConflict('This dispatch already has a confirmed receipt.')
        if state == 'confirmed':
            store.connection.execute("""INSERT INTO applications(job_id,stage,receipt) VALUES(?,'submitted',?)
                ON CONFLICT(job_id) DO UPDATE SET stage='submitted',receipt=excluded.receipt,
                updated_at=CURRENT_TIMESTAMP""", (job_id, receipt))
        store.connection.execute("""INSERT INTO delivery_records(job_id,destination,state,note,source)
            VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET state=excluded.state,note=excluded.note,
            source=excluded.source,updated_at=CURRENT_TIMESTAMP""",
            (job_id, record['destination'], state, note, source))
        store.connection.execute('INSERT INTO delivery_verifications(job_id,state,source,receipt,note) VALUES(?,?,?,?,?)',
                                 (job_id, state, source, receipt, note))


def reconcile(store, job_id, data):
    if not isinstance(data, dict) or data.get('attest_verified') is not True:
        raise ValueError('Personally verify the outcome with the employer first.')
    state, receipt, note = data.get('state'), data.get('receipt'), data.get('note')
    if state == 'confirmed':
        if data.get('verified_without_reference') is True:
            if receipt or not isinstance(note, str) or not 4 <= len(note.strip()) <= 500:
                raise ValueError('Record how the employer verified receipt without a reference.')
            receipt, note = None, note.strip()
        else:
            if not isinstance(receipt, str) or not 4 <= len(receipt.strip()) <= 200 or note:
                raise ValueError('Provide an employer confirmation reference of 4–200 characters.')
            receipt, note = receipt.strip(), None
    elif state == 'verified_not_received':
        if not isinstance(note, str) or not 4 <= len(note.strip()) <= 500 or receipt:
            raise ValueError('Record how the employer verified non-receipt in 4–500 characters.')
        note, receipt = note.strip(), None
    else:
        raise ValueError('Choose a verified delivery outcome.')
    resolve_dispatch(store, job_id, state, receipt, note, 'candidate_verification')
