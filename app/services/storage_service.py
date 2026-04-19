from __future__ import annotations

import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError


AWS_REGION = os.getenv("AWS_REGION", "eu-west-2")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = (os.getenv("S3_PREFIX", "qrma") or "").strip("/")

if not S3_BUCKET:
    raise RuntimeError("S3_BUCKET is not set.")


def _build_client():
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


s3 = _build_client()


def normalise_key(key: str) -> str:
    key = key.lstrip("/")
    if S3_PREFIX:
        return f"{S3_PREFIX}/{key}"
    return key


def upload_bytes(key: str, data: bytes, content_type: str) -> str:
    final_key = normalise_key(key)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=final_key,
        Body=data,
        ContentType=content_type,
    )
    return final_key


def upload_text(key: str, text: str, content_type: str = "text/html; charset=utf-8") -> str:
    return upload_bytes(key, text.encode("utf-8"), content_type)


def download_bytes(key: str) -> bytes:
    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return obj["Body"].read()


def object_exists(key: Optional[str]) -> bool:
    if not key:
        return False
    try:
        s3.head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )