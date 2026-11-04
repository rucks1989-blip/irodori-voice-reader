"""Verified HTTPS transfer using Python's default certificate/hostname validation.

Fallback for Windows curl/Schannel failures. Never changes system TLS settings.
The destination is a staging file, never a live installed model/runtime.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def require_https(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only HTTPS URLs without embedded credentials are accepted")


def verified(path, sha256, size=0):
    if not path.is_file() or (size and path.stat().st_size != size):
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().lower() == sha256.lower()


def transfer(url, partial, sha256, size=0):
    require_https(url)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        raise ValueError("A pinned SHA-256 is required")
    partial = Path(partial)
    partial.parent.mkdir(parents=True, exist_ok=True)
    if verified(partial, sha256, size):
        print("Verified complete staging file; no transfer needed", flush=True)
        return
    offset = partial.stat().st_size if partial.exists() else 0
    if size and offset >= size:
        raise ValueError("Staging file is full-sized but invalid; quarantine it before retrying")
    # OpenSSL-backed Python verification remains enabled, including hostname
    # checks. Do not set CERT_NONE, check_hostname=False or disable revocation
    # in Windows. Trust stores/availability differ from curl's Schannel path.
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(HTTPSRedirect(), urllib.request.HTTPSHandler(context=context))
    headers = {"User-Agent": "IrodoriVoiceReader-Installer", "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = opener.open(request, timeout=60)
    except urllib.error.HTTPError as error:
        # A server may reject a range instead of ignoring it. One fresh request
        # is safe; retain the old partial until the replacement response opens.
        if error.code != 416 or not offset:
            raise
        headers.pop("Range", None)
        response = opener.open(urllib.request.Request(url, headers=headers), timeout=60)
        offset = 0
    with response:
        require_https(response.url)
        append = response.status == 206 and bool(offset)
        if response.status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", response.headers.get("Content-Range", ""))
            if not match or int(match[1]) != offset:
                raise ValueError("Unexpected HTTP Content-Range; refusing to append")
            if size and match[3] != "*" and int(match[3]) != size:
                raise ValueError("HTTP range total differs from pinned size")
        elif response.status != 200:
            raise ValueError(f"Unexpected HTTP status: {response.status}")
        received = offset if append else 0
        print(f"Verified HTTPS transfer: {'resume at ' + str(offset) if append else 'restart'} bytes", flush=True)
        with partial.open("ab" if append else "wb") as output:
            notice = received
            while block := response.read(1024 * 1024):
                output.write(block)
                received += len(block)
                if size and received > size:
                    raise ValueError("Transfer exceeds pinned size")
                if received - notice >= 16 * 1024 * 1024:
                    print(f"Downloaded {received} / {size or 'unknown'} bytes", flush=True)
                    notice = received
            output.flush()
            os.fsync(output.fileno())
    if not verified(partial, sha256, size):
        raise ValueError("Downloaded file failed pinned size/SHA-256 verification")
    print(f"SHA-256 verified: {sha256}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--partial", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--bytes", type=int, default=0)
    args = parser.parse_args()
    try:
        transfer(args.url, args.partial, args.sha256, args.bytes)
    except Exception as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        category = ("certificate" if isinstance(reason, ssl.SSLError) else
                    "http" if isinstance(error, urllib.error.HTTPError) else
                    "verification" if isinstance(error, ValueError) else "network")
        print(json.dumps({"category": category, "code": getattr(error, "code", type(reason).__name__),
                          "message": str(reason)}, ensure_ascii=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
