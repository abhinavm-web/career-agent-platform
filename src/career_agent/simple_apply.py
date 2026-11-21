"""The beginner path: read a résumé, then show original and recommended pages plus a cover letter.

Everything here is deterministic and works offline. The recommended résumé and the
template cover letter contain only lines the candidate confirmed as true, copied
word for word. An optional AI cover letter is checked so it cannot introduce
numbers that appear in neither the confirmed lines nor the posting.
"""
from __future__ import annotations

import io
import re

from .resume_compare import _normalize, compare

HEADINGS = {
    'experience': 'experience', 'work experience': 'experience', 'professional experience': 'experience',
    'employment': 'experience', 'employment history': 'experience', 'work history': 'experience',
    'summary': 'experience', 'professional summary': 'experience',
    'education': 'education', 'academic background': 'education',
    'skills': 'skills', 'technical skills': 'skills', 'core skills': 'skills', 'key skills': 'skills',
    'projects': 'projects', 'selected projects': 'projects',
    'certifications': 'certifications', 'certificates': 'certifications', 'licenses': 'certifications',
}
TITLES = {'experience': 'Experience', 'projects': 'Projects', 'skills': 'Skills',
          'education': 'Education', 'certifications': 'Certifications'}
MAX_LINES = 100
CONTACT = re.compile(r'@|https?://|www\.|linkedin\.com|github\.com|\+?\d[\d\s().-]{7,}\d', re.I)
BULLET = re.compile(r'^[\s•·▪●–—*\-]+')
NUMBER = re.compile(r'\d+(?:[.,]\d+)*%?')


STOPWORDS = {
    'that', 'with', 'this', 'from', 'have', 'were', 'been', 'will', 'their', 'which', 'about', 'into', 'your', 'they',
    'them', 'than', 'then', 'when', 'what', 'where', 'while', 'also', 'such', 'must', 'should', 'would', 'could', 'other',
    'work', 'working', 'team', 'years', 'year', 'experience', 'ability', 'skills', 'skill', 'strong', 'including', 'using',
    'used', 'able', 'role', 'job', 'looking', 'good', 'well', 'great', 'plus', 'preferred', 'required', 'requirements',
    'responsibilities', 'qualifications', 'company', 'candidate', 'ideal', 'need', 'needs', 'help', 'make', 'across'}


def _stems(text):
    """Plain-word roots (budgeting, budgets -> budget) so ordinary wording can match the posting."""
    roots = set()
    for word in re.findall(r"[a-z][a-z+#]{3,}", text.casefold()):
        if word in STOPWORDS:
            continue
        for suffix in ('ing', 'ed', 'es', 's'):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[:-len(suffix)]
                break
        if word.endswith('e') and len(word) > 4:
            word = word[:-1]
        roots.add(word)
    return roots


def _heading(line):
    return HEADINGS.get(re.sub(r'[:\s]+$', '', line.strip()).casefold())


def _clean(line):
    return re.sub(r'\s+', ' ', BULLET.sub('', line)).strip()


def parse_resume(text):
    """Split résumé text into a name, contact line and individual lines to confirm."""
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    has_headings = any(_heading(line) for line in rows)
    name, contact, lines = '', [], []
    section, in_body = 'experience', not has_headings
    for index, stripped in enumerate(rows):
        heading = _heading(stripped)
        if heading:
            section, in_body = heading, True
            continue
        is_contact = bool(CONTACT.search(stripped)) and len(stripped) <= 80
        if not name and not is_contact and len(stripped) <= 60 and index < 3:
            name = stripped  # the first short, non-contact line near the top
            continue
        if is_contact:
            contact.append(stripped)
            continue
        if not in_body:  # a headline or summary line above the first heading
            continue
        statement = _clean(stripped)
        if 8 <= len(statement) <= 1000:
            lines.append({'text': statement, 'section': section})
    return {'name': name, 'contact': ' · '.join(dict.fromkeys(contact))[:300],
            'lines': lines[:MAX_LINES], 'truncated': len(lines) > MAX_LINES}


def original_rows(text):
    """The left-hand page: the saved résumé as written, with headings recognised."""
    rows, blank = [], True
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            if not blank:
                rows.append({'kind': 'blank', 'text': ''})
            blank = True
            continue
        rows.append({'kind': 'heading' if _heading(stripped) else 'line', 'text': stripped})
        blank = False
    return rows


