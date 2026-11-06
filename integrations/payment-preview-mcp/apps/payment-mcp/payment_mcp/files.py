import hashlib
from pathlib import Path
import tempfile
import zipfile

from .auth import PaymentError


def validate_xlsx(path):
    if not zipfile.is_zipfile(path):
        raise PaymentError('invalid_file', '请提供未加密的 .xlsx 文件；加密或其他格式请先在平台处理。')
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if not {'[Content_Types].xml', 'xl/workbook.xml'}.issubset(names):
                raise ValueError()
            if any(i.flag_bits & 1 for i in z.infolist()) or any('vbaproject' in n.lower() for n in names):
                raise ValueError()
            if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024 or len(names) > 10000:
                raise ValueError()
    except (ValueError, zipfile.BadZipFile):
        raise PaymentError('invalid_file', '表格格式异常、包含宏或解压后过大，请在平台检查。') from None


async def stage(upload, actor, settings, store):
    name = upload.filename or ''
    if not name or len(name) > 240 or '/' in name or '\\' in name or Path(name).suffix.lower() != '.xlsx':
        raise PaymentError('invalid_file', '每次请发送一个未加密的 .xlsx 表格。')
    fd, path = tempfile.mkstemp(suffix='.xlsx', dir=store.root / 'attachments')
    import os
    size, digest = 0, hashlib.sha256()
    try:
        with os.fdopen(fd, 'wb') as output:
            while block := await upload.read(65536):
                size += len(block)
                if size > settings.max_bytes:
                    raise PaymentError('file_too_large', '表格超过 20 MB，请在平台上传。', 413)
                digest.update(block)
                output.write(block)
        if not size:
            raise PaymentError('invalid_file', '表格为空，请重新发送。')
        validate_xlsx(path)
        row = store.save_attachment(actor, name, path, digest.hexdigest(), size)
        if row['path'] != path:
            Path(path).unlink(missing_ok=True)
        return {'attachment_ref': row['ref'], 'file_name': row['name'], 'size_bytes': row['size']}
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
