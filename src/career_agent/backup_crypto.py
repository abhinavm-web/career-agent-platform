"""Passphrase protection for offline archives; plaintext staging remains local."""
import os
from pathlib import Path

MAGIC = b'CAREER-BACKUP-ENC\x01'
HEADER_SIZE = len(MAGIC) + 16 + 12
TAG_SIZE = 16


def _crypto():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError:
        raise ValueError('Encrypted backups require: pip install "career-agent-platform[backup]"') from None
    return InvalidTag, Cipher, algorithms, modes, Scrypt


def _key(password, salt, scrypt):
    if not isinstance(password, str) or not password:
        raise ValueError('A nonempty backup passphrase is required')
    return scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode('utf-8'))


def encrypt(source, destination, password):
    _, Cipher, algorithms, modes, Scrypt = _crypto()
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    encryptor = Cipher(algorithms.AES(_key(password, salt, Scrypt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with open(source, 'rb') as src, open(destination, 'xb') as out:
        out.write(header)
        while chunk := src.read(1024 * 1024):
            out.write(encryptor.update(chunk))
        out.write(encryptor.finalize())
        out.write(encryptor.tag)


def decrypt(source, destination, password):
    InvalidTag, Cipher, algorithms, modes, Scrypt = _crypto()
    with open(source, 'rb') as src:
        header = src.read(HEADER_SIZE)
        size = Path(source).stat().st_size
        if len(header) != HEADER_SIZE or not header.startswith(MAGIC) or size < HEADER_SIZE + TAG_SIZE:
            raise ValueError('Invalid encrypted backup')
        src.seek(-TAG_SIZE, os.SEEK_END)
        tag = src.read(TAG_SIZE)
        src.seek(HEADER_SIZE)
        salt, nonce = header[len(MAGIC):len(MAGIC)+16], header[-12:]
        decryptor = Cipher(algorithms.AES(_key(password, salt, Scrypt)), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        remaining = size - HEADER_SIZE - TAG_SIZE
        try:
            with open(destination, 'xb') as out:
                while remaining:
                    chunk = src.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('Invalid encrypted backup')
                    remaining -= len(chunk)
                    out.write(decryptor.update(chunk))
                out.write(decryptor.finalize())
        except InvalidTag:
            raise ValueError('Incorrect passphrase or corrupted encrypted backup') from None
