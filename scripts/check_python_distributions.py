"""Check upload artifacts before granting a publishing job credentials."""
import argparse
import email.parser
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def validate_members(names):
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe archive path: {name}")
        if any(part in {".git", ".venv", "__pycache__", "node_modules", "runtime"} for part in path.parts):
            raise ValueError(f"Local/runtime content in distribution: {name}")
        if path.suffix.lower() in {".pyc", ".pyo", ".dmg", ".gguf", ".safetensors", ".sqlite3"}:
            raise ValueError(f"Unexpected binary or local data: {name}")
        if path.name in {".env", "direct_url.json", ".pypirc"}:
            raise ValueError(f"Private configuration in distribution: {name}")


def check(directory, version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable MAJOR.MINOR.PATCH version")
    wheel = directory / f"machboost-{version}-py3-none-any.whl"
    source = directory / f"machboost-{version}.tar.gz"
    if set(directory.iterdir()) != {wheel, source}:
        raise ValueError("Upload directory must contain exactly the expected wheel and source archive")
    with zipfile.ZipFile(wheel) as archive:
        validate_members(archive.namelist())
        if any(PurePosixPath(name).parts[0] not in {"machboost", f"machboost-{version}.dist-info"}
               for name in archive.namelist()):
            raise ValueError("Wheel contains non-package files")
        metadata = email.parser.BytesParser().parsebytes(
            archive.read(f"machboost-{version}.dist-info/METADATA")
        )
        if metadata["Name"] != "machboost" or metadata["Version"] != version:
            raise ValueError("Wheel metadata does not match the release")
        if metadata.get("Author-email") or metadata.get("Maintainer-email"):
            raise ValueError("Distribution metadata must not expose personal contact addresses")
    with tarfile.open(source, "r:gz") as archive:
        validate_members(archive.getnames())
        if any(member.issym() or member.islnk() for member in archive.getmembers()):
            raise ValueError("Source archive must not contain links")
        metadata = email.parser.BytesParser().parsebytes(
            archive.extractfile(f"machboost-{version}/PKG-INFO").read()
        )
        if metadata["Name"] != "machboost" or metadata["Version"] != version:
            raise ValueError("Source metadata does not match the release")
    print(f"Verified wheel and source distribution for machboost {version}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    check(args.directory, args.version)
