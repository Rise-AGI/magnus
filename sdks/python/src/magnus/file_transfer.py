# sdks/python/src/magnus/file_transfer.py
FILE_SECRET_PREFIX = "magnus-secret:"


def is_file_secret(value: str) -> bool:
    return value.startswith(FILE_SECRET_PREFIX)


def normalize_secret(file_secret: str) -> str:
    if file_secret.startswith(FILE_SECRET_PREFIX):
        return file_secret[len(FILE_SECRET_PREFIX):]
    return file_secret
