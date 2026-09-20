"""데모에 낼 가상 기록 (원티드 출품용).

지어낸 팀이 지어낸 커머스 백엔드를 4주 만든 기록이다. 사람·회사·주소·숫자 전부 가짜다.
tools/demo_seed.py 가 이 데이터를 제품 코어 API 로 쌓아 데모용 sqlite 를 만든다.

쓰는 법 — THREADS 의 steps 는 시간순 목록이고 한 항목이 한 가지 일을 한다:
    {"t": 시각, "turn": {"ev": 원문, "note": "제목\\n\\n본문", "kind": change|verified|finding|thought}}
    {"t": 시각, "decide": {"s": 문장, "why": 이유, "by": user|agent, "scope": thread|repo}}
    {"t": 시각, "constrain": {...}}   {"t": 시각, "rule_out": {"s": 가설, "scope": 범위}}
    {"t": 시각, "define": {"term": 이름, "meaning": 뜻}}
    {"t": 시각, "declare": {"kind": focus|open|next, "s": 문장, "owner": next 에 필수}}
    {"t": 시각, "commit": {"m": 메시지, "files": {경로: 내용}}}
    {"t": 시각, "close": 결과 한 줄}
기록의 첫 줄은 제목이다: 60자 이내, ' — ' 로 잇지 않는다(확장 52호). 증거와 관찰은 원문 그대로라 예외.
"""

BACKEND = "onstore/backend"
GATEWAY = "onstore/pay-gateway"

TOPICS = [
    {
        "name": "결제 대행사 교체",
        "at": "2026-08-18 09:20",
        "summary": "수수료와 부분취소 때문에 결제 대행사를 페이레일로 옮긴다. 후보 비교(8/18) → 샌드박스 연동(8/20) → 실결제 전환 점검(9/8) → 점검에서 나온 구멍 둘(9/10~).",
        "conclusion": "페이레일로 옮겼고 실결제 전환만 남았다\n\n부분취소를 API 로 열어 주는 곳이 둘뿐이었고 정산이 D+2 라 페이레일로 정했다. 샌드박스는 결제·부분취소·웹훅까지 통과했다. 남은 것은 실결제 키로 한 건을 태우는 일이고, 그건 대표 승인이 있어야 한다. 점검에서 나온 둘(어댑터 밖 호출, 환불액 1원 차이)은 전환 전에 닫는다.",
        "threads": ["C1", "C2", "C3", "C4", "C5"],
    },
    {
        "name": "주문·결제 장애",
        "at": "2026-08-26 08:40",
        "summary": "운영에서 터진 것을 그때그때 잡은 기록. 취소 실패(8/26) → 웹훅 중복(9/1) → 재고 음수(9/10).",
        "conclusion": "바깥을 기다리지 않게 고치는 것이 셋 다의 답이었다\n\n취소 실패도 웹훅 중복도 결국 '남의 시스템이 늦거나 두 번 오는 것'을 우리 요청 안에서 감당하려다 났다. 접수와 확정을 나누고 멱등키를 두자 사라졌다. 재고 음수만 아직 열려 있다.",
        "threads": ["A1", "A2", "A3"],
    },
    {
        "name": "쿠폰·할인 정책",
        "at": "2026-09-03 09:10",
        "summary": "쿠폰을 겹쳐 쓰게 할지 정하고(9/3), 흩어진 할인 계산을 주문 서비스로 모으고(9/5), 무료배송 기준을 올렸다(9/11).",
        "conclusion": "정책을 먼저 정하고 계산을 한 곳으로 모았다\n\n겹쳐 쓰기는 막고 가장 싼 값 하나만 적용한다. 그 뒤 장바구니·주문·정산 세 곳에 흩어져 있던 계산을 주문 서비스 한 곳으로 모아 세 곳이 서로 다른 값을 내던 문제가 사라졌다.",
        "threads": ["B1", "B2", "B3", "B4"],
    },
]

THREADS: dict[str, dict] = {}

# ── 주제: 결제 대행사 교체 ────────────────────────────────────────────────

THREADS["C1"] = {
    "repo": GATEWAY,
    "start": "2026-08-18 09:30",
    "focus": """결제 대행사 후보 셋 중 하나를 고른다

지금 쓰는 곳은 수수료가 3.4% 이고 부분취소를 콜센터로만 받는다. 월 거래가 늘면서 둘 다 아프다.
페이레일·머니브리지·스퀘어페이 셋을 놓고 수수료·정산 주기·부분취소 API·웹훅 재전송까지 보고 고른다.""",
    "steps": [
        {"t": "2026-08-18 10:40", "turn": {
            "kind": "finding",
            "ev": """세 곳 영업 자료와 개발 문서에서 뽑은 표 (2026-08-18)

                    페이레일        머니브리지      스퀘어페이
수수료(카드)        2.9%            2.7%            3.1%
정산 주기           D+2             D+5             D+3
부분취소 API        O (금액 지정)   X (전액만)      O (건별)
웹훅 재전송         5회/24시간      3회/1시간       없음
최소 계약 기간      없음            12개월          6개월
테스트 환경         샌드박스 상시   신청 후 3일     샌드박스 상시""",
            "note": """부분취소를 API 로 여는 곳은 둘뿐이다

수수료는 머니브리지가 가장 싸지만 부분취소가 전액취소만 되고 계약이 12개월로 묶인다. 우리 취소 중 62% 가 부분취소라 이건 못 쓴다. 페이레일과 스퀘어페이 둘로 좁힌다."""}},
        {"t": "2026-08-18 14:20", "turn": {
            "kind": "finding",
            "ev": """$ for h in payrail squarepay; do echo "== $h"; for i in 1 2 3; do curl -s -o /dev/null -w "%{time_total}\\n" https://sandbox.$h.test/v1/payments -X POST -d @sample.json; done; done
== payrail
0.412
0.388
0.401
== squarepay
0.455
0.470
0.462

$ curl -s -X POST https://sandbox.squarepay.test/v1/payments/PAY-9f21/cancel -d '{"amount": 12000}'
{"error":"PARTIAL_NOT_SUPPORTED_FOR_INSTALLMENT","message":"할부 결제는 부분취소가 지원되지 않습니다"}""",
            "note": """스퀘어페이는 할부 건의 부분취소가 안 된다

결제 생성 응답시간은 둘이 0.4초대로 비슷하다. 갈리는 것은 취소다. 스퀘어페이는 할부 결제에 부분취소를 안 받는데, 우리 거래의 28% 가 할부다. 페이레일만 남는다."""}},
        {"t": "2026-08-19 10:00", "decide": {
            "s": "결제 대행사는 페이레일로 간다",
            "why": "부분취소와 정산 주기가 갈랐다\n\n수수료는 머니브리지가 0.2%p 싸지만 부분취소가 전액만 되고 12개월 계약이다. 스퀘어페이는 할부 부분취소가 막힌다(거래의 28%). 페이레일은 셋 중 유일하게 금액 지정 부분취소를 열고 정산이 D+2 로 가장 빠르다.",
            "by": "user"}},
        {"t": "2026-08-19 10:20", "constrain": {
            "s": "대행사를 부르는 코드는 어댑터 뒤에만 둔다",
            "why": "이번이 두 번째 교체다\n\n작년에 한 번 옮길 때 결제 호출이 주문·정산·관리자 세 곳에 흩어져 있어 3주가 걸렸다. 이번에는 PaymentGateway 인터페이스 하나만 서비스 코드가 알게 한다.",
            "by": "user", "scope": "repo"}},
        {"t": "2026-08-19 10:35", "close": """페이레일로 정했다, 부분취소와 정산이 갈랐다

셋 중 금액 지정 부분취소를 여는 곳은 페이레일뿐이었다(스퀘어페이는 할부 건이 막히고 우리 거래의 28% 가 할부다). 정산도 D+2 로 가장 빠르다. 다음은 샌드박스 연동."""},
    ],
}

