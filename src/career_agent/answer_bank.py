"""Candidate-entered screening answers. A role confirmation binds to the exact question."""

TOPICS = {
    'work_authorization': 'Current work authorization',
    'sponsorship_now': 'Sponsorship required now',
    'sponsorship_future': 'Sponsorship required in the future',
    'h1b_transfer': 'H-1B transfer',
    'availability': 'Start date or notice period',
    'compensation': 'Compensation expectations',
    'relocation': 'Willingness to relocate',
    'travel': 'Willingness to travel',
}


class AnswerConflict(ValueError):
    pass


def _text(data, key, *, allow_empty=False):
    value = data.get(key)
    if (not isinstance(value, str) or len(value) > 4000 or '\x00' in value
            or (not value.strip() and not (allow_empty and value == ''))):
        raise ValueError(f'Provide valid {key} text (up to 4000 characters)')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(f'Invalid characters in {key}') from None
    return value


def _topic(data, *, role=False):
    if not isinstance(data, dict) or data.get('confirmed') is not True:
        raise ValueError('Explicit confirmation is required')
    topic = data.get('topic')
    if not isinstance(topic, str) or not (topic in TOPICS or (role and topic == 'other')):
        raise ValueError('Select a question topic')
    return topic


def _check_revision(expected, current):
    if type(expected) is not int or expected < 0:
        raise ValueError('Provide the reviewed revision')
    if expected != current:
        raise AnswerConflict('Answers changed. Reload and review the current wording and answer.')


def profile_answers(conn):
    saved = {row['topic']: row for row in conn.execute('SELECT * FROM profile_answers')}
    return [{'topic': topic, 'label': label,
             'answer': saved[topic]['answer'] if topic in saved else '',
             'revision': saved[topic]['revision'] if topic in saved else 0}
            for topic, label in TOPICS.items()]


def save_profile(conn, data):
    topic = _topic(data)
    answer = _text(data, 'answer', allow_empty=True)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT revision FROM profile_answers WHERE topic=?', (topic,)).fetchone()
        _check_revision(data.get('expected_revision'), row['revision'] if row else 0)
        conn.execute('''INSERT INTO profile_answers VALUES(?,?,1)
            ON CONFLICT(topic) DO UPDATE SET answer=excluded.answer, revision=revision+1
            WHERE answer != excluded.answer''', (topic, answer))
        return profile_answers(conn)


def role_answers(store, job_id):
    locked = store.stage_of(job_id) == 'submitted'
    rows = store.connection.execute('''SELECT r.*, p.revision AS current_profile_revision
        FROM role_answers r LEFT JOIN profile_answers p ON r.topic=p.topic
        WHERE r.job_id=? ORDER BY r.rowid''', (job_id,))
    answers = []
    for row in rows:
        reused = row['profile_revision'] is not None
        stale = reused and row['profile_revision'] != row['current_profile_revision']
        answers.append({'question': row['question'], 'topic': row['topic'], 'revision': row['revision'],
                        'answer': '' if stale else row['answer'], 'reused': reused, 'stale': stale,
                        'historical_answer': row['answer'] if locked and stale else None})
    return {'locked': locked, 'answers': answers, 'bank': profile_answers(store.connection)}


def save_role(store, job_id, data):
    topic = _topic(data, role=True)
    question = _text(data, 'question')
    answer = _text(data, 'answer', allow_empty=True)
    mode = data.get('mode')
    if mode not in ('reuse', 'role', 'unanswered') or (mode == 'unanswered') != (answer == ''):
        raise ValueError('Confirm a non-empty answer or explicitly leave the question unanswered')
    conn = store.connection
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if store.stage_of(job_id) == 'submitted':
            raise AnswerConflict('Answers are locked for a submitted role')
        row = conn.execute('SELECT revision FROM role_answers WHERE job_id=? AND question=?',
                           (job_id, question)).fetchone()
        _check_revision(data.get('expected_revision'), row['revision'] if row else 0)
        profile_revision = None
        if mode == 'reuse':
            profile = conn.execute('SELECT * FROM profile_answers WHERE topic=?', (topic,)).fetchone()
            if not profile or profile['answer'] != answer:
                raise AnswerConflict('The reviewed profile answer is unavailable. Reload the answer bank.')
            profile_revision = profile['revision']
            _check_revision(data.get('profile_revision'), profile_revision)
        conn.execute('''INSERT INTO role_answers(job_id,question,topic,answer,profile_revision,revision)
            VALUES(?,?,?,?,?,1) ON CONFLICT(job_id,question) DO UPDATE SET
            topic=excluded.topic, answer=excluded.answer, profile_revision=excluded.profile_revision,
            revision=revision+1''', (job_id, question, topic, answer, profile_revision))
        return role_answers(store, job_id)
