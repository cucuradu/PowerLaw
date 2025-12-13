#!/usr/bin/env python3
"""
MinIO Client - Object Storage for Raw Data
Handles upload/download of PDFs, raw news, and research papers.
"""

import os
import io
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from dotenv import load_dotenv

load_dotenv()

# MinIO configuration
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# Bucket names
BUCKET_LAWS = "laws"
BUCKET_NEWS = "news"
BUCKET_PAPERS = "papers"

def log(message: str) -> None:
    """Simple timestamped logger"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [MinIO] {message}")


class MinioClient:
    """
    MinIO client for raw data storage.
    
    Usage:
        client = MinioClient()
        
        # Upload PDF
        uri = client.upload_pdf("laws", "/path/to/law.pdf")
        # Returns: s3://laws/32024R1781.pdf
        
        # Upload raw JSON/HTML
        uri = client.upload_bytes("news", b'{"title": "..."}', "2024/12/article_hash.json")
        
        # Get download URL
        url = client.get_presigned_url("laws", "32024R1781.pdf")
    """
    
    def __init__(self):
        try:
            from minio import Minio
            self.client = Minio(
                MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=MINIO_SECURE
            )
            log(f"✅ Connected to MinIO at {MINIO_ENDPOINT}")
            self._ensure_buckets()
        except ImportError:
            raise ImportError("minio package not installed. Run: pip install minio")
        except Exception as e:
            log(f"❌ Failed to connect to MinIO: {e}")
            raise
    
    def _ensure_buckets(self) -> None:
        """Create required buckets if they don't exist"""
        for bucket in [BUCKET_LAWS, BUCKET_NEWS, BUCKET_PAPERS]:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                log(f"✅ Created bucket: {bucket}")
            else:
                log(f"📦 Bucket exists: {bucket}")
    
    def list_buckets(self) -> List[str]:
        """List all buckets"""
        return [b.name for b in self.client.list_buckets()]
    
    def upload_pdf(self, bucket: str, file_path):
        """
        Upload a PDF file to MinIO.
        
        Args:
            bucket: Target bucket (laws, papers)
            file_path: Path to PDF file
            
        Returns:
            S3-style URI: s3://bucket/object_name
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        object_name = file_path.name
        
        self.client.fput_object(
            bucket,
            object_name,
            str(file_path),
            content_type="application/pdf"
        )
        
        uri = f"s3://{bucket}/{object_name}"
        log(f"✅ Uploaded: {uri}")
        return uri
    
    def upload_bytes(
        self, 
        bucket: str, 
        data: bytes, 
        object_name: str,
        content_type: str = "application/json"
    ) -> str:
        """
        Upload raw bytes to MinIO.
        
        Args:
            bucket: Target bucket
            data: Raw bytes to upload
            object_name: Object path (e.g., "2024/12/hash.json")
            content_type: MIME type
            
        Returns:
            S3-style URI
        """
        data_stream = io.BytesIO(data)
        data_length = len(data)
        
        self.client.put_object(
            bucket,
            object_name,
            data_stream,
            data_length,
            content_type=content_type
        )
        
        uri = f"s3://{bucket}/{object_name}"
        log(f"✅ Uploaded: {uri}")
        return uri
    
    def upload_json(self, bucket: str, data: Dict[str, Any], object_name: str) -> str:
        """
        Upload JSON data to MinIO.
        
        Args:
            bucket: Target bucket
            data: Dictionary to serialize as JSON
            object_name: Object path
            
        Returns:
            S3-style URI
        """
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        return self.upload_bytes(bucket, json_bytes, object_name, "application/json")
    
    def get_presigned_url(self, bucket: str, object_name: str, expires_hours: int = 24) -> str:
        """
        Get a presigned URL for direct download.
        
        Args:
            bucket: Bucket name
            object_name: Object path
            expires_hours: URL expiration time in hours
            
        Returns:
            Presigned URL string
        """
        from datetime import timedelta
        url = self.client.presigned_get_object(
            bucket,
            object_name,
            expires=timedelta(hours=expires_hours)
        )
        return url
    
    def download_bytes(self, bucket: str, object_name: str) -> bytes:
        """
        Download object as bytes.
        
        Args:
            bucket: Bucket name
            object_name: Object path
            
        Returns:
            Raw bytes
        """
        response = self.client.get_object(bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    
    def download_json(self, bucket: str, object_name: str) -> Dict[str, Any]:
        """
        Download and parse JSON object.
        
        Args:
            bucket: Bucket name
            object_name: Object path
            
        Returns:
            Parsed JSON as dictionary
        """
        data = self.download_bytes(bucket, object_name)
        return json.loads(data.decode('utf-8'))
    
    def list_objects(self, bucket: str, prefix: str = "") -> List[Dict[str, Any]]:
        """
        List objects in a bucket with optional prefix filter.
        
        Args:
            bucket: Bucket name
            prefix: Optional prefix to filter (e.g., "2024/12/")
            
        Returns:
            List of object metadata dicts
        """
        objects = self.client.list_objects(bucket, prefix=prefix, recursive=True)
        return [
            {
                "name": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat() if obj.last_modified else None
            }
            for obj in objects
        ]
    
    def object_exists(self, bucket: str, object_name: str) -> bool:
        """Check if object exists in bucket"""
        try:
            self.client.stat_object(bucket, object_name)
            return True
        except:
            return False
    
    def delete_object(self, bucket: str, object_name: str) -> None:
        """Delete an object from bucket"""
        self.client.remove_object(bucket, object_name)
        log(f"🗑️ Deleted: s3://{bucket}/{object_name}")


# ============ CONVENIENCE FUNCTIONS ============

_client: Optional[MinioClient] = None

def get_minio_client() -> MinioClient:
    """Get singleton MinIO client"""
    global _client
    if _client is None:
        _client = MinioClient()
    return _client


def upload_law_pdf(file_path: Union[str, Path]) -> str:
    """Upload a law PDF and return S3 URI"""
    return get_minio_client().upload_pdf(BUCKET_LAWS, file_path)


def upload_news_raw(article_id: str, raw_text: str) -> str:
    """Upload raw news text and return S3 URI"""
    date_prefix = datetime.now().strftime("%Y/%m")
    object_name = f"{date_prefix}/{article_id}.txt"
    text_bytes = raw_text.encode('utf-8')
    return get_minio_client().upload_bytes(BUCKET_NEWS, text_bytes, object_name, "text/plain")


def upload_paper_pdf(file_path: Union[str, Path]) -> str:
    """Upload a research paper PDF and return S3 URI"""
    return get_minio_client().upload_pdf(BUCKET_PAPERS, file_path)


def upload_bytes(bucket: str, data: bytes, object_name: str, content_type: str = "application/json") -> str:
    """Upload raw bytes and return S3 URI"""
    return get_minio_client().upload_bytes(bucket, data, object_name, content_type)


# ============ TEST ============

if __name__ == "__main__":
    print("Testing MinIO Client...")
    
    try:
        client = MinioClient()
        print(f"Buckets: {client.list_buckets()}")
        
        # Test upload bytes
        test_data = {"test": "Hello MinIO!", "timestamp": datetime.now().isoformat()}
        uri = client.upload_json("news", test_data, "test/test_article.json")
        print(f"Uploaded test: {uri}")
        
        # Test download
        downloaded = client.download_json("news", "test/test_article.json")
        print(f"Downloaded: {downloaded}")
        
        # Cleanup
        client.delete_object("news", "test/test_article.json")
        print("✅ All tests passed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        print("Make sure MinIO is running: docker-compose up -d minio")
