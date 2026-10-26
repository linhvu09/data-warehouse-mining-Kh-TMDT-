import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


# Xác định thư mục gốc BTLKhTmdt.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Giữ nguyên ba địa chỉ API đã chọn.
API_URLS = {
    2016: "https://date.nager.at/api/v3/PublicHolidays/2016/BR",
    2017: "https://date.nager.at/api/v3/PublicHolidays/2017/BR",
    2018: "https://date.nager.at/api/v3/PublicHolidays/2018/BR",
}


def main():
    # Mỗi lần chạy có một thư mục riêng.
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid4().hex[:8]
    )

    base_dir = (
        PROJECT_ROOT
        / "data"
        / "raw"
        / "external"
        / "nager_date"
    )

    output_dir = base_dir / "runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    manifest = {
        "source": "Nager.Date",
        "country_code": "BR",
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "files": [],
    }

    try:
        for year, url in API_URLS.items():
            print(f"\nĐang tải dữ liệu năm {year}...")
            print(f"Địa chỉ: {url}")

            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "OlistAcademicProject/1.0",
                },
            )

            with urlopen(request, timeout=30) as response:
                raw_data = response.read()

                http_status = response.status
                final_url = response.geturl()
                content_type = response.headers.get(
                    "Content-Type", ""
                )

            filename = f"public_holidays_BR_{year}.json"
            output_path = output_dir / filename

            # Lưu nguyên nội dung nhận được từ API.
            output_path.write_bytes(raw_data)

            file_info = {
                "year": year,
                "filename": filename,
                "requested_url": url,
                "final_url": final_url,
                "http_status": http_status,
                "content_type": content_type,
                "downloaded_at_utc": (
                    datetime.now(timezone.utc).isoformat()
                ),
                "size_bytes": len(raw_data),
                "sha256": hashlib.sha256(raw_data).hexdigest(),
                "validation_status": "pending",
            }

            manifest["files"].append(file_info)

            # Kiểm tra phản hồi có phải danh sách JSON không.
            records = json.loads(raw_data)

            if not isinstance(records, list) or not records:
                raise ValueError(
                    f"Năm {year}: cần danh sách JSON không rỗng."
                )

            required_fields = {
                "date",
                "localName",
                "name",
                "countryCode",
                "global",
                "counties",
                "types",
            }

            for index, record in enumerate(records, start=1):
                if not isinstance(record, dict):
                    raise ValueError(
                        f"Năm {year}, dòng {index}: "
                        "không phải đối tượng JSON."
                    )

                missing = required_fields - record.keys()

                if missing:
                    raise ValueError(
                        f"Năm {year}, dòng {index}: "
                        f"thiếu trường {sorted(missing)}"
                    )

                holiday_date = date.fromisoformat(
                    record["date"]
                )

                if holiday_date.year != year:
                    raise ValueError(
                        f"Năm {year}: phát hiện ngày sai năm."
                    )

                if record["countryCode"] != "BR":
                    raise ValueError(
                        f"Năm {year}: mã quốc gia không phải BR."
                    )

                if not isinstance(record["global"], bool):
                    raise ValueError(
                        f"Năm {year}: global không phải boolean."
                    )

                if not isinstance(record["types"], list):
                    raise ValueError(
                        f"Năm {year}: types không phải danh sách."
                    )

                if (
                    record["counties"] is not None
                    and not isinstance(record["counties"], list)
                ):
                    raise ValueError(
                        f"Năm {year}: counties không hợp lệ."
                    )

            dates = [record["date"] for record in records]

            unique_records = {
                json.dumps(record, sort_keys=True)
                for record in records
            }

            file_info.update({
                "validation_status": "passed",
                "record_count": len(records),
                "distinct_date_count": len(set(dates)),
                "exact_duplicate_count": (
                    len(records) - len(unique_records)
                ),
                "global_record_count": sum(
                    record["global"] for record in records
                ),
                "types_found": sorted({
                    str(holiday_type)
                    for record in records
                    for holiday_type in record["types"]
                }),
            })

            print(f"Đã lưu: {output_path}")
            print(f"Số bản ghi: {len(records)}")
            print(f"Số ngày khác nhau: {len(set(dates))}")
            print(
                "Số bản ghi trùng hoàn toàn: "
                f"{file_info['exact_duplicate_count']}"
            )
            print(
                "Các loại ngày lễ: "
                f"{file_info['types_found']}"
            )

        manifest["status"] = "completed"
        manifest["completed_at_utc"] = (
            datetime.now(timezone.utc).isoformat()
        )

    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        print(f"\nTHU THẬP CHƯA HOÀN TẤT: {error}")
        raise

    finally:
        # Luôn ghi nhật ký, kể cả khi xảy ra lỗi.
        manifest_path = output_dir / "manifest.json"

        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # Chỉ cập nhật khi cả ba năm đã tải và kiểm tra xong.
    (base_dir / "latest_download.json").write_text(
        json.dumps(
            {"run_id": run_id},
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\nHOÀN THÀNH THU THẬP CẢ BA NĂM")
    print(f"Thư mục kết quả: {output_dir}")


if __name__ == "__main__":
    main()