THREADS["C2"] = {
    "repo": GATEWAY,
    "start": "2026-08-20 09:40",
    "focus": """페이레일 샌드박스로 결제와 부분취소를 통과시킨다

앞 스레드에서 페이레일로 정했다. 결제 생성 → 승인 → 부분취소 → 웹훅 수신까지 샌드박스에서 한 바퀴 돌린다.
대행사 호출을 어댑터 뒤에 둔다는 저장소 제약을 지키며 PaymentGateway 구현체로 만든다.""",
    "steps": [
        {"t": "2026-08-20 11:15", "turn": {
            "kind": "finding",
            "ev": """$ python -m gateway.payrail.smoke
POST https://sandbox.payrail.test/v1/payments
< HTTP/1.1 401 Unauthorized
< {"code":"SIGNATURE_MISMATCH","message":"서명이 일치하지 않습니다","hint":"서명 문자열 생성 규칙을 확인하세요"}

보낸 서명 문자열:
  amount=15000&currency=KRW&merchant=onstore&order_id=ORD-20260820-001&timestamp=1787...""",
            "note": """서명이 안 맞아 401 이다, 규칙 대조가 필요하다

키와 시크릿은 맞게 들어갔다(문서의 테스트 키와 같다). 서명 문자열을 만드는 규칙이 다른 것으로 보인다. 문서의 예제와 우리가 만든 문자열을 한 글자씩 대조한다."""}},
        {"t": "2026-08-20 13:50", "turn": {
            "kind": "finding",
            "ev": """페이레일 문서 4.2 서명 생성 (발췌)

  1. 요청 파라미터를 키 이름의 **UTF-8 바이트 순**으로 정렬한다
  2. key=value 를 & 로 잇는다 (값은 URL 인코딩하지 않는다)
  3. 끝에 &secret=<시크릿> 을 붙인다
  4. SHA-256 해시의 소문자 16진수

우리 코드(gateway/payrail/sign.py:18):
  items = sorted(params.items())          # 파이썬 기본 정렬
  body = "&".join(f"{k}={quote(v)}" for k, v in items)   # ← URL 인코딩하고 있다""",
            "note": """값을 URL 인코딩한 것이 원인이다

문서는 값을 인코딩하지 말라고 하는데 우리는 quote() 를 통과시켰다. 주문번호에 하이픈만 있어 대부분 같아 보였지만 상품명이 한글이라 거기서 갈렸다. 인코딩을 빼면 맞는다."""}},
        {"t": "2026-08-20 14:30", "commit": {
            "m": "결제 서명 생성에서 값 URL 인코딩을 뺀다",
            "files": {"gateway/payrail/sign.py": "# 문서 4.2: 값은 인코딩하지 않는다\nbody = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))\n"}}},
        {"t": "2026-08-20 14:45", "turn": {
            "kind": "verified",
            "ev": """$ python -m gateway.payrail.smoke
POST https://sandbox.payrail.test/v1/payments
< HTTP/1.1 200 OK
< {"payment_id":"PAY-SBX-4471","status":"APPROVED","amount":15000,"approved_at":"2026-08-20T14:44:52+09:00"}""",
            "note": """샌드박스 결제가 승인까지 갔다

인코딩을 빼자 첫 시도에 200 이다. 결제 생성·승인은 끝. 다음은 부분취소."""}},
        {"t": "2026-08-21 10:20", "turn": {
            "kind": "finding",
            "ev": """$ curl -s -X POST https://sandbox.payrail.test/v1/payments/PAY-SBX-4471/cancel \\
    -H "X-Signature: ..." -d '{"amount": 5000}'
{"code":"CANCEL_AMOUNT_REQUIRED","message":"cancel_amount 필드가 필요합니다"}

문서 6.1 부분취소 요청:
  cancel_amount   정수   필수   취소할 금액(원)
  reason          문자열 선택   취소 사유""",
            "note": """부분취소 필드 이름이 amount 가 아니라 cancel_amount 다

결제 생성은 amount, 취소는 cancel_amount 로 이름이 다르다. 어댑터에서 이름을 맞춰 준다."""}},
        {"t": "2026-08-21 10:50", "commit": {
            "m": "부분취소 파라미터를 cancel_amount 로 맞춘다",
            "files": {"gateway/payrail/client.py": "def cancel(self, payment_id, amount, reason=None):\n    return self._post(f'/v1/payments/{payment_id}/cancel',\n                      {'cancel_amount': amount, 'reason': reason})\n"}}},
        {"t": "2026-08-21 11:10", "turn": {
            "kind": "verified",
            "ev": """$ python -m gateway.payrail.smoke --full
결제 생성 .......... 200 PAY-SBX-4472 APPROVED 15000원
부분취소 5000원 .... 200 CANCEL-SBX-881 PARTIAL_CANCELED 잔액 10000원
부분취소 10000원 ... 200 CANCEL-SBX-882 CANCELED 잔액 0원
웹훅 수신 .......... 3건 (payment.approved, payment.partial_canceled, payment.canceled)
소요 12.4초""",
            "note": """결제·부분취소·웹훅이 한 바퀴 돌았다

부분취소를 두 번 나눠 걸어 잔액이 0 이 되는 것까지 확인했다. 웹훅도 세 건 다 들어왔다. 샌드박스 범위는 여기서 끝."""}},
        {"t": "2026-08-24 15:30", "close": """샌드박스에서 결제·부분취소·웹훅이 통과했다

막힌 곳은 둘이었다. 서명 문자열의 값을 URL 인코딩한 것(문서는 하지 말라고 한다), 취소 금액 필드가 cancel_amount 인 것. 둘 다 어댑터 안에서 끝났고 서비스 코드는 PaymentGateway 만 안다. 실결제 전환은 따로 연다."""},
    ],
}

THREADS["C3"] = {
    "repo": GATEWAY,
    "start": "2026-09-08 10:00",
    "focus": """실결제 키로 첫 한 건을 태우기 전에 점검한다

샌드박스는 앞 스레드에서 통과했다. 실키는 한 번 쓰면 진짜 돈이 움직이므로, 키 보관·정산 계좌·취소 한도·
장애 시 되돌리는 길을 먼저 확인하고 대표 승인을 받는다.""",
    "steps": [
        {"t": "2026-09-08 11:30", "turn": {
            "kind": "finding",
            "ev": """점검표 (2026-09-08)

[O] 실키를 코드·저장소에 두지 않는다        비밀 저장소에만, 배포 때 주입
[O] 정산 계좌 확인                          법인 계좌로 등록 완료, 테스트 입금 1원 확인
[O] 웹훅 수신 주소 https 인증서             유효, 2027-03 만료
[ ] 일일 취소 한도                          미확인 — 계약서에 없다
[ ] 장애 시 이전 대행사로 되돌리는 길        어댑터는 있으나 실제로 돌려 본 적 없다
[O] 결제 실패 시 주문 상태                  pending 으로 남고 30분 뒤 자동 정리""",
            "note": """두 가지가 비어 있다, 취소 한도와 되돌리는 길

키·계좌·인증서는 됐다. 남은 것은 (1) 일일 취소 한도가 계약서에 없어 영업 담당에게 물어야 하고, (2) 장애 시 이전 대행사로 되돌리는 경로를 실제로 한 번 돌려 봐야 한다."""}},
        {"t": "2026-09-09 14:00", "turn": {
            "kind": "verified",
            "ev": """$ PAYMENT_GATEWAY=legacy python -m gateway.smoke --full
결제 생성 .......... 200 LEG-7781 APPROVED 15000원
전액취소 ........... 200 LEG-CANCEL-331 CANCELED
소요 8.9초

$ PAYMENT_GATEWAY=payrail python -m gateway.smoke --full
결제 생성 .......... 200 PAY-SBX-4490 APPROVED 15000원
부분취소 5000원 .... 200 CANCEL-SBX-903 PARTIAL_CANCELED
소요 12.1초""",
            "note": """환경변수 한 줄로 이전 대행사로 돌아간다

되돌리는 길은 확인됐다. PAYMENT_GATEWAY 를 legacy 로 두면 예전 경로가 그대로 산다. 배포 없이 환경변수만 바꾸면 되므로 장애 때 몇 분이면 된다. 남은 것은 취소 한도 하나."""}},
        {"t": "2026-09-09 14:20", "declare": {
            "kind": "open",
            "s": """일일 취소 한도를 모른 채로는 못 켠다

영업 담당에게 9월 8일에 물었고 아직 답이 없다. 한도가 우리 하루 취소액(평균 240만원, 최대 810만원)보다 낮으면 월말에 취소가 막힌다. 답이 오면 계약서 부속에 넣고 켠다.""",
            "by": "user"}},
        {"t": "2026-09-09 14:25", "declare": {
            "kind": "next",
            "s": """대표 승인과 취소 한도 답을 받고 실키를 켠다

(a) 영업 담당 답으로 일일 취소 한도 확인. (b) 대표 승인. (c) 실키를 비밀 저장소에 넣고 PAYMENT_GATEWAY=payrail 로 바꾼다. (d) 소액 한 건을 실제로 결제하고 부분취소까지 확인한 뒤 공지.""",
            "owner": "user", "by": "user"}},
    ],
}

# ── 주제: 주문·결제 장애 ──────────────────────────────────────────────────

