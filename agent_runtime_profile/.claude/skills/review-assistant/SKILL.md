---
name: review-assistant
description: Tóm tắt chính xác nội dung đang chờ người dùng duyệt bằng Review Model và trả kết quả bằng tiếng Việt.
---

# Review assistant

Khi một bước sáng tạo đã hoàn tất và workflow đang chờ người dùng duyệt:

1. Xác định đúng `episode` và `review_kind` từ bước đang chờ duyệt.
2. Gọi `create_review_summary` đúng một lần.
3. Trả nguyên ý bản tóm tắt tiếng Việt cho người dùng đọc.

Không tự phê duyệt, từ chối, sửa nội dung hoặc gọi thêm LLM/subagent. Nếu tool báo nội dung không còn chờ duyệt, hãy làm mới trạng thái workflow thay vì gọi lại bằng dữ liệu cũ.
