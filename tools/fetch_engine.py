"""Download approved official assets into distinct new directories; never run from them."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ENGINE_URL = (
    "https://github.com/lightvector/KataGo/releases/download/v1.18.1/"
    "katago-v1.18.1-opencl-windows-x64.zip"
)
ENGINE_SHA256 = "1710db1903ab921aa6837a9599c8474f8a59f057650217c5d9bc125ee393a9ff"
MODEL_NAME = "kata1-b18c384nbt-s9996604416-d4316597426.bin.gz"
MODEL_URL = "https://media.katagotraining.org/uploaded/networks/models/kata1/" + MODEL_NAME


def download(url: str, destination: Path, expected_digest: str | None = None) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "YehuStyleGo-local-setup"})
    digest = hashlib.sha256()
    count = 0
    with urllib.request.urlopen(request, timeout=90) as response, destination.open("xb") as output:
        size = int(response.headers.get("Content-Length", "0"))
        previous_progress = -1
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            count += len(chunk)
            progress = count // (10 * 1024 * 1024)
            if progress != previous_progress:
                print(f"{destination.name}: {count / 1048576:.1f} / {size / 1048576:.1f} MiB", flush=True)
                previous_progress = progress
    if size and count != size:
        raise ValueError("Download ended before the expected length")
    actual_digest = digest.hexdigest()
    if expected_digest and actual_digest != expected_digest:
        raise ValueError("Official engine archive SHA-256 mismatch; it will not be extracted")
    return {"url": url, "path": str(destination), "size": count, "sha256": actual_digest,
            "digest_verified_against_release": expected_digest is not None}


def extract_archive(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as package:
        if sum(item.file_size for item in package.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Unexpected archive expansion size")
        for item in package.infolist():
            path = PurePosixPath(item.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
                raise ValueError("Unsafe archive entry")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive contains a symbolic link")
        package.extractall(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    archive_dir = Path(tempfile.mkdtemp(prefix="archive-", dir=args.destination))
    extracted_dir = Path(tempfile.mkdtemp(prefix="opencl-1.18.1-", dir=args.destination))
    model_dir = Path(tempfile.mkdtemp(prefix="model-b18-", dir=args.destination))
    archive = archive_dir / "katago.zip"
    engine = download(ENGINE_URL, archive, ENGINE_SHA256)
    extract_archive(archive, extracted_dir)
    executables = list(extracted_dir.rglob("katago.exe"))
    if len(executables) != 1:
        raise ValueError("Expected exactly one katago.exe in the official archive")
    model = download(MODEL_URL, model_dir / MODEL_NAME)
    manifest = {
        "engine": engine, "model": model, "executable": str(executables[0]),
        "model_path": model["path"], "backend": "OpenCL", "version": "1.18.1",
    }
    manifest_path = args.destination / "setup-manifest.json"
    if manifest_path.exists():
        raise FileExistsError("Setup manifest already exists; choose a new destination")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
