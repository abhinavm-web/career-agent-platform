"""Local, bounded résumé text extraction; extracted text is never verified automatically."""
from __future__ import annotations

import base64
import binascii
import io
import zipfile
from xml.etree import ElementTree

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 20000


def extract_resume(filename, encoded):
    if not isinstance(filename, str) or not isinstance(encoded, str):
        raise ValueError('Choose a PDF, DOCX, or UTF-8 text file')
    if len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError('Résumé file must be at most 2 MB')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('Invalid résumé file encoding') from None
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Résumé file must be nonempty and at most 2 MB')
    extension = filename.rsplit('.', 1)[-1].lower()
    if extension == 'txt':
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('Text résumé must use UTF-8 encoding') from None
    elif extension == 'pdf':
        if not data.startswith(b'%PDF-'):
            raise ValueError('File is not a PDF')
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('Install PDF résumé support: pip install -e ".[resume]"') from None
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            encrypted = reader.is_encrypted
            page_count = 0 if encrypted else len(reader.pages)
        except Exception:
            raise ValueError('Unable to read this PDF') from None
        if encrypted:
            raise ValueError('Password-protected PDFs are not supported')
        if page_count > 30:
            raise ValueError('PDF résumé must have at most 30 pages')
        pages = []
        try:
            for page in reader.pages:
                pages.append(page.extract_text() or '')
                if sum(map(len, pages)) > MAX_TEXT:
                    break
        except Exception:
            raise ValueError('Unable to extract text from this PDF') from None
        text = '\n'.join(pages)
    elif extension == 'docx':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                matches = [item for item in archive.infolist() if item.filename == 'word/document.xml']
                if len(matches) != 1 or matches[0].file_size > 4 * 1024 * 1024 or matches[0].flag_bits & 1:
                    raise ValueError('Invalid or oversized Word document')
                root = ElementTree.fromstring(archive.read(matches[0]))
            namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(item.text or '' for item in paragraph.iter(namespace + 't'))
                             for paragraph in root.iter(namespace + 'p'))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, NotImplementedError):
            raise ValueError('Unable to extract text from this Word document') from None
    else:
        raise ValueError('Use a PDF, DOCX, or UTF-8 text file')
    if not text.strip():
        raise ValueError('No text found; scanned images require OCR and are not supported')
    if '\x00' in text or len(text) > MAX_TEXT:
        raise ValueError('Extracted résumé text is invalid or exceeds 20,000 characters')
    return text.strip()
