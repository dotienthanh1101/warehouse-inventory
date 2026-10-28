"""Streaming AES-256-GCM with scrypt. No passwords persisted or scheduled jobs."""
import hashlib
import os
import re
from pathlib import Path,PurePosixPath
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC=b'WHFULL01'
HEADER=36
LIMIT=60*1024**3


def derive(password,salt):
    return Scrypt(salt=salt,length=32,n=32768,r=8,p=1).derive(password.encode('utf-8'))


def encrypt(source,destination,password):
    if Path(source).stat().st_size>LIMIT:raise ValueError('Bản sao vượt giới hạn mã hóa.')
    salt=os.urandom(16);nonce=os.urandom(12);header=MAGIC+salt+nonce
    cipher=Cipher(algorithms.AES(derive(password,salt)),modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(header);digest=hashlib.sha256()
    with Path(source).open('rb') as src,Path(destination).open('xb') as dst:
        os.chmod(destination,0o600);dst.write(header)
        for chunk in iter(lambda:src.read(1024*1024),b''):
            digest.update(chunk);dst.write(cipher.update(chunk))
        dst.write(cipher.finalize());dst.write(cipher.tag)
    return digest.hexdigest()


def decrypt(source,password,output=None):
    """Write only to a private temporary stream; trust it after finalize succeeds."""
    digest=hashlib.sha256()
    with Path(source).open('rb') as src:
        size=src.seek(0,2)
        if not HEADER+16<=size<=LIMIT+HEADER+16:raise ValueError('Kích thước bản sao không hợp lệ.')
        src.seek(0);header=src.read(HEADER)
        if header[:8]!=MAGIC:raise ValueError('Không đúng định dạng bản sao toàn dự án.')
        src.seek(-16,2);tag=src.read(16);src.seek(HEADER)
        cipher=Cipher(algorithms.AES(derive(password,header[8:24])),modes.GCM(header[24:36],tag)).decryptor()
        cipher.authenticate_additional_data(header);left=size-HEADER-16
        while left:
            chunk=src.read(min(left,1024*1024))
            if not chunk:raise ValueError('Bản sao bị ngắt giữa chừng.')
            left-=len(chunk);plain=cipher.update(chunk);digest.update(plain)
            if output is not None:output.write(plain)
        plain=cipher.finalize();digest.update(plain)
        if output is not None:output.write(plain)
    return digest.hexdigest()


def safe_name(name):
    path=PurePosixPath(name)
    if not name or path.is_absolute() or '\\' in name or str(path)!=name or any(p in ('','.','..') for p in name.split('/')):
        raise ValueError('Đường dẫn trong bản sao không an toàn.')
    for part in path.parts:
        if any(ord(c)<32 or c in '<>:"|?*' for c in part) or part.endswith((' ','.')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part):
            raise ValueError('Tên file trong bản sao không an toàn.')
    return path
