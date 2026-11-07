"""Tạo lại data/index.json từ các file trong data/daily và data/weekly.

Chạy sau khi thêm/xóa/sửa tay file bản tin:  python scripts/build_index.py
"""
from common import build_index

if __name__ == "__main__":
    idx = build_index()
    print(f"index.json: {len(idx['daily'])} bản tin ngày, {len(idx['weekly'])} bản tổng hợp tuần")