THREADS["A1"] = {
    "repo": BACKEND,
    "start": "2026-08-26 09:10",
    "focus": """주문 취소가 하루 수십 건 실패하는 원인을 찾는다

어제부터 고객센터에 "취소 버튼을 눌렀는데 취소가 안 됐다"는 문의가 늘었다. 화면에는 실패라고 뜨는데
결제는 취소된 건도 있다고 한다. 어디서 갈리는지 찾아 고친다.""",
    "steps": [
        {"t": "2026-08-26 09:40", "turn": {
            "kind": "finding",
            "ev": """Sentry: OrderCancelTimeout (최근 24시간 47건)

Traceback (most recent call last):
  File "orders/service.py", line 212, in cancel_order
    result = gateway.cancel(order.payment_id, amount=order.total)
  File "gateway/payrail/client.py", line 88, in cancel
    return self._post(f"/v1/payments/{payment_id}/cancel", {...})
  File "gateway/http.py", line 41, in _post
    resp = self.session.post(url, json=body, timeout=30)
requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.payrail.test', port=443):
    Read timed out. (read timeout=30)

발생 분포(시각):
  11:00-12:00  14건
  12:00-13:00   9건
  18:00-19:00  17건
  19:00-20:00   7건""",
            "note": """취소 실패는 전부 대행사 취소 호출의 30초 타임아웃이다

47건이 전부 같은 예외다. 우리 코드가 던진 것이 아니라 대행사 응답을 기다리다 30초에 끊긴 것이다. 점심과 저녁 피크에 몰려 있다."""}},
        {"t": "2026-08-26 11:20", "turn": {
            "kind": "finding",
            "ev": """$ grep 'payrail.cancel' /var/log/app/api-*.log | awk '{print $NF}' | sort -n | \\
    awk '{a[NR]=$1} END {print "n="NR, "p50="a[int(NR*0.5)], "p95="a[int(NR*0.95)], "p99="a[int(NR*0.99)], "max="a[NR]}'
n=2841 p50=1.31 p95=18.70 p99=41.20 max=63.80

$ grep 'payrail.payment.create' /var/log/app/api-*.log | awk '{print $NF}' | sort -n | \\
    awk '{a[NR]=$1} END {print "n="NR, "p50="a[int(NR*0.5)], "p95="a[int(NR*0.95)], "p99="a[int(NR*0.99)]}'
n=19204 p50=0.39 p95=0.91 p99=1.44""",
            "note": """취소 API 만 p99 가 41초다, 결제 생성은 1.4초다

같은 대행사인데 결제 생성은 p99 가 1.4초이고 취소만 41초다. 우리 타임아웃 30초가 p99 와 p95 사이에 걸려 있어 피크 때 잘린다. 타임아웃을 늘리는 것은 답이 아니다 — 사용자가 60초를 기다리게 된다."""}},
        {"t": "2026-08-26 11:40", "rule_out": {
            "s": "우리 쪽 네트워크나 커넥션 풀이 좁아 취소가 느리다",
            "scope": "취소 경로(orders.cancel_order → gateway.cancel), 2026-08-26 기준"}},
        {"t": "2026-08-26 14:10", "turn": {
            "kind": "finding",
            "ev": """sql> SELECT status, count(*) FROM orders
     WHERE updated_at > now() - interval '24 hours' GROUP BY status ORDER BY 2 DESC;
 status      | count
-------------+-------
 paid        |  8812
 delivered   |  3190
 canceled    |   402
 cancelling  |    17     <-- 여기서 멈춰 있다
 pending     |     9

sql> SELECT id, payment_id, updated_at FROM orders WHERE status = 'cancelling' ORDER BY updated_at LIMIT 5;
 id       | payment_id    | updated_at
----------+---------------+---------------------
 ORD-8841 | PAY-33120     | 2026-08-25 18:41:02
 ORD-8853 | PAY-33147     | 2026-08-25 18:44:55
 ORD-8901 | PAY-33298     | 2026-08-25 19:02:13
 ORD-9012 | PAY-33511     | 2026-08-26 11:38:40
 ORD-9033 | PAY-33559     | 2026-08-26 12:05:27

sql> -- 대행사 쪽에서는?
     -- PAY-33120, PAY-33147 조회 결과: 둘 다 CANCELED (취소는 됐다)""",
            "note": """돈은 취소됐는데 주문만 cancelling 에 갇혔다

17건이 중간 상태에 멈춰 있다. 확인해 보니 대행사 쪽에서는 취소가 성공했다. 타임아웃으로 끊긴 뒤 우리가 상태를 되돌리지도 확정하지도 못해 주문만 남은 것이다. 고객은 실패 화면을 보고 돈은 돌려받은 상태다."""}},
        {"t": "2026-08-26 15:00", "decide": {
            "s": "취소는 접수와 확정 두 단계로 나눈다",
            "why": "바깥이 늦는 것을 사용자가 기다리게 두지 않는다\n\n취소 API 의 p99 가 41초라 어떤 타임아웃을 잡아도 피크에는 잘린다. 요청에서는 cancelling 으로 접수만 하고 즉시 응답한다. 실제 대행사 호출은 작업 큐가 맡고, 결과가 오면 canceled 로 확정한다. 웹훅이 먼저 와도 같은 자리에서 확정된다.",
            "by": "user"}},
        {"t": "2026-08-26 15:15", "constrain": {
            "s": "외부 결제 호출을 사용자 요청 안에서 기다리지 않는다",
            "why": "이번 장애의 뿌리다\n\n대행사 응답시간은 우리가 못 정한다. 요청-응답 안에서 기다리면 그 시간이 곧 사용자 대기이고 타임아웃은 중간 상태를 남긴다. 결제 생성처럼 빠른 것도 언제 느려질지 모르므로 같은 규칙을 쓴다.",
            "by": "user", "scope": "repo"}},
        {"t": "2026-08-27 11:30", "commit": {
            "m": "취소를 접수·확정 두 단계로 나눈다",
            "files": {
                "orders/service.py": "def cancel_order(order_id):\n    order = repo.get(order_id)\n    order.mark_cancelling()      # 접수만 하고 즉시 응답\n    queue.enqueue('confirm_cancel', order_id=order_id)\n    return order\n",
                "orders/workers.py": "def confirm_cancel(order_id):\n    order = repo.get(order_id)\n    result = gateway.cancel(order.payment_id, amount=order.total)\n    order.mark_canceled(result.cancel_id)\n"}}},
        {"t": "2026-08-27 16:20", "commit": {
            "m": "cancelling 에 멈춘 주문을 되살리는 정리 작업 추가",
            "files": {"orders/recovery.py": "# 10분 넘게 cancelling 인 주문은 대행사에 상태를 물어 확정하거나 되돌린다\ndef sweep_stuck_cancelling():\n    for order in repo.stuck('cancelling', minutes=10):\n        state = gateway.get(order.payment_id).status\n        order.mark_canceled() if state == 'CANCELED' else order.mark_paid()\n"}}},
        {"t": "2026-08-28 10:40", "turn": {
            "kind": "verified",
            "ev": """$ python -m tools.loadtest --scenario cancel --rps 20 --duration 300
요청 6000건 · 성공 6000 · 실패 0
응답시간 p50=0.08 p95=0.14 p99=0.21 (접수 응답)
확정까지 p50=1.4 p95=19.2 p99=44.8 (작업 큐)

$ python -m orders.recovery --once
cancelling 17건 검사: 확정 17 · 되돌림 0

sql> SELECT status, count(*) FROM orders WHERE status = 'cancelling';
 count
-------
     0""",
            "note": """취소 실패가 0 이 됐고 갇힌 17건도 풀렸다

사용자가 보는 응답은 p99 가 0.21초다. 대행사가 44초를 써도 그것은 작업 큐 안에서 일어나고 화면은 이미 '취소 접수됨'을 보여 준다. 멈춰 있던 17건은 정리 작업이 전부 취소로 확정했다."""}},
        {"t": "2026-08-28 10:55", "close": """취소를 접수·확정으로 나눠 실패가 0 이 됐다

원인은 대행사 취소 API 의 p99 가 41초인데 우리 타임아웃이 30초였던 것이다. 끊기면 주문이 cancelling 에 갇히고 돈만 돌아가 고객은 실패 화면을 봤다(17건). 요청에서는 접수만 하고 확정은 작업 큐로 옮겼다. 사용자 응답 p99 는 0.21초, 갇힌 건은 정리 작업이 푼다."""},
    ],
}

