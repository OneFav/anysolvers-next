"""Read original receipts, including operator-created lossless archives.

Archiving is never performed by a worker. A missing/corrupt archive remains an
unknown outcome; it is not permission to execute the original request again.
"""
import gzip
import hashlib
import json

MAX_RECEIPT_BYTES = 128 * 1024 * 1024


def read_result(directory):
    original = directory / 'result.json'
    if original.exists():
        if original.stat().st_size > MAX_RECEIPT_BYTES:
            raise ValueError('Oversized remote receipt')
        return json.loads(original.read_bytes())
    metadata = directory / 'result.archive.json'
    if not metadata.exists():
        return None
    if metadata.is_symlink() or metadata.stat().st_size > 8192:
        raise ValueError('Invalid remote archive metadata')
    record = json.loads(metadata.read_bytes())
    if (record.get('schema_version') != 1 or record.get('format') != 'gzip'
            or record.get('original') != 'result.json' or record.get('compressed') != 'result.json.gz'
            or type(record.get('original_bytes')) is not int
            or not 0 < record['original_bytes'] <= MAX_RECEIPT_BYTES):
        raise ValueError('Invalid remote archive contract')
    archive = directory / 'result.json.gz'
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != record['compressed_bytes']:
        raise ValueError('Missing or changed archived receipt')
    if archive.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError('Oversized compressed receipt')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != record['compressed_sha256']:
        raise ValueError('Archived receipt checksum mismatch')
    with gzip.open(archive, 'rb') as stream:
        data = stream.read(record['original_bytes'] + 1)
    if len(data) != record['original_bytes'] or hashlib.sha256(data).hexdigest() != record['original_sha256']:
        raise ValueError('Original receipt checksum mismatch')
    value = json.loads(data)
    if value['request_id'] != directory.name or value['request_sha'] != record['request_sha']:
        raise ValueError('Archived receipt identity mismatch')
    return value
