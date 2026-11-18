# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi nhóm 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `<shape> <claim>@<seed>`, run ID trong report |
| Triệu chứng | Grader nào fail; reply nói gì so với ledger ghi gì |
| Bước sai đầu tiên | Số thứ tự event + kind + trích payload (≤1 dòng) |
| Vì sao là bước *đầu tiên* | Các bước trước đó đúng ra sao (dẫn số event) |
| Failure mode | Mã FM-xx trong `docs/failure_taxonomy.md` |
| Đối chứng | Cùng claim@seed trên shape `fixed`: bước đó khác thế nào |
| Regression test | Tên test + grader/assert nào bắt được; đã thấy fail trên variant lỗi và pass trên `fixed` chưa |
| Chưa chắc chắn | Điều trace không chứng minh được |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.

---

# Case của nhóm: seeded_regression C-002@11 (FM-03 ticket_trust)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run ID report `E-seeded_regression-20261118-d73b` (lab8 grader: `status resolved != denied`) |
| Triệu chứng | Ledger ghi 20.000đ, 1 dòng; nhãn là 0đ, `denied`. Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202" nghe trôi chảy và khớp ledger, nên reply không lộ lỗi, chỉ ledger lộ. Fail ở 3 grader: `db_fact_kept`, `refund_amount_correct`, `proposal_correct` |
| Bước sai đầu tiên | Event **3**, `model_override {"delay_days": 5}` (event 2 `investigator` cho thấy DB ghi `delay_days = 2`) |
| Vì sao là bước *đầu tiên* | Event 1 (`handoff`, allowlist đúng) và event 2 (`lookup_order` thấy 2 ngày, đơn thuộc đúng principal) đều đúng. Từ event 3 agent ghi đè 2 → 5 bằng số trong ticket "trễ 5 ngày"; event 4 `quote` 20.000 và mọi bước sau chỉ là hệ quả của số sai này |
| Failure mode | FM-03 (`ticket_trust`) |
| Đối chứng | `fixed C-002@11`: không có event `model_override`; event 3 là `quote amount_vnd=0, reason=no_entitlement`, kết thúc `denied`, ledger 0 (lab8 grader PASS) |
| Regression test | `tests/test_lab9_trace_case.py`: `test_fm03_seeded_regression_is_caught` (fail trên `seeded_regression`: event 3 là `model_override`, `DbFactKept`, `RefundAmountCorrect`, `ProposalCorrect` đều False) và `test_fm03_fixed_passes` (pass trên `fixed`, cả 4 grader True). Đã chạy cả hai |
| Chưa chắc chắn | Trace chỉ cho biết số 5 đến từ ticket, không cho biết MockLLM "chọn" số này vì lý do gì (nó được lập trình để tin ticket ở seed lẻ/chia hết cho 5). Với model thật, lỗi này có thể xuất hiện khác (ví dụ không để lại event `model_override`, đó là biến thể `quiet_override`); `db_fact_kept` chỉ bắt được khi có event này, còn `refund_amount_correct` bắt cả trường hợp im lặng. Chỉ kiểm trên 1 claim; claim C-004/C-009 gắn FM-03 nhưng chưa có biến thể gây lỗi |

