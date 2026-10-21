from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QMimeData, QUrl

from fs_ops import file_signature

FILE_MIME = "application/x-file-tree-viewer-files"
DROP_EFFECT = 'application/x-qt-windows-mime;value="Preferred DropEffect"'


def file_mime(paths, *, cut=False, entries=None):
    items = (
        entries
        if entries is not None
        else [{"path": str(path), "signature": file_signature(path)} for path in paths]
    )
    payload = {"token": uuid4().hex, "cut": cut, "entries": items}
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(item["path"]) for item in items])
    data.setData(FILE_MIME, json.dumps(payload).encode("utf-8"))
    data.setData(DROP_EFFECT, (2 if cut else 1).to_bytes(4, "little"))
    data.setData("application/x-kde-cutselection", b"1" if cut else b"0")
    data.setData(
        "x-special/gnome-copied-files",
        (
            ("cut" if cut else "copy")
            + "\n"
            + "\n".join(url.toString() for url in data.urls())
        ).encode("utf-8"),
    )
    return data


def read_file_mime(data):
    if data is None or not data.hasUrls():
        raise ValueError("The clipboard does not contain local files")
    urls = data.urls()
    if not urls or len(urls) > 100000 or any(not url.isLocalFile() for url in urls):
        raise ValueError("Only local file URLs can be pasted")
    paths = [Path(url.toLocalFile()) for url in urls]
    if any(not path.is_absolute() for path in paths):
        raise ValueError("Clipboard paths must be absolute")
    raw = bytes(data.data(FILE_MIME))
    payload = None
    if raw:
        if len(raw) > 8 * 1024**2:
            raise ValueError("Clipboard file information exceeds the limit")
        try:
            payload = json.loads(raw)
            entries = payload["entries"]
            if not isinstance(entries, list) or [item["path"] for item in entries] != [
                str(path) for path in paths
            ]:
                raise ValueError("Clipboard paths do not match their file information")
            if not isinstance(payload["token"], str) or not isinstance(
                payload["cut"], bool
            ):
                raise TypeError("Invalid clipboard operation")
            for item in entries:
                if (
                    not isinstance(item["signature"], dict)
                    or not item["signature"]
                    or any(not isinstance(v, int) for v in item["signature"].values())
                ):
                    raise ValueError("Invalid clipboard file identity")
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid clipboard file information") from error
    effect = bytes(data.data(DROP_EFFECT))
    cut = (
        payload["cut"]
        if payload
        else (
            int.from_bytes(effect[:4], "little") == 2
            or bytes(data.data("application/x-kde-cutselection")) == b"1"
        )
    )
    return paths, cut, payload
