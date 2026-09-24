"""Receive mission artifacts with path confinement and verified resumption."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import time

import httpx


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def receive_file(event: dict, client, workspace: str | Path = "") -> Path:
    """Publish a local file only after its complete contents have been checked."""
    filename = event.get("filename")
    if (not isinstance(filename, str) or not filename or "\\" in filename
            or "\x00" in filename or ":" in filename
            or PurePosixPath(filename).is_absolute() or PureWindowsPath(filename).drive
            or any(part in ("", ".", "..") for part in filename.split("/"))):
        raise ValueError("Invalid transfer filename")
    root = Path(workspace or os.getcwd()).resolve()
    target = root.joinpath(*filename.split("/"))
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Transfer path escapes the workspace")
    target.parent.mkdir(parents=True, exist_ok=True)
    checksum = event.get("sha256", "")
    size = event.get("size")
    remote = event.get("url")
    if remote:
        if (not isinstance(remote, str) or not re.fullmatch(r"/api/cli/artifacts/[0-9a-f]{32}", remote)
                or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
                or type(size) is not int or size < 0):
            raise ValueError("Invalid artifact descriptor")
    elif not isinstance(event.get("data"), str):
        raise ValueError("Missing transfer contents")
    if checksum and target.is_file() and target.stat().st_size == size and _digest(target) == checksum:
        return target
    identity = hashlib.sha256((filename + checksum).encode()).hexdigest()
    partial = target.parent / f".aurora-download-{identity}.part"
    if partial.is_symlink():
        raise ValueError("Invalid partial download path")
    if remote:
        fresh_digest = None
        for attempt in range(4):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > size:
                partial.unlink()
                offset = 0
            if partial.exists() and offset == size:
                break
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers.update({"Range": f"bytes={offset}-", "If-Range": f'"{checksum}"'})
            try:
                with client._client.stream("GET", remote, headers=headers, follow_redirects=False) as response:
                    response.raise_for_status()
                    if response.status_code == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if not match or tuple(map(int, match.groups())) != (offset, size - 1, size):
                            raise ValueError("Invalid download range")
                        mode = "ab"
                    elif response.status_code == 200:
                        offset = 0
                        mode = "wb"
                        fresh_digest = hashlib.sha256()
                    else:
                        raise ValueError("Unexpected download response")
                    with partial.open(mode) as writer:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            offset += len(chunk)
                            if offset > size:
                                raise ValueError("Download exceeds declared size")
                            writer.write(chunk)
                            if fresh_digest:
                                fresh_digest.update(chunk)
                    if offset != size:
                        raise httpx.ReadError("Incomplete artifact download")
                break
            except httpx.HTTPStatusError as exc:
                if attempt == 3 or exc.response.status_code not in (500, 502, 503, 504, 530):
                    raise
                time.sleep(2 ** attempt)
            except httpx.RequestError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
    else:
        partial.write_bytes(base64.b64decode(event["data"], validate=True))
    if size is not None and partial.stat().st_size != size:
        partial.unlink(missing_ok=True)
        raise ValueError("Transfer size mismatch")
    if remote and fresh_digest is not None:
        observed = fresh_digest.hexdigest()
    else:
        observed = _digest(partial)
    if checksum and observed != checksum:
        partial.unlink(missing_ok=True)
        raise ValueError("Transfer checksum mismatch")
    partial.replace(target)
    return target
