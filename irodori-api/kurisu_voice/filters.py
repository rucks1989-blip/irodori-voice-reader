import re


def normalize_markers(text):
    source = str(text or "")
    source = re.sub(r"([「『（\(【\[])\s*[＠@]{2}\s*", r"\1", source)
    source = re.sub(r"(^|[\s\u3000])[＠@]{2}\s*", r"\1", source)
    return source.replace("@@", "").replace("＠＠", "")


def remove_ignored_texts(text, ignored_texts):
    source = str(text or "")
    values = [ignored_texts] if isinstance(ignored_texts, str) else (ignored_texts or [])
    for ignored in sorted({str(value) for value in values if str(value)}, key=len, reverse=True):
        source = source.replace(ignored, "")
    return source

