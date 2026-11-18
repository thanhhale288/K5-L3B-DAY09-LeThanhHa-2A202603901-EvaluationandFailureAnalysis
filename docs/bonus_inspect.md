# Bonus: port grader sang Inspect AI và so khớp kết quả

Lệnh (thư mục `src/`, cần `pip install -r requirements-bonus.txt`; không cần API key, `mockllm`):
```bash
python -m lab9.inspect_port --stress
python -m pytest -q -m student -o addopts= -k bonus_inspect
```

## Cách làm
- **Công cụ:** Inspect AI 0.3.291. Mỗi trial `(item, seed)` do đúng shape đó chạy qua `lab9.eval.make_task` (cùng host, cùng ledger), rồi nhét vào một `Sample` của Inspect. Một `solver` rỗng thay cho model (trial đã do MockLLM sinh ra), bốn `@scorer` chấm trial.
- **Bốn grader được port:** `ApprovalBypass`, `RefundAmountCorrect`, `SecretLeak` (ba grader đề yêu cầu) và `ReplyMatchesLedger` (FM-08 của nhóm). Scorer Inspect **viết lại từ định nghĩa metric**, không import `lab9.graders`, nên so sánh có ý nghĩa.
- **So khớp:** từng ô (item, seed, metric) của Inspect so với dòng cùng ô trong report pydantic-evals (`baseline_v1.json`, `eval_fixed.json`, `eval_seeded_regression.json`, hoặc chạy trực tiếp cho shape stress).

## Kết quả (`reports/bonus_inspect_compare.json`)
| Shape | Trial | Ô so sánh | Lệch | Inspect: bypass / leak / amount_correct / reply_ok |
|---|---|---|---|---|
| swarm (baseline v1) | 70 | 280 | 0 | 0 / 0 / 0.986 / 1.0 |
| fixed | 70 | 280 | 0 | 0 / 0 / 1.0 / 1.0 |
| seeded_regression | 70 | 280 | 0 | 0 / 0 / 0.857 / 0.786 |
| stress_leaky_trace (FM-01) | 70 | 280 | 0 | 0 / **1.0** / 1.0 / 1.0 |
| stress_sibling_refund (FM-07) | 70 | 280 | 0 | **0.286** / 0 / 0.714 / 1.0 |
| stress_misreport (FM-08) | 70 | 280 | 0 | 0 / 0 / 1.0 / **0.857** |
| stress_round_down (FM-04) | 70 | 280 | 0 | 0 / 0 / **0.714** / 1.0 |

Tổng 1960 ô, **0 lệch**. Bốn shape stress gài đúng một lỗi để mỗi scorer kích hoạt ít nhất một lần; nếu chỉ so trên shape sạch thì hai bên cùng nói "không lỗi" và phép so sánh vô nghĩa (secret_leak, approval_bypass bằng 0 ở swarm/fixed/seeded_regression).

## Khác biệt giữa hai framework
- **Mô hình dữ liệu:** pydantic-evals gọi grader trên `EvaluatorContext` (đầu ra + nhãn); Inspect gọi scorer trên `TaskState` + `Target`. Ở đây nhãn nằm trong `Sample.metadata` vì Inspect muốn `target` là chuỗi/danh sách.
- **Phải có solver:** Inspect luôn chạy một solver, nên tôi dùng solver rỗng; MockLLM không đi qua Inspect.
- **Cực của điểm số:** Inspect `Score.value` là số/chuỗi, nên tôi dùng 1.0 = "đã lỗi" cho metric an toàn và 1.0 = "đúng" cho metric năng lực, đúng cực như dòng trong report pydantic-evals.
- **Chi phí:** Inspect ghi log `.eval` cho mỗi lần chạy (dùng thư mục tạm); thêm khoảng 3–5 giây khởi động mỗi lần. pydantic-evals nhẹ hơn khi chạy offline thuần.

## Giới hạn
- Chỉ so khớp trên MockLLM và 14 item; không kiểm trên model thật.
- Inspect không nằm trong cổng CI (`requirements-bonus.txt` tách riêng); test bonus tự bỏ qua khi chưa cài `inspect-ai`.
- Scorer viết lại độc lập vẫn do cùng một người viết nên có thể cùng hiểu sai một định nghĩa; hai bên khớp nhau không chứng minh định nghĩa đúng.
