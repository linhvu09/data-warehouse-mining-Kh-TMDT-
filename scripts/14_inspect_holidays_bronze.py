import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config import get_minio_client, required


PROJECT_ROOT = Path(__file__).resolve().parents[1]

BRONZE_ROOT = "bronze/external/nager_date"

EXPECTED_FILES = {
    2016: "public_holidays_BR_2016.json",
    2017: "public_holidays_BR_2017.json",
    2018: "public_holidays_BR_2018.json",
}

SOURCE_COLUMNS = [
    "date",
    "localName",
    "name",
    "countryCode",
    "fixed",
    "global",
    "counties",
    "launchYear",
    "types",
]


def read_object(client, bucket, object_name):
    """Đọc object từ MinIO và đóng kết nối sau khi dùng."""
    response = client.get_object(
        bucket_name=bucket,
        object_name=object_name,
    )

    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def is_missing(value):
    """Kiểm tra thiếu giá trị; không coi False là thiếu."""
    return value is None or (
        isinstance(value, str) and not value.strip()
    )


def main():
    client = get_minio_client()
    bucket = required("MINIO_BUCKET")

    # 1. Tìm bộ Bronze hoàn chỉnh mới nhất.
    pointer = json.loads(
        read_object(
            client,
            bucket,
            f"{BRONZE_ROOT}/latest.json",
        )
    )

    if pointer.get("status") != "completed":
        raise ValueError("Bộ Bronze mới nhất chưa hoàn chỉnh.")

    run_id = pointer["run_id"]

    if not isinstance(run_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", run_id
    ):
        raise ValueError("run_id không hợp lệ.")

    prefix = f"{BRONZE_ROOT}/runs/{run_id}"

    manifest = json.loads(
        read_object(
            client,
            bucket,
            f"{prefix}/manifest.json",
        )
    )

    if (
        manifest.get("status") != "completed"
        or manifest.get("run_id") != run_id
    ):
        raise ValueError("Manifest không khớp bộ Bronze.")

    entries = manifest.get("files", [])

    if (
        len(entries) != 3
        or {item.get("year") for item in entries}
        != set(EXPECTED_FILES)
    ):
        raise ValueError("Manifest chưa có đủ ba năm.")

    # 2. Đọc và xác minh nội dung ba JSON.
    records = []
    signatures = []

    for entry in entries:
        year = entry["year"]
        filename = EXPECTED_FILES[year]

        if entry.get("filename") != filename:
            raise ValueError(f"Tên file không khớp: {year}")

        body = read_object(
            client,
            bucket,
            f"{prefix}/{filename}",
        )

        actual_hash = hashlib.sha256(body).hexdigest()

        if actual_hash != entry.get("sha256"):
            raise ValueError(
                f"Nội dung không khớp manifest: {filename}"
            )

        payload = json.loads(body)

        if not isinstance(payload, list) or not payload:
            raise ValueError(
                f"{filename}: không phải danh sách không rỗng."
            )

        for row in payload:
            if not isinstance(row, dict):
                raise ValueError(
                    f"{filename}: có bản ghi không phải object."
                )

            # Chữ ký chỉ dùng nội dung nguồn, không gồm cột hỗ trợ.
            signatures.append(
                json.dumps(
                    row,
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )

            records.append({
                **row,
                "_source_year": year,
            })

    # 3. Tạo bảng để khảo sát.
    df = pd.DataFrame(records)

    for column in SOURCE_COLUMNS:
        if column not in df.columns:
            df[column] = None

    # Dùng None thay cho giá trị thiếu của Pandas.
    df = df.astype(object).where(pd.notna(df), None)

    parsed_dates = pd.to_datetime(
        df["date"],
        format="%Y-%m-%d",
        errors="coerce",
    )

    wrong_year = (
        parsed_dates.notna()
        & parsed_dates.dt.year.ne(df["_source_year"])
    )

    wrong_country = df["countryCode"].ne("BR")

    invalid_global = df["global"].map(
        lambda value: not isinstance(value, bool)
    )

    invalid_types = df["types"].map(
        lambda value: (
            not isinstance(value, list)
            or not value
            or any(
                not isinstance(item, str) or not item.strip()
                for item in value
            )
        )
    )

    invalid_counties = df["counties"].map(
        lambda value: (
            value is not None
            and (
                not isinstance(value, list)
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in value
                )
            )
        )
    )

    missing_counts = {
        column: int(df[column].map(is_missing).sum())
        for column in SOURCE_COLUMNS
    }

    type_counts = Counter()

    for value in df["types"]:
        if isinstance(value, list):
            type_counts.update(
                item for item in value
                if isinstance(item, str)
            )

    county_codes = sorted({
        item
        for value in df["counties"]
        if isinstance(value, list)
        for item in value
        if isinstance(item, str)
    })

    by_year = []

    for year in EXPECTED_FILES:
        selected = df["_source_year"].eq(year)
        part = df.loc[selected]
        dates = parsed_dates.loc[selected]

        by_year.append({
            "year": year,
            "record_count": len(part),
            "distinct_valid_dates": int(
                dates.dropna().nunique()
            ),
            "global_true_count": int(
                part["global"].map(
                    lambda value: value is True
                ).sum()
            ),
            "global_false_count": int(
                part["global"].map(
                    lambda value: value is False
                ).sum()
            ),
        })

    # Nhiều bản ghi cùng ngày là trường hợp cần xem xét.
    same_date_mask = (
        parsed_dates.notna()
        & parsed_dates.duplicated(keep=False)
    )

    repeated_dates = df.loc[
        same_date_mask,
        ["date", "name", "localName", "global", "types", "counties"],
    ].sort_values("date")

    report = {
        "source_run_id": run_id,
        "profiled_at_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "total_records": len(df),
        "by_year": by_year,
        "missing_counts": missing_counts,
        "invalid_date_count": int(
            parsed_dates.isna().sum()
        ),
        "wrong_year_count": int(wrong_year.sum()),
        "wrong_country_count": int(wrong_country.sum()),
        "invalid_global_count": int(invalid_global.sum()),
        "invalid_types_count": int(invalid_types.sum()),
        "invalid_counties_count": int(invalid_counties.sum()),
        "exact_duplicate_count": (
            len(signatures) - len(set(signatures))
        ),
        "dates_with_multiple_records": int(
            parsed_dates.loc[same_date_mask].nunique()
        ),
        "type_counts": dict(sorted(type_counts.items())),
        "county_codes": county_codes,
    }

    # 4. In và lưu báo cáo.
    report_dir = (
        PROJECT_ROOT
        / "data"
        / "reports"
        / "external"
        / "nager_date"
        / run_id
    )

    report_dir.mkdir(parents=True, exist_ok=True)

    report_text = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    )

    (report_dir / "holiday_profile.json").write_text(
        report_text,
        encoding="utf-8",
    )

    details = (
        repeated_dates.to_string(index=False)
        if not repeated_dates.empty
        else "Không có nhiều bản ghi cùng ngày."
    )

    (report_dir / "holiday_profile.txt").write_text(
        "BÁO CÁO KHẢO SÁT NGÀY LỄ BRAZIL\n\n"
        + report_text
        + "\n\nCÁC BẢN GHI CÙNG NGÀY\n"
        + details,
        encoding="utf-8",
    )

    print("\nTHỐNG KÊ THEO NĂM")
    print(pd.DataFrame(by_year).to_string(index=False))

    print("\nKẾT QUẢ KHẢO SÁT")
    print(report_text)

    print("\nCÁC BẢN GHI CÙNG NGÀY")
    print(details)

    print("\nHOÀN THÀNH KHẢO SÁT BRONZE")
    print(f"Báo cáo được lưu tại: {report_dir}")


if __name__ == "__main__":
    main()