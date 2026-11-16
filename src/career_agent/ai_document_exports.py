"""Literal, offline exports of reviewed AI documents; no model calls or private notes."""
import io
from html import escape

from .resume_docx import DOCXUnavailable
from .resume_pdf import html_pdf


def document_blocks(revision, kind, snapshot):
    if kind not in {'resume', 'cover_letter'}:
        raise ValueError('Choose resume or cover_letter')
    blocks = [('name', snapshot['name'])]
    if snapshot['contact']:
        blocks.append(('contact', snapshot['contact']))
    if kind == 'resume':
        if snapshot['headline']:
            blocks.append(('paragraph', snapshot['headline']))
        sections = {}
        for row in revision['document']['resume']:
            sections.setdefault(row.get('section', 'experience'), []).append(row['text'])
        for section, statements in sections.items():
            blocks.append(('section', section.title()))
            blocks.extend(('paragraph' if section == 'summary' else 'bullet', text) for text in statements)
    else:
        blocks.append(('paragraph', 'Dear Hiring Team,'))
        blocks.extend(('paragraph', row['text']) for row in revision['document']['cover_letter'])
        blocks.append(('paragraph', 'Sincerely,\n' + snapshot['name']))
    for _, value in blocks:
        if (not isinstance(value, str) or any((ord(c) < 32 and c not in '\t\r\n')
                or 0xd800 <= ord(c) <= 0xdfff or ord(c) in (0xfffe, 0xffff) for c in value)):
            raise ValueError('Document contains unsupported characters')
    if sum(len(value) for _, value in blocks) > 100_000:
        raise ValueError('Document exceeds the export size limit')
    return blocks


def document_html(revision, kind, snapshot):
    tags = {'name': 'h1', 'section': 'h2', 'paragraph': 'p', 'contact': 'p', 'bullet': 'li'}
    parts, in_list = [], False
    for block, value in document_blocks(revision, kind, snapshot):
        if block != 'bullet' and in_list:
            parts.append('</ul>'); in_list = False
        if block == 'bullet' and not in_list:
            parts.append('<ul>'); in_list = True
        tag = tags[block]
        parts.append(f'<{tag} class="{block}">{escape(value)}</{tag}>')
    if in_list:
        parts.append('</ul>')
    css = '''@page { size: Letter; margin: .65in .7in; }
    * { box-sizing: border-box; }
    body { font-family: Arial, sans-serif; font-size: 10.5pt; line-height: 1.4; color: #202a38; margin: 0; }
    h1 { font-size: 23pt; line-height: 1.15; margin: 0 0 7pt; color: #172d46; break-after: avoid; }
    h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: .7pt; color: #172d46;
         border-bottom: 1px solid #b7c3d1; margin: 15pt 0 6pt; padding-bottom: 4pt; break-after: avoid; }
    p, li, h1, h2 { white-space: pre-wrap; overflow-wrap: anywhere; orphans: 3; widows: 3; }
    p { margin: 0 0 8pt; } .contact { font-size: 9.5pt; color: #455365; margin-bottom: 12pt; }
    ul { padding-left: 16pt; margin: 0; } li { margin: 0 0 6pt; break-inside: avoid; }
    '''
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>' + ('Resume' if kind == 'resume' else 'Cover letter') + '</title><style>' + css +
            '</style></head><body>' + '\n'.join(parts) + '</body></html>')


def document_pdf(revision, kind, snapshot):
    return html_pdf(document_html(revision, kind, snapshot))


def document_docx(revision, kind, snapshot):
    blocks = document_blocks(revision, kind, snapshot)
    try:
        from docx import Document
        from docx.shared import Inches, Pt, RGBColor
    except ImportError as error:
        raise DOCXUnavailable('Install the web extra to export Word documents') from error
    document = Document()
    document.core_properties.author = ''
    document.core_properties.last_modified_by = ''
    for section in document.sections:
        section.page_width, section.page_height = Inches(8.5), Inches(11)
        section.top_margin = section.bottom_margin = Inches(.65)
        section.left_margin = section.right_margin = Inches(.7)
    normal = document.styles['Normal']
    normal.font.name, normal.font.size = 'Calibri', Pt(10.5)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.15
    normal.paragraph_format.widow_control = True
    for label, size in (('Title', 23), ('Heading 1', 11)):
        style = document.styles[label]
        style.font.name, style.font.size = 'Calibri', Pt(size)
        style.font.color.rgb = RGBColor.from_string('172D46')
        style.paragraph_format.keep_with_next = True
    for block, value in blocks:
        if block == 'name':
            document.add_heading(value, level=0)
        elif block == 'section':
            document.add_heading(value, level=1)
        else:
            paragraph = document.add_paragraph(value, style='List Bullet' if block == 'bullet' else None)
            if block == 'bullet':
                paragraph.paragraph_format.keep_together = True
            if block == 'contact':
                for run in paragraph.runs:
                    run.font.size = Pt(9.5)
    result = io.BytesIO()
    document.save(result)
    return result.getvalue()
