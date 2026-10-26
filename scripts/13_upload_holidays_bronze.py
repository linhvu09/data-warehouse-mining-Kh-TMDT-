import hashlib
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from config import get_minio_client, required


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "external"
    / "nager_date"
)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def upload_and_verify(client, bucket, object_name, file_path):
    """Upload file rồi đọc lại để kiểm tra nội dung."""
    local_data = file_path.read_bytes()
    local_hash = sha256_bytes(local_data)

    client.fput_object(
        bucket_name=bucket,
        object_name=object_name,
        file_path=str(file_path),
        content_type="application/json",
    )

    response = client.get_object(
        bucket_name=bucket,
        object_name=object_name,
    )

    try:
        remote_data = response.read()
    finally:
        response.close()
        response.release_conn()

    if sha256_bytes(remote_data) != local_hash:
        raise ValueError(
            f"Nội dung trên MinIO không khớp: {object_name}"
        )

    print(f"Đã upload và kiểm tra: {object_name}")


def main():
    # 1. Xác định lần tải hoàn chỉnh gần nhất.
    pointer_path = SOURCE_DIR / "latest_download.json"

    pointer = json.loads(
        pointer_path.read_text(encoding="utf-8")
    )

    run_id = pointer["run_id"]

    if not isinstance(run_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", run_id
    ):
        raise ValueError("run_id không hợp lệ.")

    run_dir = SOURCE_DIR / "runs" / run_id
    manifest_path = run_dir / "manifest.json"

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if manifest.get("status") != "completed":
        raise ValueError(
            "Lần tải này chưa hoàn tất. Không thực hiện upload."
        )

    if manifest.get("run_id") != run_id:
        raise ValueError("run_id trong manifest không khớp.")

    # 2. Kiểm tra đủ ba file và đúng nội dung đã tải.
    expected = {
        2016: "public_holidays_BR_2016.json",
        2017: "public_holidays_BR_2017.json",
        2018: "public_holidays_BR_2018.json",
    }

    entries = manifest.get("files", [])

    if (
        len(entries) != 3
        or {entry.get("year") for entry in entries}
        != set(expected)
    ):
        raise ValueError(
            "Manifest phải có đủ ba năm 2016, 2017, 2018."
        )

    files_to_upload = []

    for entry in entries:
        filename = expected[entry["year"]]

        if entry.get("filename") != filename:
            raise ValueError(
                f"Tên file không đúng: {entry.get('filename')}"
            )

        file_path = run_dir / filename
        actual_hash = sha256_bytes(file_path.read_bytes())

        if actual_hash != entry.get("sha256"):
            raise ValueError(
                f"File đã thay đổi sau khi tải: {filename}"
            )

        files_to_upload.append(file_path)

    # 3. Dùng kết nối MinIO và bucket của dự án.
    client = get_minio_client()
    bucket = required("MINIO_BUCKET")

    if not client.bucket_exists(bucket):
        raise ValueError(
            f"Không tìm thấy bucket của dự án: {bucket}"
        )

    prefix = f"bronze/external/nager_date/runs/{run_id}"

    print(f"Bucket: {bucket}")
    print(f"Lần tải: {run_id}")

    # 4. Upload và xác minh cả ba JSON.
    for file_path in files_to_upload:
        upload_and_verify(
            client,
            bucket,
            f"{prefix}/{file_path.name}",
            file_path,
        )

    # 5. Chỉ upload manifest sau khi ba JSON đã được xác minh.
    upload_and_verify(
        client,
        bucket,
        f"{prefix}/manifest.json",
        manifest_path,
    )

    # 6. Công bố lần upload hoàn chỉnh mới nhất.
    bronze_pointer = {
        "run_id": run_id,
        "bucket": bucket,
        "prefix": prefix,
        "status": "completed",
        "uploaded_at_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
    }

    pointer_data = json.dumps(
        bronze_pointer,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    client.put_object(
        bucket_name=bucket,
        object_name="bronze/external/nager_date/latest.json",
        data=BytesIO(pointer_data),
        length=len(pointer_data),
        content_type="application/json",
    )

    print("\nHOÀN THÀNH UPLOAD BRONZE")
    print("Đã lưu và xác minh ba JSON cùng manifest.")
    print(f"Đường dẫn: {bucket}/{prefix}")


if __name__ == "__main__":
    main()