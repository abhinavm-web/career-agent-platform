"""Export reviewed résumé text to a local, editable Word document."""
from __future__ import annotations

import io

from .application import export_resume_blocks, validate_packet


class DOCXUnavailable(RuntimeError):
    """The optional Word renderer is unavailable."""


def resume_docx(packet: dict) -> bytes:
    """Render the saved, fingerprinted résumé only; never use private notes."""
    validate_packet(packet)
    text = packet['resume_markdown']
    if not isinstance(text, str) or len(text) > 200_000:
        raise ValueError('Resume exceeds the Word export limit')
    if any((ord(c) < 32 and c not in '\t\r\n') or 0xd800 <= ord(c) <= 0xdfff or ord(c) in (0xfffe, 0xffff) for c in text):
        raise ValueError('Resume contains characters unsupported by Word')
    try:
        from docx import Document
        from docx.shared import Inches, Pt
    except ImportError as error:
        raise DOCXUnavailable('Install the web extra to export Word documents') from error
    document = Document()
    for section in document.sections:
        section.top_margin = section.bottom_margin = Inches(0.7)
        section.left_margin = section.right_margin = Inches(0.75)
    normal = document.styles['Normal']
    normal.font.name = 'Calibri'
    normal.font.size = Pt(10.5)
    document.core_properties.author = ''
    document.core_properties.last_modified_by = ''
    blocks = export_resume_blocks(packet)
    if blocks is not None:
        for block in blocks:
            kind, value = block['kind'], block['text']
            if kind == 'name': document.add_heading(value, level=0)
            elif kind == 'section': document.add_heading(value, level=1)
            elif kind == 'fact': document.add_paragraph(value, style='List Bullet')
            else: document.add_paragraph(value)
        result = io.BytesIO()
        document.save(result)
        return result.getvalue()
    for line in text.splitlines():
        if line.startswith('# '):
            document.add_heading(line[2:], level=0)
        elif line.startswith('## '):
            document.add_heading(line[3:], level=1)
        elif line.startswith('- '):
            document.add_paragraph(line[2:], style='List Bullet')
        elif line.strip():
            document.add_paragraph(line)
    result = io.BytesIO()
    document.save(result)
    return result.getvalue()