def cover_letter(name, job, sections):
    """A plain template letter that quotes the candidate's most relevant confirmed lines."""
    bullets = [b for section in sections for b in section['bullets']]
    ranked = sorted((b for b in bullets if b['shared']), key=lambda b: -b['shared'])
    chosen = [b['text'] for b in (ranked or bullets)[:3]]
    paragraphs = [
        {'type': 'text', 'text': 'Dear Hiring Team,'},
        {'type': 'text', 'text': f'I am excited to apply for the {job.title} position at {job.company}. '
                                 'I believe my background is a good match for this role.'}]
    if chosen:
        paragraphs.append({'type': 'text', 'text': 'Here is some of what I have done that relates to this job:'})
        paragraphs.extend({'type': 'bullet', 'text': text} for text in chosen)
    paragraphs.extend([
        {'type': 'text', 'text': f'I would welcome the chance to talk about how I can help {job.company}. '
                                 'Thank you for your time and consideration.'},
        {'type': 'text', 'text': 'Sincerely,'}, {'type': 'text', 'text': name or 'Your name'}])
    return {'paragraphs': paragraphs, 'written_by': 'template'}


def build(original_text, profile, job, facts, workspace_revision=0):
    posting = _stems(f'{job.title} {job.description}')
    haystack = _normalize(original_text)
    by_section = {key: [] for key in TITLES}
    for fact in facts:
        shared = len(_stems(fact['statement']) & posting)
        in_original = bool(haystack) and _normalize(fact['statement']) in haystack
        added_here = fact.get('source_note') == f'Added on job {job.id}'
        if shared or in_original or added_here:
            by_section.setdefault(fact['section'], []).append(
                {'text': fact['statement'], 'new': not in_original, 'fact_id': fact['id'], 'shared': shared})
    sections = []
    for key, title in TITLES.items():
        bullets = sorted(by_section.get(key, []), key=lambda b: (-b['shared'], b['new']))[:12]
        if bullets:
            sections.append({'title': title, 'bullets': bullets})
    asks = [re.sub(r'^[-*\u2022]\s*', '', g['quote']).split('\n')[0].strip()
            for g in compare(original_text, job, facts, workspace_revision)['gaps']]
    resume = {'name': profile.get('name', ''), 'headline': profile.get('headline', ''),
              'contact': profile.get('resume_contact', ''),
              'sections': [{'title': sec['title'], 'bullets': [{k: b[k] for k in ('text', 'new', 'fact_id')} for b in sec['bullets']]}
                           for sec in sections]}
    return {'original': original_rows(original_text), 'resume': resume,
            'cover_letter': cover_letter(profile.get('name', ''), job, sections),
            'added_count': sum(b['new'] for sec in sections for b in sec['bullets']),
            'job_asks': [a for a in asks if a][:4]}


def check_ai_letter(letter, claims, job):
    """Accept an AI letter only if it adds no placeholders and no unsupported numbers."""
    if not isinstance(letter, str) or not letter.strip() or len(letter) > 4000:
        raise ValueError('The AI letter was empty or too long; nothing was accepted')
    if re.search(r'\[[^\]]*\]|\{[^}]*\}|<[^>]*>', letter):
        raise ValueError('The AI letter contained a placeholder; nothing was accepted')
    allowed = set(NUMBER.findall(' '.join(claims.values()) + ' ' + job.description + ' ' + job.title))
    if [n for n in NUMBER.findall(letter) if n not in allowed]:
        raise ValueError('The AI letter used a number that is not in your confirmed lines or the job posting; nothing was accepted')
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', letter.strip()) if p.strip()]
    return [{'type': 'text', 'text': p} for p in paragraphs][:20]


def resume_docx(resume):
    from docx import Document
    from docx.shared import Inches, Pt
    document = Document()
    for section in document.sections:
        section.left_margin = section.right_margin = Inches(1)
        section.top_margin = section.bottom_margin = Inches(0.8)
    document.styles['Normal'].font.name = 'Calibri'
    document.styles['Normal'].font.size = Pt(11)
    title = document.add_paragraph()
    run = title.add_run(resume['name'] or 'Your name')
    run.bold = True
    run.font.size = Pt(20)
    for line in (resume['headline'], resume['contact']):
        if line:
            document.add_paragraph(line)
    for section in resume['sections']:
        heading = document.add_paragraph()
        part = heading.add_run(section['title'].upper())
        part.bold = True
        for bullet in section['bullets']:
            document.add_paragraph(bullet['text'], style='List Bullet')
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def letter_docx(paragraphs):
    from docx import Document
    from docx.shared import Inches, Pt
    document = Document()
    for section in document.sections:
        section.left_margin = section.right_margin = Inches(1)
    document.styles['Normal'].font.name = 'Calibri'
    document.styles['Normal'].font.size = Pt(11)
    for item in paragraphs:
        document.add_paragraph(item['text'], style='List Bullet' if item['type'] == 'bullet' else None)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
