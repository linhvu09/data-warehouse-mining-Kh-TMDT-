import hashlib
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pandas as pd

from config import get_minio_client, required


ROOT = Path(__file__).resolve().parents[1]
BRONZE_ROOT = "bronze/external/nager_date"
SILVER_ROOT = "silver/external/nager_date"

YEARS = (2016, 2017, 2018)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_object(client, bucket, object_name):
    response = client.get_object(
        bucket_name=bucket,
        object_name=object_name,
    )

    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def upload_verified(client, bucket, object_name, path):
    client.fput_object(
        bucket_name=bucket,
        object_name=object_name,
        file_path=str(path),
        content_type=(
            "application/json"
            if path.suffix == ".json"
            else "application/octet-stream"
        ),
    )

    remote_data = read_object(client, bucket, object_name)

    if sha256(remote_data) != sha256(path.read_bytes()):
        raise ValueError(
            f"File trên MinIO không khớp: {object_name}"
        )

    print(f"Đã lưu và xác minh: {object_name}")


def main():
    client = get_minio_client()
    bucket = required("MINIO_BUCKET")

    # 1. Xác định bộ Bronze hoàn chỉnh.
    pointer = json.loads(
        read_object(
            client, bucket, f"{BRONZE_ROOT}/latest.json"
        )
    )

    if pointer.get("status") != "completed":
        raise ValueError("Bộ Bronze chưa hoàn chỉnh.")

    source_run_id = pointer["run_id"]

    if not isinstance(source_run_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", source_run_id
    ):
        raise ValueError("Mã lần chạy Bronze không hợp lệ.")

    source_prefix = (
        f"{BRONZE_ROOT}/runs/{source_run_id}"
    )

    source_manifest = json.loads(
        read_object(
            client,
            bucket,
            f"{source_prefix}/manifest.json",
        )
    )

    if (
        source_manifest.get("status") != "completed"
        or source_manifest.get("run_id") != source_run_id
    ):
        raise ValueError("Manifest Bronze không hợp lệ.")

    entries = source_manifest.get("files", [])

    if (
        len(entries) != 3
        or {entry.get("year") for entry in entries}
        != set(YEARS)
    ):
        raise ValueError("Bronze phải có đủ ba năm.")

    rows = []
    original_signatures = []

    # 2. Đọc, kiểm tra và chuẩn hóa từng bản ghi.
    for entry in entries:
        year = entry["year"]
        filename = f"public_holidays_BR_{year}.json"

        if entry.get("filename") != filename:
            raise ValueError(f"Tên file sai ở năm {year}.")

        body = read_object(
            client, bucket, f"{source_prefix}/{filename}"
        )

        if sha256(body) != entry.get("sha256"):
            raise ValueError(
                f"Nội dung Bronze đã thay đổi: {filename}"
            )

        records = json.loads(body)

        if not isinstance(records, list) or not records:
            raise ValueError(
                f"{filename}: dữ liệu không hợp lệ."
            )

        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise ValueError(
                    f"{year}, dòng {index}: không phải object."
                )

            original_signatures.append(
                json.dumps(record, sort_keys=True)
            )

            holiday_date = datetime.strptime(
                record["date"], "%Y-%m-%d"
            ).date()

            if holiday_date.year != year:
                raise ValueError(
                    f"{year}, dòng {index}: ngày sai năm."
                )

            if record.get("countryCode") != "BR":
                raise ValueError(
                    f"{year}, dòng {index}: sai quốc gia."
                )

            is_global = record.get("global")
            types = record.get("types")
            counties = record.get("counties")

            if not isinstance(is_global, bool):
                raise ValueError("global phải là boolean.")

            if (
                not isinstance(types, list)
                or not types
                or any(
                    not isinstance(x, str) or not x.strip()
                    for x in types
                )
            ):
                raise ValueError("types không hợp lệ.")

            if counties is not None and (
                not isinstance(counties, list)
                or any(
                    not isinstance(x, str) or not x.strip()
                    for x in counties
                )
            ):
                raise ValueError("counties không hợp lệ.")

            name = record.get("name")
            local_name = record.get("localName")

            if (
                not isinstance(name, str) or not name.strip()
                or not isinstance(local_name, str)
                or not local_name.strip()
            ):
                raise ValueError(
                    f"{year}, dòng {index}: thiếu tên ngày lễ."
                )

            # Loại khoảng trắng và phần tử lặp trong danh sách.
            clean_types = sorted({x.strip() for x in types})

            clean_counties = (
                sorted({x.strip() for x in counties})
                if counties is not None
                else None
            )

            # Không tự giải quyết thông tin phạm vi mâu thuẫn.
            if is_global and clean_counties:
                raise ValueError(
                    f"{year}, dòng {index}: global=true "
                    "nhưng counties có giá trị; cần kiểm tra."
                )

            rows.append({
                "holiday_date": holiday_date,
                "country_code": "BR",
                "holiday_name": name.strip(),
                "holiday_local_name": local_name.strip(),
                "is_global": is_global,
                "types_json": json.dumps(
                    clean_types, ensure_ascii=False
                ),
                "counties_json": json.dumps(
                    clean_counties, ensure_ascii=False
                ),
                "is_national_public_holiday": (
                    is_global and "Public" in clean_types
                ),
            })

    # 3. Loại bản ghi giống nhau sau chuẩn hóa.
    events = pd.DataFrame(rows)
    before_count = len(events)

    events = (
        events.drop_duplicates()
        .sort_values(["holiday_date", "holiday_name"])
        .reset_index(drop=True)
    )

    selected = events.loc[
        events["is_national_public_holiday"]
    ].copy()

    if selected.empty:
        raise ValueError(
            "Không có bản ghi thỏa quy tắc ngày lễ đã chọn. "
            "Cần xem lại kết quả khảo sát."
        )

    # Tổng hợp nhiều sự kiện cùng ngày thành một dòng.
    selected_days = (
        selected.groupby("holiday_date", as_index=False)
        .agg(
            national_public_event_count=(
                "holiday_name", "size"
            ),
            national_public_holiday_names=(
                "holiday_name",
                lambda values: " | ".join(
                    sorted(set(values))
                ),
            ),
        )
    )

    # 4. Tạo lịch đủ từng ngày cho ba năm.
    calendar = pd.DataFrame({
        "calendar_date": pd.date_range(
            "2016-01-01", "2018-12-31", freq="D"
        )
    })

    # Thứ Hai = 0, Chủ nhật = 6.
    calendar["day_of_week"] = (
        calendar["calendar_date"].dt.dayofweek
    )

    calendar["is_weekend"] = (
        calendar["day_of_week"] >= 5
    )

    calendar["year"] = (
        calendar["calendar_date"].dt.year
    )

    calendar["month"] = (
        calendar["calendar_date"].dt.month
    )

    calendar["calendar_date"] = (
        calendar["calendar_date"].dt.date
    )

    calendar = calendar.merge(
        selected_days,
        left_on="calendar_date",
        right_on="holiday_date",
        how="left",
        validate="one_to_one",
    ).drop(columns="holiday_date")

    calendar["national_public_event_count"] = (
        calendar["national_public_event_count"]
        .fillna(0)
        .astype("int64")
    )

    calendar["is_national_public_holiday"] = (
        calendar["national_public_event_count"] > 0
    )

    calendar["national_public_holiday_names"] = (
        calendar["national_public_holiday_names"]
        .fillna("")
    )

    # 5. Kiểm tra chất lượng đầu ra.
    expected_days = len(pd.date_range(
        "2016-01-01", "2018-12-31", freq="D"
    ))

    if (
        len(calendar) != expected_days
        or calendar["calendar_date"].duplicated().any()
    ):
        raise ValueError("Lịch đầu ra thiếu hoặc trùng ngày.")

    annual_summary = (
        calendar.groupby("year", as_index=False)
        .agg(
            calendar_days=("calendar_date", "size"),
            selected_holiday_days=(
                "is_national_public_holiday", "sum"
            ),
        )
    )

    report = {
        "source_bronze_run_id": source_run_id,
        "policy": 'global=true AND types contains "Public"',
        "raw_record_count": before_count,
        "raw_exact_duplicate_count": (
            len(original_signatures)
            - len(set(original_signatures))
        ),
        "silver_event_count": len(events),
        "removed_after_normalization": (
            before_count - len(events)
        ),
        "selected_event_count": len(selected),
        "calendar_row_count": len(calendar),
        "duplicate_calendar_dates": 0,
        "by_year": json.loads(
            annual_summary.to_json(orient="records")
        ),
    }

    # 6. Lưu Silver theo một mã lần chạy mới.
    silver_run_id = (
        datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        + "_"
        + uuid4().hex[:8]
    )

    output_dir = (
        ROOT
        / "data"
        / "processed"
        / "external"
        / "nager_date"
        / "silver"
        / "runs"
        / silver_run_id
    )

    output_dir.mkdir(parents=True, exist_ok=False)

    events.to_parquet(
        output_dir / "holiday_events.parquet",
        index=False,
        engine="pyarrow",
    )

    calendar.to_parquet(
        output_dir / "calendar_br.parquet",
        index=False,
        engine="pyarrow",
    )

    write_json(output_dir / "quality_report.json", report)

    # Đọc lại Parquet để kiểm tra số dòng.
    for filename, expected_count in [
        ("holiday_events.parquet", len(events)),
        ("calendar_br.parquet", len(calendar)),
    ]:
        actual = pd.read_parquet(output_dir / filename)

        if len(actual) != expected_count:
            raise ValueError(
                f"Số dòng Parquet không khớp: {filename}"
            )

    filenames = [
        "holiday_events.parquet",
        "calendar_br.parquet",
        "quality_report.json",
    ]

    manifest = {
        "status": "completed",
        "silver_run_id": silver_run_id,
        "source_bronze_run_id": source_run_id,
        "created_at_utc": utc_now(),
        "policy": report["policy"],
        "files": [
            {
                "filename": filename,
                "sha256": sha256(
                    (output_dir / filename).read_bytes()
                ),
            }
            for filename in filenames
        ],
    }

    write_json(output_dir / "manifest.json", manifest)

    # 7. Upload và xác minh trước khi công bố latest.
    silver_prefix = (
        f"{SILVER_ROOT}/runs/{silver_run_id}"
    )

    for filename in filenames + ["manifest.json"]:
        upload_verified(
            client,
            bucket,
            f"{silver_prefix}/{filename}",
            output_dir / filename,
        )

    latest = {
        "status": "completed",
        "silver_run_id": silver_run_id,
        "source_bronze_run_id": source_run_id,
        "prefix": silver_prefix,
        "published_at_utc": utc_now(),
    }

    latest_bytes = json.dumps(
        latest, ensure_ascii=False, indent=2
    ).encode("utf-8")

    client.put_object(
        bucket_name=bucket,
        object_name=f"{SILVER_ROOT}/latest.json",
        data=BytesIO(latest_bytes),
        length=len(latest_bytes),
        content_type="application/json",
    )

    write_json(
        output_dir.parents[1] / "latest.json",
        latest,
    )

    print("\nTHỐNG KÊ LỊCH THEO NĂM")
    print(annual_summary.to_string(index=False))

    print("\nBÁO CÁO CHẤT LƯỢNG")
    print(json.dumps(
        report, ensure_ascii=False, indent=2
    ))

    print("\nHOÀN THÀNH SILVER NGÀY LỄ")
    print(f"Thư mục trên máy: {output_dir}")
    print(f"Đường dẫn MinIO: {bucket}/{silver_prefix}")


if __name__ == "__main__":
    main()