THREADS["A2"] = {
    "repo": BACKEND,
    "start": "2026-09-01 13:20",
    "focus": """결제 웹훅이 같은 건을 두 번 보내 적립금이 두 배가 된다

9월 1일 오전에 적립금이 두 번 쌓인 주문 6건이 발견됐다. 웹훅이 중복으로 온 것으로 보이는데,
어디까지 두 번 일어나는지 확인하고 막는다.""",
    "steps": [
        {"t": "2026-09-01 14:05", "turn": {
            "kind": "finding",
            "ev": """$ grep 'webhook.received' /var/log/app/webhook-2026-09-01.log | grep PAY-41288
2026-09-01 09:12:03 webhook.received event=payment.approved payment_id=PAY-41288 delivery_id=whd_8813
2026-09-01 09:12:34 webhook.received event=payment.approved payment_id=PAY-41288 delivery_id=whd_8819

$ grep 'webhook.handled' /var/log/app/webhook-2026-09-01.log | grep PAY-41288
2026-09-01 09:12:04 webhook.handled  payment_id=PAY-41288 points_awarded=1500
2026-09-01 09:12:35 webhook.handled  payment_id=PAY-41288 points_awarded=1500

페이레일 문서 7.3:
  웹훅은 최소 1회 전달을 보장합니다(at-least-once). 2초 안에 200 을 받지 못하면 재전송합니다.
  같은 이벤트가 여러 번 전달될 수 있으므로 delivery_id 로 멱등 처리하십시오.""",
            "note": """31초 간격의 재전송이고 문서가 미리 경고한 것이다

같은 payment_id 에 delivery_id 가 다른 두 건이 왔다. 문서에 최소 1회 전달이라고 적혀 있고 멱등 처리를 하라고 되어 있는데 우리가 안 했다. 첫 응답이 2초를 넘겨 재전송이 걸린 것으로 보인다."""}},
        {"t": "2026-09-01 15:30", "turn": {
            "kind": "finding",
            "ev": """$ grep 'webhook.received\\|webhook.handled' /var/log/app/webhook-2026-09-01.log | \\
    awk '/received/{t[$6]=$1" "$2} /handled/{print $4, $1" "$2}' | head -3
payment_id=PAY-41288 2026-09-01 09:12:04
payment_id=PAY-41290 2026-09-01 09:12:11
payment_id=PAY-41301 2026-09-01 09:13:02

$ grep 'webhook.duration' /var/log/app/webhook-2026-09-01.log | awk '{print $NF}' | sort -n | tail -5
2.81
3.02
3.44
4.10
5.21

웹훅 처리 안에서 하는 일:
  1. 결제 상태 갱신        0.02초
  2. 적립금 지급           0.04초
  3. 주문 확정 메일 발송   2.9초  <-- 외부 메일 API 를 기다린다""",
            "note": """메일 발송을 웹훅 안에서 기다려 2초를 넘긴다

처리 시간의 대부분이 메일 API 다. 2초 안에 200 을 못 주니 대행사가 재전송하고, 멱등 처리가 없어 적립금이 두 번 들어간다. 취소 장애 때 세운 저장소 제약(바깥을 요청 안에서 기다리지 않는다)이 여기에도 그대로 걸린다."""}},
        {"t": "2026-09-01 15:50", "decide": {
            "s": "웹훅은 delivery_id 로 멱등 처리하고 즉시 200 을 준다",
            "why": "재전송은 대행사의 정상 동작이다\n\n최소 1회 전달이므로 중복은 막을 수 없고 받는 쪽이 견뎌야 한다. delivery_id 를 유니크 키로 저장해 두 번째는 조용히 버린다. 메일 같은 느린 일은 작업 큐로 밀어 응답을 2초 안에 끝낸다(취소 장애 때 세운 저장소 제약과 같은 이유).",
            "by": "user"}},
        {"t": "2026-09-02 10:15", "commit": {
            "m": "웹훅에 delivery_id 멱등키를 두고 메일을 큐로 옮긴다",
            "files": {
                "webhooks/receiver.py": "def receive(payload):\n    if not seen.add_if_absent(payload['delivery_id']):\n        return 200            # 두 번째부터는 조용히 버린다\n    apply_payment_state(payload)\n    queue.enqueue('send_order_mail', order_id=payload['order_id'])\n    return 200\n",
                "webhooks/models.py": "class WebhookDelivery(Model):\n    delivery_id = CharField(unique=True)   # 유니크 제약이 멱등을 보장한다\n    received_at = DateTimeField()\n"}}},
        {"t": "2026-09-02 14:40", "turn": {
            "kind": "verified",
            "ev": """$ python -m tools.webhook_replay --file samples/duplicated.json --times 3
전송 3회 (같은 delivery_id whd_9001)
  1회차 200  처리됨 (적립금 1500)
  2회차 200  중복으로 버림
  3회차 200  중복으로 버림
적립금 합계: 1500

$ grep 'webhook.duration' /var/log/app/webhook-2026-09-02.log | awk '{print $NF}' | sort -n | tail -3
0.09
0.11
0.14""",
            "note": """중복을 세 번 보내도 적립은 한 번이고 응답은 0.14초다

멱등키가 걸렸고 메일이 빠지면서 처리 시간이 0.14초로 떨어졌다. 2초 재전송 기준에서 한참 멀어졌으니 재전송 자체도 거의 안 생긴다."""}},
        {"t": "2026-09-02 15:00", "close": """delivery_id 멱등키로 웹훅 중복 적립이 사라졌다

대행사 웹훅은 최소 1회 전달이라 재전송이 정상이다. 우리가 2초 안에 200 을 못 준 이유는 메일 API 를 웹훅 안에서 기다린 것(2.9초)이었다. 멱등키를 두고 메일을 큐로 옮겨 처리 시간이 0.14초가 됐다. 잘못 지급된 6건은 운영팀이 회수했다."""},
    ],
}

THREADS["A3"] = {
    "repo": BACKEND,
    "start": "2026-09-10 09:30",
    "focus": """재고가 음수로 내려간 주문 3건의 경로를 찾는다

9월 10일 아침 재고 점검에서 수량이 -1, -2, -1 인 상품 셋이 나왔다. 품절 상품이 팔린 것이므로
주문을 막지 못한 경로가 어딘가에 있다. 어디서 동시에 들어왔는지 확인한다.""",
    "steps": [
        {"t": "2026-09-10 10:40", "turn": {
            "kind": "finding",
            "ev": """sql> SELECT product_id, stock FROM products WHERE stock < 0;
 product_id | stock
------------+-------
 SKU-2288   |    -1
 SKU-3401   |    -2
 SKU-3407   |    -1

sql> SELECT o.id, o.created_at, i.product_id FROM orders o JOIN order_items i ON i.order_id = o.id
     WHERE i.product_id IN ('SKU-2288','SKU-3401','SKU-3407') AND o.created_at > '2026-09-09'
     ORDER BY o.created_at;
 id       | created_at              | product_id
----------+-------------------------+------------
 ORD-9881 | 2026-09-09 20:00:01.204 | SKU-3401
 ORD-9882 | 2026-09-09 20:00:01.219 | SKU-3401
 ORD-9883 | 2026-09-09 20:00:01.402 | SKU-3401
 ORD-9885 | 2026-09-09 20:00:02.118 | SKU-2288
 ORD-9886 | 2026-09-09 20:00:02.140 | SKU-2288
 ORD-9890 | 2026-09-09 20:00:03.901 | SKU-3407
 ORD-9891 | 2026-09-09 20:00:03.933 | SKU-3407""",
            "note": """20시 타임세일 첫 3초에 같은 상품이 겹쳐 들어왔다

세 상품 모두 9월 9일 20시 정각 직후 몇십 밀리초 간격으로 주문이 겹쳤다. 타임세일 시작 시각이다. 재고 차감이 동시 요청을 막지 못하는 것으로 보인다."""}},
        {"t": "2026-09-10 14:20", "turn": {
            "kind": "finding",
            "ev": """inventory/service.py:41

def reserve(product_id, qty):
    product = Product.objects.get(id=product_id)     # 1) 읽고
    if product.stock < qty:                          # 2) 비교하고
        raise OutOfStock(product_id)
    product.stock -= qty                             # 3) 빼서
    product.save()                                   # 4) 쓴다
    return True

$ grep -n "reserve(" -r orders/ | head
orders/service.py:88:        inventory.reserve(item.product_id, item.qty)

$ psql -c "SHOW transaction_isolation;"
 transaction_isolation
-----------------------
 read committed""",
            "note": """읽고 쓰는 사이가 잠겨 있지 않다, 전형적인 경쟁 상태다

재고를 읽어 비교한 뒤 빼서 저장하는데 그 사이에 잠금이 없다. 격리 수준도 read committed 라 두 요청이 같은 값을 읽고 각자 하나씩 뺀다. 재고 1개에 요청 2개가 들어오면 둘 다 통과하고 -1 이 된다."""}},
        {"t": "2026-09-10 14:35", "declare": {
            "kind": "open",
            "s": """막는 방법 셋 중 무엇을 쓸지 안 정했다

(1) SELECT FOR UPDATE 로 행을 잠근다: 확실하지만 인기 상품에 요청이 몰리면 대기가 길어진다. (2) 조건부 UPDATE 한 문장(stock >= qty 일 때만 빼기): 잠금 없이 원자적이고 빠르다. (3) 재고를 따로 두고 미리 예약: 크지만 타임세일에는 이것이 맞을 수 있다. 세일 트래픽 규모를 보고 정한다.""",
            "by": "agent"}},
        {"t": "2026-09-10 14:45", "declare": {
            "kind": "next",
            "s": """세일 트래픽을 재고 방식 셋과 맞춰 보고 하나를 고른다

(a) 9/9 세일의 초당 요청 수와 상품별 집중도를 뽑는다. (b) 조건부 UPDATE 로 같은 부하를 재현해 음수가 나오는지 본다. (c) 결과를 보고 셋 중 하나를 정한 뒤 음수 재고 3건을 손으로 바로잡는다.""",
            "owner": "claude", "by": "user"}},
    ],
}

