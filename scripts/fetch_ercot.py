#!/usr/bin/env python3
"""Mirror ERCOT report type 13061 to a GitHub release using only stdlib."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path


REPORT_LIST_URL = (
    "https://www.ercot.com/misapp/servlets/"
    "IceDocListJsonWS?reportTypeId=13061"
)
PRODUCT_URL = (
    "https://www.ercot.com/mp/data-products/"
    "data-product-details?id=NP6-785-ER"
)
DOWNLOAD_URL = (
    "https://www.ercot.com/misdownload/servlets/"
    "mirDownload?doclookupId={doc_id}"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)


def request_headers(*, json_response: bool = False) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*" if json_response else "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": PRODUCT_URL,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def open_with_retries(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    headers: dict[str, str],
    attempts: int = 3,
) -> object:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            return opener.open(request, timeout=120)
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Unable to download {url}: {last_error}")


def load_json(
    opener: urllib.request.OpenerDirector,
    url: str,
) -> dict[str, object]:
    with open_with_retries(
        opener,
        url,
        headers=request_headers(json_response=True),
    ) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8-sig"))


def iter_documents(payload: dict[str, object]):
    root = payload.get("ListDocsByRptTypeRes", {})
    document_list = root.get("DocumentList", []) if isinstance(root, dict) else []
    if not isinstance(document_list, list):
        document_list = [document_list]
    for item in document_list:
        if not isinstance(item, dict):
            continue
        document = item.get("Document", item)
        documents = document if isinstance(document, list) else [document]
        for candidate in documents:
            if isinstance(candidate, dict):
                yield candidate


def document_year(document: dict[str, object]) -> int | None:
    name = str(document.get("FriendlyName", ""))
    if not name.startswith("RTMLZHBSPP_"):
        return None
    suffix = name.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else None


def select_document(payload: dict[str, object]) -> dict[str, object]:
    candidates = [
        document
        for document in iter_documents(payload)
        if document_year(document) is not None
    ]
    if not candidates:
        raise RuntimeError("ERCOT returned no RTMLZHBSPP annual documents.")

    current_year = datetime.now(timezone.utc).year
    same_year = [d for d in candidates if document_year(d) == current_year]
    pool = same_year or candidates
    return max(
        pool,
        key=lambda d: (
            document_year(d) or 0,
            str(d.get("PublishDate", "")),
        ),
    )


def load_previous(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def download_archive(
    opener: urllib.request.OpenerDirector,
    doc_id: str,
    destination: Path,
) -> tuple[int, str]:
    temporary = destination.with_suffix(".partial")
    hasher = hashlib.sha256()
    total = 0
    with open_with_retries(
        opener,
        DOWNLOAD_URL.format(doc_id=doc_id),
        headers=request_headers(),
    ) as response, temporary.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            hasher.update(chunk)
            total += len(chunk)
    temporary.replace(destination)
    return total, hasher.hexdigest()


def validate_archive(path: Path, expected_size: int | None) -> None:
    actual_size = path.stat().st_size
    if actual_size < 100_000:
        raise RuntimeError(f"Downloaded archive is unexpectedly small: {actual_size} bytes.")
    if expected_size and actual_size != expected_size:
        raise RuntimeError(
            f"Downloaded size {actual_size} does not match ERCOT metadata {expected_size}."
        )
    if path.read_bytes()[:2] != b"PK":
        raise RuntimeError("Downloaded file is not a ZIP archive.")
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"ZIP integrity check failed at {bad_member}.")
        if not any(name.lower().endswith(".xlsx") for name in archive.namelist()):
            raise RuntimeError("ERCOT archive contains no XLSX workbook.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar)
    )

    try:
        with open_with_retries(
            opener,
            PRODUCT_URL,
            headers=request_headers(),
            attempts=2,
        ) as response:
            response.read(1)
    except Exception as error:  # Cookie seeding is helpful but not mandatory.
        print(f"Warning: ERCOT landing page could not be preloaded: {error}")

    listing = load_json(opener, REPORT_LIST_URL)
    document = select_document(listing)
    doc_id = str(document.get("DocID", "")).strip()
    constructed_name = str(document.get("ConstructedName", "")).strip()
    friendly_name = str(document.get("FriendlyName", "")).strip()
    publish_date = str(document.get("PublishDate", "")).strip()
    content_size_raw = document.get("ContentSize")
    content_size = int(content_size_raw) if str(content_size_raw).isdigit() else None
    source_year = document_year(document)
    if not doc_id or not constructed_name or source_year is None:
        raise RuntimeError("ERCOT document metadata is incomplete.")

    previous = load_previous(args.previous)
    unchanged = (
        previous is not None
        and str(previous.get("DocID", "")) == doc_id
        and str(previous.get("ConstructedName", "")) == constructed_name
    )
    if unchanged and not args.force:
        (args.output_dir / "changed.txt").write_text("false\n", encoding="ascii")
        print(f"No new ERCOT archive: {constructed_name}")
        return 0

    archive_path = args.output_dir / "ercot-latest.zip"
    downloaded_size, sha256 = download_archive(opener, doc_id, archive_path)
    validate_archive(archive_path, content_size)

    metadata = {
        "DocID": doc_id,
        "ConstructedName": constructed_name,
        "FriendlyName": friendly_name,
        "PublishDate": publish_date,
        "ContentSize": downloaded_size,
        "SourceYear": source_year,
        "Sha256": sha256,
        "AssetName": archive_path.name,
        "MirroredAt": datetime.now(timezone.utc).isoformat(),
        "SourceReportTypeId": 13061,
        "SourceUrl": REPORT_LIST_URL,
    }
    (args.output_dir / "latest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "changed.txt").write_text("true\n", encoding="ascii")
    print(
        f"Downloaded {constructed_name}: {downloaded_size} bytes, "
        f"sha256={sha256}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
