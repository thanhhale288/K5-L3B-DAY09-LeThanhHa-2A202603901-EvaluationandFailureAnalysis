# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (backend replay, 100 mẫu mỗi tiêu chí, FAIL là lớp dương).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** (tp 24, fp 4, fn 6, tn 66) | **Dùng** (được làm judge cho giọng điệu) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** κ và recall (tp 9, fp 0, fn 21, tn 70) | **Loại** |

Tỉ lệ FAIL quan sát được: tone 0.28 → hiệu chỉnh Rogan-Gladen 0.30. helpful 0.09 → hiệu chỉnh **0.30**: judge `helpful` chỉ thấy 9% lỗi trong khi tỉ lệ lỗi thật cỡ 30%.

## Audit thủ công (tone_ok, 10 bất đồng trong tổng số; liệt kê 8)
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha" | PASS | FAIL | Người | Xưng "em"/"nha" suồng sã. Rubric: cấm "nha/nhé", xưng hô phải là "LogiX" – "anh/chị". |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn … rồi nha" | PASS | FAIL | Người | Xưng "bạn" với khách. Thêm "bạn" vào danh sách xưng hô cấm. |
| 3 | H-029 "…không cần nhắn thêm" | PASS | FAIL | Người | Giọng ra lệnh; judge chỉ thấy "Dạ, vui lòng". Rubric: câu ra lệnh/phủ định hướng dẫn khách là FAIL dù có "Dạ". |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ…" | FAIL | PASS | Người | Judge phạt vì "thiếu xin lỗi". Rubric: không bắt buộc xin lỗi khi đã hoàn tiền đúng. |
| 5 | H-051 "…đã refund 90.000đ…" | FAIL | PASS | Người | "refund" được CS LogiX cho phép. Rubric: thuật ngữ chuyên ngành được duyệt không bị phạt. |
| 6 | H-063 "…sẽ hoàn thêm cho anh/chị" | PASS | FAIL | Người | Hứa hẹn ngoài kết quả hệ thống. Cái này nên chuyển sang grader code (so với ledger), không để judge tone. |
| 7 | H-070 "…😂😂" | PASS | FAIL | Người | Emoji không phù hợp. Rubric: emoji cười trong ngữ cảnh khiếu nại là FAIL. |
| 8 | H-081 "đơn O-107 chưa đủ điều kiện…" | FAIL | PASS | **Judge** | Ghi chú CSV: nhãn người sai (đọc nhầm ticket). Sửa nhãn thành FAIL; đây là lỗi nhãn, không phải lỗi judge. |

Còn H-088 (lặp "Dạ, dạ", judge phạt, người cho qua) và H-094 (mất dấu tiếng Việt, người FAIL, judge PASS): cùng nhóm "lỗi hình thức": thêm điều "không dấu/lặp từ" vào rubric.

## Vì sao `helpful` không được làm gate
- κ = 0.375 < 0.6 và recall = 0.30 < 0.7: judge bỏ sót 21/30 lỗi thật (fn = 21) dù precision 1.0 (không báo nhầm).
- 18 trong 21 lỗi người ghi nhận là "sai tiền" (reply nói số khác ledger, ví dụ HL-015 nói 55.000đ, ledger ghi 40.000đ; HL-020 nói 90.000đ, ledger 120.000đ). Judge bị câu trả lời lịch sự, trôi chảy đánh lừa nên cho PASS. Đây chính là **FM-08 (misreport)**: một judge đọc chữ không biết ledger.
- Do đó FM-08 và mọi kiểm tra liên quan đến tiền **phải dùng code** so reply với ledger (M3). Judge chỉ được phép dùng cho `tone_ok`, sau khi sửa rubric theo bảng trên.
- Dùng judge `helpful` làm gate sẽ cho qua gần 70% lỗi, và báo tỉ lệ lỗi 9% trong khi thực tế khoảng 30%.

## Kết luận
- Dùng `tone_ok` (đạt cả ba floor); loại `helpful` (κ và recall dưới sàn), thay bằng grader code cho tiền.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (n=100); không có held-out thật. κ 0.757 chỉ lớn hơn sàn 0.6 một khoảng vừa phải; recall 0.8 với n=30 lỗi thì CI rộng. Cần nhãn người mới trước khi tin con số này cho phiên bản sau.