# ── 주제: 쿠폰·할인 정책 ──────────────────────────────────────────────────

THREADS["B1"] = {
    "repo": BACKEND,
    "start": "2026-09-03 09:30",
    "focus": """쿠폰을 겹쳐 쓰게 할 것인가를 정한다

마케팅이 추석 쿠폰을 준비하면서 "생일 쿠폰과 같이 쓸 수 있나"를 물었다. 지금 코드는 막지도 열지도
않은 채 우연히 겹쳐진다. 정책을 정하고 코드가 그 정책을 강제하게 만든다.""",
    "steps": [
        {"t": "2026-09-03 10:50", "turn": {
            "kind": "finding",
            "ev": """sql> SELECT cnt, count(*) AS orders, round(avg(discount)) AS avg_discount FROM (
       SELECT order_id, count(*) AS cnt, sum(amount) AS discount
       FROM order_coupons GROUP BY order_id) t GROUP BY cnt ORDER BY cnt;
 cnt | orders | avg_discount
-----+--------+--------------
   1 |  22841 |         3200
   2 |    918 |         9700
   3 |     41 |        24500

sql> SELECT max(discount_rate) FROM (
       SELECT order_id, sum(amount)::float / max(total) AS discount_rate
       FROM order_coupons c JOIN orders o ON o.id = c.order_id GROUP BY order_id) t;
 max
------
 0.94""",
            "note": """겹쳐 쓴 주문이 959건 있고 최대 94% 할인이 나갔다

막은 적이 없어 이미 겹쳐 쓰이고 있다. 세 장을 겹친 41건은 평균 24,500원이 빠졌고, 한 건은 원가의 94% 가 할인됐다. 정책을 정하지 않으면 추석 쿠폰이 나가는 순간 이 구멍이 커진다."""}},
        {"t": "2026-09-03 14:20", "turn": {
            "kind": "thought",
            "ev": """마케팅 담당 의견 (2026-09-03 회의 메모)

- 겹쳐 쓰기를 열면 "쿠폰 모으기" 재미가 생겨 재방문이 는다. 경쟁사 두 곳은 2장까지 허용한다.
- 다만 생일 쿠폰(정액 5천원)과 시즌 쿠폰(정률 20%)이 겹치면 객단가 낮은 주문이 거의 공짜가 된다.
- 정산팀: 겹친 건은 매입 정산에서 수수료 계산이 어긋나 월말마다 손으로 맞추고 있다.""",
            "note": """열면 재방문이 늘지만 정산이 매달 손으로 맞춰야 한다

마케팅은 열자는 쪽, 정산은 닫자는 쪽이다. 정률과 정액이 겹칠 때만 문제가 커지므로 '가장 싼 값 하나만'과 '2장까지 허용' 둘을 놓고 대표가 정하는 것이 맞다."""}},
        {"t": "2026-09-04 10:00", "decide": {
            "s": "쿠폰은 겹쳐 쓰지 못하고 가장 싼 값 하나만 적용한다",
            "why": "정산을 매달 손으로 맞추는 값이 더 크다\n\n겹쳐 쓰기로 얻는 재방문보다, 수수료 정산이 어긋나 매월 손으로 맞추는 비용과 94% 할인 같은 사고 위험이 크다. 고객에게 손해가 아니도록 여러 장을 가졌으면 자동으로 가장 유리한 것을 골라 준다.",
            "by": "user"}},
        {"t": "2026-09-04 10:15", "define": {
            "term": "가장 싼 값",
            "meaning": "쿠폰을 다 적용해 보고 결제액이 최소가 되는 한 장\n\n정률·정액이 섞여 있어 액면가로는 비교되지 않는다. 주문 금액에 각각 적용해 본 뒤 결제액이 가장 낮아지는 한 장을 고른다. 같으면 유효기간이 먼저 끝나는 쪽을 쓴다."}},
        {"t": "2026-09-04 11:30", "commit": {
            "m": "쿠폰 적용을 한 장으로 제한하고 가장 싼 값을 고른다",
            "files": {"pricing/coupon.py": "def pick_best(order_total, coupons):\n    # 결제액이 최소가 되는 한 장. 같으면 먼저 만료되는 쪽.\n    return min(coupons, key=lambda c: (apply(order_total, c), c.expires_at))\n"}}},
        {"t": "2026-09-04 15:40", "close": """쿠폰은 한 장만, 가장 싼 값을 자동으로 고른다

이미 959건이 겹쳐 쓰였고 최대 94% 할인이 나간 상태였다. 마케팅은 열자고 했지만 정산이 매달 어긋나는 비용이 더 커서 닫기로 했다. 대신 여러 장을 가진 고객은 자동으로 가장 유리한 한 장이 적용된다. 이미 나간 959건은 소급하지 않는다."""},
    ],
}

