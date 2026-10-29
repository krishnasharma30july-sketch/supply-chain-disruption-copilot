"""Locate the Olist CSVs: data/raw/ first, then download with kagglehub (as in dataset.txt)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)

RAW_FILES: dict[str, str] = {
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "translation": "product_category_name_translation.csv",
    "geolocation": "olist_geolocation_dataset.csv",
}
KAGGLE_DATASET = "olistbr/brazilian-ecommerce"


class DatasetMissing(RuntimeError):
    pass


def _missing(raw_dir: Path) -> list[str]:
    return [f for f in RAW_FILES.values() if not (raw_dir / f).exists()]


def ensure_raw(data_dir: Path, import_dir: Path | None = None) -> Path:
    """Return the directory holding the CSVs: DATA_DIR/raw, filled from IMPORT_DIR (a read-only drop-in folder,
    ./data/raw on the host in docker-compose) or else downloaded from Kaggle."""
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    missing = _missing(raw_dir)
    if not missing:
        return raw_dir
    if import_dir is None:
        from app.core.config import get_settings

        import_dir = get_settings().import_dir
    if import_dir is not None and import_dir.is_dir():
        for name in list(missing):
            found = next(iter(sorted(import_dir.rglob(name))), None)
            if found is not None:
                shutil.copyfile(found, raw_dir / name)
                missing.remove(name)
        if not missing:
            log.info("dataset_imported", source=str(import_dir))
            return raw_dir
    log.info("dataset_download_start", dataset=KAGGLE_DATASET, missing=missing)
    try:
        import kagglehub

        src = Path(kagglehub.dataset_download(KAGGLE_DATASET))
    except Exception as e:
        target = f"{import_dir} (./data/raw on the host with docker compose)" if import_dir else str(raw_dir)
        raise DatasetMissing(
            f"Could not download {KAGGLE_DATASET} ({e}). Download it from "
            "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce and extract the CSV files into "
            f"{target} (missing: {', '.join(missing)})."
        ) from e
    for name in missing:
        candidates = list(src.rglob(name))
        if not candidates:
            raise DatasetMissing(f"{name} was not found in the downloaded dataset at {src}.")
        shutil.copy2(candidates[0], raw_dir / name)
    log.info("dataset_download_done", path=str(raw_dir))
    return raw_dir


def file_hashes(raw_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, name in RAW_FILES.items():
        h = hashlib.sha256()
        with (raw_dir / name).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        out[key] = h.hexdigest()
    return out
