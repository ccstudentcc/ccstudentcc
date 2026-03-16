from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / ".github/manager/state/metadata-store.json"


def iso_from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def checksum_of(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not MANIFEST_PATH.exists():
        print("manifest not found:", MANIFEST_PATH)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    prior_docs = {d.get("path"): d for d in manifest.get("documents", [])}

    store_paths = {d.get("path") for d in manifest.get("documents", [])}
    updated_docs: list[dict[str, Any]] = []
    missing = []
    total_size = 0

    for path in sorted(store_paths):
        repo_path = ROOT / path
        exists = repo_path.exists()
        size = repo_path.stat().st_size if exists and repo_path.is_file() else 0
        mtime_iso = iso_from_mtime(repo_path.stat().st_mtime) if exists and repo_path.is_file() else None
        chksum = checksum_of(repo_path) if exists and repo_path.is_file() else None

        prior = prior_docs.get(path, {})
        # Preserve prior.updated_at when checksum unchanged
        if prior and prior.get("checksum") and chksum and prior.get("checksum") == chksum:
            updated_at = prior.get("updated_at")
        else:
            updated_at = mtime_iso

        if not exists:
            missing.append(path)

        if size:
            total_size += int(size)

        updated_docs.append(
            {
                "name": prior.get("name") or Path(path).name,
                "path": path,
                "exists": exists,
                "size_bytes": int(size),
                "updated_at": updated_at,
                "checksum": chksum,
            }
        )

    # Update manifest fields
    manifest["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest["total_size_bytes"] = total_size
    manifest["documents"] = updated_docs
    manifest["missing_documents"] = missing
    manifest["consistency_status"] = "healthy" if not missing else "degraded"
    manifest["last_persisted_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", MANIFEST_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