THREADS["B2"] = {
    "repo": BACKEND,
    "start": "2026-09-05 09:20",
    "focus": """흩어진 할인 계산을 주문 서비스 한 곳으로 모은다

장바구니·주문·정산 세 곳이 각자 할인을 계산해 같은 주문에 서로 다른 금액을 낸다. 쿠폰 정책을
한 장으로 정한 김에(앞 스레드), 계산하는 자리를 하나로 모아 세 곳이 같은 답을 쓰게 한다.""",
    "steps": [
        {"t": "2026-09-05 11:00", "turn": {
            "kind": "finding",
            "ev": """$ grep -rn "discount" --include="*.py" cart/ orders/ settlement/ | grep -v test | wc -l
34

$ python -m tools.compare_pricing --sample 500
장바구니와 주문이 다른 건: 31 / 500
주문과 정산이 다른 건:     44 / 500
세 곳이 모두 다른 건:       7 / 500
최대 차이: 2,400원 (ORD-9120, 장바구니 18,600 / 주문 18,600 / 정산 21,000)

차이가 나는 자리:
  - 배송비를 할인 대상에 넣는지 (장바구니는 뺀다, 정산은 넣는다)
  - 원 단위 절사 방향 (주문은 내림, 정산은 반올림)""",
            "note": """500건 중 44건이 주문과 정산에서 다른 값을 낸다

세 곳이 배송비 포함 여부와 절사 방향을 각자 정하고 있다. 최대 2,400원 차이다. 계산 코드가 34군데 흩어져 있어 한 곳을 고쳐도 나머지가 따라오지 않는다."""}},
        {"t": "2026-09-05 15:30", "decide": {
            "s": "할인 계산은 주문 서비스의 가격 모듈만 한다",
            "why": "세 곳이 각자 계산해 44건이 어긋났다\n\n장바구니와 정산은 계산하지 않고 주문 서비스가 낸 값을 받아 쓴다. 배송비는 할인 대상에서 빼고 절사는 내림으로 통일한다. 규칙이 바뀔 때 고칠 자리가 한 곳이어야 한다.",
            "by": "user"}},
        {"t": "2026-09-07 16:40", "commit": {
            "m": "가격 계산을 pricing 모듈로 모으고 규칙을 고정한다",
            "files": {"pricing/calculator.py": "# 할인 대상에서 배송비는 뺀다. 절사는 내림.\ndef price(order):\n    base = sum(i.price * i.qty for i in order.items)\n    coupon = pick_best(base, order.coupons)\n    return floor_won(base - apply(base, coupon)) + order.shipping_fee\n"}}},
        {"t": "2026-09-08 14:10", "commit": {
            "m": "장바구니와 정산이 pricing 결과를 받아 쓰게 바꾼다",
            "files": {
                "cart/view.py": "total = pricing.price(draft_order)   # 직접 계산하지 않는다\n",
                "settlement/report.py": "amount = order.paid_amount           # 주문이 확정한 값을 그대로 쓴다\n"}}},
        {"t": "2026-09-09 11:20", "turn": {
            "kind": "verified",
            "ev": """$ python -m tools.compare_pricing --sample 500
장바구니와 주문이 다른 건: 0 / 500
주문과 정산이 다른 건:     0 / 500

$ pytest tests/pricing -q
................................................ 48 passed in 2.31s

$ grep -rn "discount" --include="*.py" cart/ orders/ settlement/ | grep -v test | wc -l
9""",
            "note": """세 곳이 같은 값을 내고 계산 코드가 34곳에서 9곳으로 줄었다

500건 표본에서 차이가 0 이다. 남은 9곳은 화면에 표시하는 문구와 테스트 픽스처라 계산이 아니다."""}},
        {"t": "2026-09-09 11:45", "close": """할인 계산을 주문 서비스로 모아 차이가 0이 됐다

장바구니·주문·정산이 각자 계산해 500건 중 44건이 어긋났고 최대 2,400원 차이가 났다. 원인은 배송비 포함 여부와 절사 방향이 자리마다 달랐던 것이다. 계산을 pricing 모듈 한 곳으로 모으고 배송비 제외·내림으로 고정했다. 계산 코드는 34곳에서 9곳으로 줄었다."""},
    ],
}

THREADS["B3"] = {
    "repo": BACKEND,
    "start": "2026-09-11 10:10",
    "focus": """무료배송 기준을 2만원에서 3만원으로 올린다

택배 단가가 올라 2만원 기준으로는 배송비가 마진을 넘는 주문이 생긴다. 기준을 올리되
얼마나 많은 주문이 영향을 받는지 먼저 보고 정한다.""",
    "steps": [
        {"t": "2026-09-11 11:30", "turn": {
            "kind": "finding",
            "ev": """sql> SELECT width_bucket(total, 0, 50000, 10) AS bucket,
            min(total) AS from_amt, max(total) AS to_amt, count(*)
     FROM orders WHERE created_at > now() - interval '30 days' GROUP BY 1 ORDER BY 1;
 bucket | from_amt | to_amt | count
--------+----------+--------+-------
      4 |    15012 |  19980 |  4102
      5 |    20000 |  24990 |  3881    <-- 지금 무료배송이 걸리는 구간
      6 |    25010 |  29970 |  2240
      7 |    30000 |  34980 |  1904

배송 단가: 2026-08 부터 3,000원 → 3,600원
2만~3만 구간 주문의 평균 마진: 4,100원""",
            "note": """2만~3만 구간 6,121건이 영향을 받고 마진은 500원 남는다

한 달 기준 2만~3만 사이 주문이 6,121건이다. 여기에 배송비 3,600원이 나가면 평균 마진 4,100원에서 500원만 남는다. 기준을 3만원으로 올리면 이 구간은 배송비를 고객이 낸다."""}},
        {"t": "2026-09-11 14:00", "decide": {
            "s": "무료배송 기준을 3만원으로 올린다",
            "why": "2만~3만 구간에서 마진이 500원만 남는다\n\n택배 단가가 3,600원으로 오르면서 이 구간 6,121건의 마진이 거의 사라진다. 기준을 올리되 기존 고객이 놀라지 않게 2주 전에 공지하고, 올리는 주에는 3천원 쿠폰을 뿌려 충격을 줄인다.",
            "by": "user"}},
        {"t": "2026-09-12 10:30", "commit": {
            "m": "무료배송 기준을 30000원으로 올린다",
            "files": {"pricing/shipping.py": "FREE_SHIPPING_THRESHOLD = 30_000   # 2026-09-26 부터. 이전 20,000\nAPPLY_FROM = date(2026, 9, 26)\n"}}},
        {"t": "2026-09-12 11:00", "turn": {
            "kind": "verified",
            "ev": """$ pytest tests/pricing/test_shipping.py -q
........................ 24 passed in 0.84s

$ python -m tools.preview_shipping --date 2026-09-26 --sample 200
무료배송 적용: 84 / 200 (이전 기준이면 138 / 200)
평균 배송비 부담: 1,970원 (이전 940원)""",
            "note": """적용일 기준으로 무료배송이 138건에서 84건으로 준다

날짜 조건이 걸려 9월 26일 전에는 예전 기준이 그대로 산다. 표본 200건에서 무료배송이 84건으로 줄고 고객이 내는 배송비는 평균 1,970원이 된다."""}},
        {"t": "2026-09-12 11:20", "close": """무료배송 기준을 3만원으로 올리고 9월 26일부터 적용한다

택배 단가가 3,600원으로 오르며 2만~3만 구간 6,121건의 마진이 500원까지 줄었다. 기준을 3만원으로 올리되 적용은 2주 뒤로 미뤄 공지 기간을 뒀고, 그 주에 3천원 쿠폰을 함께 내보낸다."""},
    ],
}

# ── 열려 있는 일 셋 (현황을 채운다: Codex 에게 · 미정 · 관찰 중) ──────────────────
# 새 스레드는 파일 끝에 붙인다 — 앞 아홉 개의 번호(#1~#9)가 바뀌지 않게.

THREADS["C4"] = {
    "repo": GATEWAY,
    "start": "2026-09-10 10:20",
    "focus": """대행사를 직접 부르는 남은 코드를 어댑터 뒤로 옮긴다

대행사를 부르는 코드는 어댑터 뒤에만 둔다고 정했는데, 실결제 전환 점검에서 어댑터를 거치지 않는 호출이
남아 있는 것이 보였다. 환경변수 한 줄로 되돌리는 길이 이 호출들에는 안 먹는다. 전부 옮기고 계약 시험을 붙인다.""",
    "steps": [
        {"t": "2026-09-10 11:00", "turn": {
            "kind": "finding",
            "ev": """$ grep -rn "payrail\\." --include=*.py . | grep -v "adapters/" | grep -v tests/
./jobs/settlement_sync.py:41:    rows = payrail.settlements.list(date=day)
./admin/refund_tool.py:88:        res = payrail.payments.cancel(pid, cancel_amount=amt)""",
            "note": """어댑터를 안 거치는 호출이 두 군데 남아 있다

정산 동기화 작업과 관리자 환불 도구다. PAYMENT_GATEWAY 를 legacy 로 돌려도 이 둘은 계속 페이레일을 부른다. 장애 때 되돌리는 길에 구멍이 있는 셈이다."""}},
        {"t": "2026-09-10 11:20", "declare": {
            "kind": "next", "owner": "codex",
            "s": """남은 두 군데를 어댑터 뒤로 옮기고 계약 시험을 붙인다

(a) settlement_sync 와 refund_tool 이 gateway 어댑터를 받게 바꾼다. (b) legacy·payrail 두 어댑터가 같은 계약 시험을 통과하게 한다. (c) 어댑터 밖 호출이 0줄인 것을 CI 에서 본다."""}},
    ],
}

THREADS["C5"] = {
    "repo": GATEWAY,
    "start": "2026-09-11 16:10",
    "focus": """부분취소한 주문의 환불액이 대행사 정산서와 1원씩 어긋난다

샌드박스 정산서를 우리 환불 기록과 맞춰 보다 나왔다. 실결제로 가면 매달 정산 대사에서 걸릴 일이라
전환 전에 어느 쪽 계산이 다른지 찾는다.""",
    "steps": [
        {"t": "2026-09-11 17:20", "turn": {
            "kind": "finding",
            "ev": """$ python -m tools.reconcile --env sandbox --from 2026-08-21 --to 2026-09-10
대사 대상 부분취소 212건
일치 187건 / 불일치 25건
불일치 25건 전부 쿠폰이 걸린 주문의 부분취소, 차이는 전부 1원 (우리 환불액이 1원 많음)
  예) 주문 29,000원 · 쿠폰 2,000원 · 9,900원짜리 한 개 취소
      쿠폰 몫 2000 × 9900 / 29000 = 682.76
      우리: 682 (절사) → 환불 9,218원   정산서: 683 (반올림) → 환불 9,217원""",
            "note": """차이는 전부 쿠폰 몫을 나눌 때의 절사와 반올림이다

부분취소 212건 중 25건이 1원씩 어긋나고 전부 쿠폰이 걸린 주문이다. 우리는 쿠폰 몫을 절사하고 대행사는 반올림한다. 원인은 찾았지만 어느 쪽에 맞출지는 정하지 않았다."""}},
        {"t": "2026-09-11 17:35", "declare": {
            "kind": "open",
            "s": """우리 계산을 반올림으로 바꿀지, 대사에서 1원 차이를 허용할지 안 정했다

반올림으로 바꾸면 할인 계산을 한 곳으로 모으며 고정한 가격 규칙을 다시 건드리게 된다. 허용으로 가면 정산 담당이 매달 25건 안팎을 눈으로 넘겨야 한다."""}},
    ],
}

THREADS["B4"] = {
    "repo": BACKEND,
    "start": "2026-09-12 13:30",
    "focus": """무료배송 기준을 올린 뒤 장바구니 이탈이 느는지 지켜본다

9월 26일부터 무료배송 기준이 3만원이 된다. 2만~3만 구간 고객이 결제 직전에 빠져나가는지, 3만원을 채우려고
더 담는지 적용 뒤 2주 동안 본다. 이탈률이 기준선보다 5%p 넘게 오르면 쿠폰 금액을 다시 정한다.""",
    "steps": [
        {"t": "2026-09-12 14:40", "turn": {
            "kind": "finding",
            "ev": """sql> SELECT date_trunc('week', created_at)::date AS wk,
            round(count(*) FILTER (WHERE status = 'abandoned')::numeric / count(*), 3) AS abandon_rate,
            round(avg(total) FILTER (WHERE status = 'paid')) AS avg_paid
     FROM carts WHERE total BETWEEN 20000 AND 29999 AND created_at >= '2026-08-17' GROUP BY 1 ORDER BY 1;
     wk     | abandon_rate | avg_paid
------------+--------------+----------
 2026-08-17 |        0.412 |    24310
 2026-08-24 |        0.398 |    24120
 2026-08-31 |        0.405 |    24480
 2026-09-07 |        0.401 |    24260""",
            "note": """기준선은 이탈률 40%, 평균 결제액 24,300원이다

적용 전 4주 동안 2만~3만 구간 장바구니의 이탈률은 39.8~41.2% 사이에서 움직였다. 이 폭을 넘는 변화만 기준 변경의 영향으로 본다."""}},
        {"t": "2026-09-12 14:55", "declare": {
            "kind": "next", "owner": "watch",
            "s": """9월 26일 적용 뒤 2주 동안 이탈률과 평균 결제액을 본다

매주 월요일 같은 쿼리를 돌린다. 이탈률이 45%를 넘으면 쿠폰 금액을 다시 정하는 스레드를 연다."""}},
    ],
}
