"""Fabricated records for the demo (English edition).

A made-up team building a made-up commerce backend over four weeks. Every person, company,
address and number here is invented. tools/demo_seed.py replays this through the product's own
core API to build a demo sqlite database.

This is not a translation of tools/demo_seed_data.py — it is the same four weeks as an
English-speaking team would have written them. The Korean edition goes to the Wanted submission,
this one goes to the public link (D15188).

How to read it — each entry in a thread's `steps` is one thing happening, in time order:
    {"t": when, "turn": {"ev": raw text, "note": "title\\n\\nbody", "kind": change|verified|finding|thought}}
    {"t": when, "decide": {"s": statement, "why": reason, "by": user|agent, "scope": thread|repo}}
    {"t": when, "constrain": {...}}   {"t": when, "rule_out": {"s": hypothesis, "scope": where}}
    {"t": when, "define": {"term": name, "meaning": what it means}}
    {"t": when, "declare": {"kind": focus|open|next, "s": statement, "owner": required for next}}
    {"t": when, "commit": {"m": message, "files": {path: contents}}}
    {"t": when, "close": the result line}
The first line of a record is its title: at most 120 display columns, never joined with ' — '
(extension 52, widened in 77). Evidence and observations are stored verbatim, so they are exempt.
"""

BACKEND = "onstore/backend"
GATEWAY = "onstore/pay-gateway"

TOPICS = [
    {
        "name": "Moving payment providers",
        "at": "2026-08-18 09:20",
        "summary": "Fees and partial refunds push us off our current provider onto Payrail. Compare the three (8/18), get the sandbox working (8/20), pre-flight the live switch (9/8).",
        "conclusion": "We moved to Payrail and only the live switch is left\n\nOnly two of the three could refund a partial amount over the API, and Payrail settles at D+2, so it won the comparison. The sandbox cleared payment, partial refund and webhooks. What remains is putting one real charge through on the live key, and that needs sign-off from the CEO.",
        "threads": ["C1", "C2", "C3"],
    },
    {
        "name": "Order and payment incidents",
        "at": "2026-08-26 08:40",
        "summary": "What broke in production and what we did about it. Cancellations failing (8/26), duplicate webhooks (9/1), stock going negative (9/10).",
        "conclusion": "Not waiting on someone else's system was the answer all three times\n\nThe failed cancellations and the duplicate webhooks both came from trying to absorb another system being slow, or arriving twice, inside our own request. Splitting accept from confirm and adding an idempotency key made them go away. Negative stock is still open.",
        "threads": ["A1", "A2", "A3"],
    },
    {
        "name": "Coupons and discounts",
        "at": "2026-09-03 09:10",
        "summary": "Decide whether coupons stack (9/3), pull the scattered discount math into one service (9/5), raise the free shipping threshold (9/11).",
        "conclusion": "We settled the policy first, then put the math in one place\n\nCoupons do not stack; only the single cheapest one applies. After that we moved the calculation out of cart, order and settlement into the order service alone, and the three of them stopped disagreeing about the total.",
        "threads": ["B1", "B2", "B3"],
    },
]

THREADS: dict[str, dict] = {}

THREADS["C1"] = {'repo': 'onstore/pay-gateway',
 'start': '2026-08-18 09:30',
 'focus': 'Pick one of three payment providers\n'
          '\n'
          'Our current provider charges 3.4% on card and only takes partial cancellations over the phone. Both hurt\n'
          "more every month as volume grows. We're weighing Payrail, Moneybridge and Squarepay on fee, settlement\n"
          'window, partial cancel API and webhook retries.',
 'steps': [{'t': '2026-08-18 10:40',
            'turn': {'kind': 'finding',
                     'ev': "Table pulled from the three vendors' sales decks and API docs (2026-08-18)\n"
                           '\n'
                           '                    Payrail           Moneybridge       Squarepay\n'
                           'Card fee            2.9%              2.7%              3.1%\n'
                           'Settlement          D+2               D+5               D+3\n'
                           'Partial cancel API  yes (by amount)   no (full only)    yes (per line)\n'
                           'Webhook retries     5x / 24h          3x / 1h           none\n'
                           'Minimum term        none              12 months         6 months\n'
                           'Test environment    sandbox, always   3 days on request sandbox, always',
                     'note': 'Only two of the three expose partial cancel as an API\n'
                             '\n'
                             'Moneybridge has the cheapest fee, but it can only cancel a payment in full and locks '
                             "us into a 12-month contract. 62% of our cancellations are partial, so it's out. That "
                             'narrows it to Payrail and Squarepay.'}},
           {'t': '2026-08-18 14:20',
            'turn': {'kind': 'finding',
                     'ev': '$ for h in payrail squarepay; do echo "== $h"; for i in 1 2 3; do curl -s -o /dev/null '
                           '-w "%{time_total}\\n" https://sandbox.$h.test/v1/payments -X POST -d @sample.json; done; '
                           'done\n'
                           '== payrail\n'
                           '0.412\n'
                           '0.388\n'
                           '0.401\n'
                           '== squarepay\n'
                           '0.455\n'
                           '0.470\n'
                           '0.462\n'
                           '\n'
                           '$ curl -s -X POST https://sandbox.squarepay.test/v1/payments/PAY-9f21/cancel -d '
                           '\'{"amount": 12000}\'\n'
                           '{"error":"PARTIAL_NOT_SUPPORTED_FOR_INSTALLMENT","message":"Partial cancellation is not '
                           'supported for installment payments"}',
                     'note': "Squarepay can't partially cancel an installment payment\n"
                             '\n'
                             "Both take about 0.4s to create a payment, so latency isn't what separates them. "
                             'Cancellation is. Squarepay refuses partial cancellation on installment plans, and 28% '
                             'of our transactions are installments. Payrail is the only one left.'}},
           {'t': '2026-08-19 10:00',
            'decide': {'s': 'The payment provider becomes Payrail',
                       'why': 'Partial cancel and the settlement window decided it\n'
                              '\n'
                              'Moneybridge is 0.2pp cheaper, but it only cancels in full and wants a 12-month '
                              'contract. Squarepay blocks partial cancellation on installments, which are 28% of our '
                              'transactions. Payrail is the only one of the three that lets us cancel a specific '
                              'amount, and at D+2 it settles fastest.',
                       'by': 'user'}},
           {'t': '2026-08-19 10:20',
            'constrain': {'s': 'Provider calls only ever go through the adapter',
                          'why': 'This is the second provider swap\n'
                                 '\n'
                                 "Last year's took three weeks because payment calls were scattered across orders, "
                                 'settlement and the admin tool. This time the only thing service code knows about '
                                 'is the PaymentGateway interface.',
                          'by': 'user',
                          'scope': 'repo'}},
           {'t': '2026-08-19 10:35',
            'close': 'Went with Payrail, decided by partial cancel and settlement\n'
                     '\n'
                     'Payrail is the only one of the three that cancels a specific amount (Squarepay blocks it on '
                     'installments, and 28% of our transactions are installments). It also settles fastest at D+2. '
                     'Sandbox integration is next.'}]}

THREADS["C2"] = {'repo': 'onstore/pay-gateway',
 'start': '2026-08-20 09:40',
 'focus': 'Get payment and partial cancel through the Payrail sandbox\n'
          '\n'
          'The previous thread settled on Payrail. Run one full loop in the sandbox: create a payment, get it '
          'approved,\n'
          'partially cancel it, receive the webhooks. The repo-wide rule is that provider calls live behind an '
          'adapter,\n'
          'so this lands as a PaymentGateway implementation.',
 'steps': [{'t': '2026-08-20 11:15',
            'turn': {'kind': 'finding',
                     'ev': '$ python -m gateway.payrail.smoke\n'
                           'POST https://sandbox.payrail.test/v1/payments\n'
                           '< HTTP/1.1 401 Unauthorized\n'
                           '< {"code":"SIGNATURE_MISMATCH","message":"Signature does not match","hint":"Check how '
                           'the signature string is built"}\n'
                           '\n'
                           'Signature string we sent:\n'
                           '  amount=15000&currency=KRW&merchant=onstore&order_id=ORD-20260820-001&timestamp=1787...',
                     'note': '401 on the signature, so the rule for building it must differ\n'
                             '\n'
                             'Key and secret went in correctly (same test key the docs use). What is left is how we '
                             'assemble the string we sign. Next: put the worked example from the docs next to ours '
                             'and compare it character by character.'}},
           {'t': '2026-08-20 13:50',
            'turn': {'kind': 'finding',
                     'ev': 'Payrail docs 4.2 Signature generation (excerpt)\n'
                           '\n'
                           '  1. Sort the request parameters by key name in **UTF-8 byte order**\n'
                           '  2. Join key=value pairs with & (do not URL-encode the values)\n'
                           '  3. Append &secret=<secret> to the end\n'
                           '  4. SHA-256, lowercase hex\n'
                           '\n'
                           'Our code (gateway/payrail/sign.py:18):\n'
                           "  items = sorted(params.items())          # Python's default sort\n"
                           '  body = "&".join(f"{k}={quote(v)}" for k, v in items)   # ← this URL-encodes the values',
                     'note': 'URL-encoding the values is what breaks it\n'
                             '\n'
                             'The docs sign the raw values; we run every one of them through quote(). Order ids are '
                             'digits and hyphens, so they survive encoding unchanged and most requests looked '
                             'identical. The product name carries spaces, and that is where the two strings part '
                             'ways. Drop the encoding and they match.'}},
           {'t': '2026-08-20 14:30',
            'commit': {'m': 'Stop URL-encoding values when building the payment signature',
                       'files': {'gateway/payrail/sign.py': '# docs 4.2: values are not encoded\n'
                                                            "body = '&'.join(f'{k}={v}' for k, v in "
                                                            'sorted(params.items()))\n'}}},
           {'t': '2026-08-20 14:45',
            'turn': {'kind': 'verified',
                     'ev': '$ python -m gateway.payrail.smoke\n'
                           'POST https://sandbox.payrail.test/v1/payments\n'
                           '< HTTP/1.1 200 OK\n'
                           '< '
                           '{"payment_id":"PAY-SBX-4471","status":"APPROVED","amount":15000,"approved_at":"2026-08-20T14:44:52+09:00"}',
                     'note': 'Sandbox payment goes all the way to APPROVED\n'
                             '\n'
                             'With the encoding gone it came back 200 on the first try. Create and approve are done. '
                             'Partial cancel next.'}},
           {'t': '2026-08-21 10:20',
            'turn': {'kind': 'finding',
                     'ev': '$ curl -s -X POST https://sandbox.payrail.test/v1/payments/PAY-SBX-4471/cancel \\\n'
                           '    -H "X-Signature: ..." -d \'{"amount": 5000}\'\n'
                           '{"code":"CANCEL_AMOUNT_REQUIRED","message":"cancel_amount is required"}\n'
                           '\n'
                           'Docs 6.1 Partial cancel request:\n'
                           '  cancel_amount   integer  required  amount to cancel (KRW)\n'
                           '  reason          string   optional  reason for the cancellation',
                     'note': 'Partial cancel wants cancel_amount, not amount\n'
                             '\n'
                             'Create takes amount, cancel takes cancel_amount; the two endpoints name the same thing '
                             'differently. The adapter maps it so nothing above it has to know.'}},
           {'t': '2026-08-21 10:50',
            'commit': {'m': 'Send the partial cancel amount as cancel_amount',
                       'files': {'gateway/payrail/client.py': 'def cancel(self, payment_id, amount, reason=None):\n'
                                                              '    return '
                                                              "self._post(f'/v1/payments/{payment_id}/cancel',\n"
                                                              "                      {'cancel_amount': amount, "
                                                              "'reason': reason})\n"}}},
           {'t': '2026-08-21 11:10',
            'turn': {'kind': 'verified',
                     'ev': '$ python -m gateway.payrail.smoke --full\n'
                           'create payment ........... 200 PAY-SBX-4472 APPROVED 15000 KRW\n'
                           'partial cancel 5000 ...... 200 CANCEL-SBX-881 PARTIAL_CANCELED balance 10000 KRW\n'
                           'partial cancel 10000 ..... 200 CANCEL-SBX-882 CANCELED balance 0 KRW\n'
                           'webhooks received ........ 3 (payment.approved, payment.partial_canceled, '
                           'payment.canceled)\n'
                           'took 12.4s',
                     'note': 'Payment, partial cancel and webhooks made it around once\n'
                             '\n'
                             'Canceled in two pieces and watched the balance land on 0. All three webhooks arrived. '
                             'That is the whole sandbox scope.'}},
           {'t': '2026-08-24 15:30',
            'close': 'Payment, partial cancel and webhooks pass in the sandbox\n'
                     '\n'
                     'Two things blocked us. We URL-encoded the values in the signature string, which the docs tell '
                     'you not to do, and the cancel amount field is cancel_amount. Both stayed inside the adapter; '
                     'service code still only sees PaymentGateway. Switching to live keys gets its own thread.'}]}

THREADS["C3"] = {'repo': 'onstore/pay-gateway',
 'start': '2026-09-08 10:00',
 'focus': 'Run the pre-flight checks before the first live charge\n'
          '\n'
          'Sandbox passed in the previous thread. A live key moves real money the moment we use it, so we check\n'
          'key storage, the settlement account, the cancellation limit and the way back out before we ask for '
          'sign-off.',
 'steps': [{'t': '2026-09-08 11:30',
            'turn': {'kind': 'finding',
                     'ev': 'Go-live checklist (2026-09-08)\n'
                           '\n'
                           '[x] Live key never in code or the repo    secrets store only, injected at deploy\n'
                           '[x] Settlement account verified           company account registered, 1 KRW test deposit '
                           'cleared\n'
                           '[x] Webhook endpoint https certificate    valid, expires 2027-03\n'
                           '[ ] Daily cancellation limit              unknown, the contract does not say\n'
                           '[ ] Rollback path to the old provider     adapter exists, never actually exercised\n'
                           '[x] Order state when a payment fails      stays pending, swept automatically after 30 '
                           'min',
                     'note': 'Two boxes are still empty, the cancel limit and the rollback\n'
                             '\n'
                             'Key, account and certificate are done. What is left: (1) the daily cancellation limit '
                             'is nowhere in the contract, so we have to ask our sales rep, and (2) we have never '
                             'actually exercised the path back to the old provider.'}},
           {'t': '2026-09-09 14:00',
            'turn': {'kind': 'verified',
                     'ev': '$ PAYMENT_GATEWAY=legacy python -m gateway.smoke --full\n'
                           'create payment ......... 200 LEG-7781 APPROVED 15000 KRW\n'
                           'full cancel ............ 200 LEG-CANCEL-331 CANCELED\n'
                           'took 8.9s\n'
                           '\n'
                           '$ PAYMENT_GATEWAY=payrail python -m gateway.smoke --full\n'
                           'create payment ......... 200 PAY-SBX-4490 APPROVED 15000 KRW\n'
                           'partial cancel 5000 .... 200 CANCEL-SBX-903 PARTIAL_CANCELED\n'
                           'took 12.1s',
                     'note': 'One environment variable takes us back to the old provider\n'
                             '\n'
                             'The rollback path holds up. Set PAYMENT_GATEWAY to legacy and the old route is still '
                             'alive end to end. No deploy needed, just the variable, so an outage costs us minutes. '
                             'Only the cancellation limit is left.'}},
           {'t': '2026-09-09 14:20',
            'declare': {'kind': 'open',
                        's': 'We do not switch on without knowing the daily cancel limit\n'
                             '\n'
                             'Asked our sales rep on the 8th and there is still no answer. If the limit sits below '
                             'what we cancel in a day (2,400,000 KRW on average, 8,100,000 KRW at peak), '
                             'cancellations start failing at month end. Once we have the number it goes into a '
                             'contract addendum and then we switch on.',
                        'by': 'user'}},
           {'t': '2026-09-09 14:25',
            'declare': {'kind': 'next',
                        's': 'Get sign-off and the cancel limit, then turn the live key on\n'
                             '\n'
                             '(a) Get the daily cancellation limit from our sales rep. (b) Sign-off from the CEO. '
                             '(c) Put the live key in the secrets store and flip PAYMENT_GATEWAY to payrail. (d) Put '
                             'one small charge through for real, confirm a partial cancel on it, then announce.',
                        'owner': 'user',
                        'by': 'user'}}]}

THREADS["A1"] = {'repo': 'onstore/backend',
 'start': '2026-08-26 09:10',
 'focus': 'Find out why dozens of order cancellations fail every day\n'
          '\n'
          'Since yesterday support has been getting more tickets that say "I hit cancel and nothing was canceled".\n'
          'The screen shows a failure, but for some of those orders the payment was canceled anyway. Find where the\n'
          'two stories split, and fix it.',
 'steps': [{'t': '2026-08-26 09:40',
            'turn': {'kind': 'finding',
                     'ev': 'Sentry: OrderCancelTimeout (47 in the last 24 hours)\n'
                           '\n'
                           'Traceback (most recent call last):\n'
                           '  File "orders/service.py", line 212, in cancel_order\n'
                           '    result = gateway.cancel(order.payment_id, amount=order.total)\n'
                           '  File "gateway/payrail/client.py", line 88, in cancel\n'
                           '    return self._post(f"/v1/payments/{payment_id}/cancel", {...})\n'
                           '  File "gateway/http.py", line 41, in _post\n'
                           '    resp = self.session.post(url, json=body, timeout=30)\n'
                           "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.payrail.test', "
                           'port=443):\n'
                           '    Read timed out. (read timeout=30)\n'
                           '\n'
                           'By hour:\n'
                           '  11:00-12:00  14\n'
                           '  12:00-13:00   9\n'
                           '  18:00-19:00  17\n'
                           '  19:00-20:00   7',
                     'note': "Every cancel failure is our 30s timeout on Payrail's cancel call\n"
                             '\n'
                             'All 47 are the same exception. Nothing our code raised: we sat waiting on Payrail and '
                             'the read was cut off at 30 seconds. They pile up in the lunch and dinner peaks.'}},
           {'t': '2026-08-26 11:20',
            'turn': {'kind': 'finding',
                     'ev': "$ grep 'payrail.cancel' /var/log/app/api-*.log | awk '{print $NF}' | sort -n | \\\n"
                           '    awk \'{a[NR]=$1} END {print "n="NR, "p50="a[int(NR*0.5)], "p95="a[int(NR*0.95)], '
                           '"p99="a[int(NR*0.99)], "max="a[NR]}\'\n'
                           'n=2841 p50=1.31 p95=18.70 p99=41.20 max=63.80\n'
                           '\n'
                           "$ grep 'payrail.payment.create' /var/log/app/api-*.log | awk '{print $NF}' | sort -n | "
                           '\\\n'
                           '    awk \'{a[NR]=$1} END {print "n="NR, "p50="a[int(NR*0.5)], "p95="a[int(NR*0.95)], '
                           '"p99="a[int(NR*0.99)]}\'\n'
                           'n=19204 p50=0.39 p95=0.91 p99=1.44',
                     'note': 'Only the cancel API has a p99 of 41s, while payment creation is 1.4s\n'
                             '\n'
                             'Same provider: creating a payment comes back at p99 1.4s and only cancel takes 41s. '
                             'Our 30s timeout sits between their p95 and p99, so peak traffic runs straight into it. '
                             'Raising the timeout is not the answer, because then the user waits a minute.'}},
           {'t': '2026-08-26 11:40',
            'rule_out': {'s': 'Cancels are slow because of our own network or a too-small connection pool',
                         'scope': 'the cancel path (orders.cancel_order → gateway.cancel), as of 2026-08-26'}},
           {'t': '2026-08-26 14:10',
            'turn': {'kind': 'finding',
                     'ev': 'sql> SELECT status, count(*) FROM orders\n'
                           "     WHERE updated_at > now() - interval '24 hours' GROUP BY status ORDER BY 2 DESC;\n"
                           ' status      | count\n'
                           '-------------+-------\n'
                           ' paid        |  8812\n'
                           ' delivered   |  3190\n'
                           ' canceled    |   402\n'
                           ' cancelling  |    17     <-- stuck here\n'
                           ' pending     |     9\n'
                           '\n'
                           "sql> SELECT id, payment_id, updated_at FROM orders WHERE status = 'cancelling' ORDER BY "
                           'updated_at LIMIT 5;\n'
                           ' id       | payment_id    | updated_at\n'
                           '----------+---------------+---------------------\n'
                           ' ORD-8841 | PAY-33120     | 2026-08-25 18:41:02\n'
                           ' ORD-8853 | PAY-33147     | 2026-08-25 18:44:55\n'
                           ' ORD-8901 | PAY-33298     | 2026-08-25 19:02:13\n'
                           ' ORD-9012 | PAY-33511     | 2026-08-26 11:38:40\n'
                           ' ORD-9033 | PAY-33559     | 2026-08-26 12:05:27\n'
                           '\n'
                           'sql> -- and what does Payrail say?\n'
                           '     -- looked up PAY-33120, PAY-33147: both CANCELED (the cancel went through)',
                     'note': 'The money came back but the order is stuck in cancelling\n'
                             '\n'
                             "17 orders are sitting in the in-between state. On Payrail's side the cancellation "
                             'succeeded. Once the timeout cut us off we neither rolled the order back nor confirmed '
                             'it, so the order was left behind on its own. The customer is looking at a failure '
                             'screen with the money already back.'}},
           {'t': '2026-08-26 15:00',
            'decide': {'s': 'Cancellation becomes two stages: accept, then confirm',
                       'why': 'Do not make the user wait on something we do not control\n'
                              '\n'
                              "The cancel API's p99 is 41s, so whatever timeout we pick gets cut off at peak. The "
                              'request marks the order cancelling, accepts it, and returns right away. A job queue '
                              'makes the actual Payrail call and flips the order to canceled when the result lands. '
                              'If the webhook arrives first, it confirms in that same place.',
                       'by': 'user'}},
           {'t': '2026-08-26 15:15',
            'constrain': {'s': 'Never wait on an external payment call inside a user request',
                          'why': 'This is the root of the outage\n'
                                 '\n'
                                 'We do not get to decide how long the provider takes. Wait for it inside '
                                 "request/response and that time becomes the user's wait, and a timeout leaves an "
                                 'in-between state behind. Payment creation is fast today, but nothing says it stays '
                                 'fast, so it lives under the same rule.',
                          'by': 'user',
                          'scope': 'repo'}},
           {'t': '2026-08-27 11:30',
            'commit': {'m': 'Split cancellation into accept and confirm',
                       'files': {'orders/service.py': 'def cancel_order(order_id):\n'
                                                      '    order = repo.get(order_id)\n'
                                                      '    order.mark_cancelling()      # accept only, respond '
                                                      'immediately\n'
                                                      "    queue.enqueue('confirm_cancel', order_id=order_id)\n"
                                                      '    return order\n',
                                 'orders/workers.py': 'def confirm_cancel(order_id):\n'
                                                      '    order = repo.get(order_id)\n'
                                                      '    result = gateway.cancel(order.payment_id, '
                                                      'amount=order.total)\n'
                                                      '    order.mark_canceled(result.cancel_id)\n'}}},
           {'t': '2026-08-27 16:20',
            'commit': {'m': 'Add a sweep that recovers orders stuck in cancelling',
                       'files': {'orders/recovery.py': '# orders cancelling for more than 10 minutes: ask the '
                                                       'provider, then confirm or roll back\n'
                                                       'def sweep_stuck_cancelling():\n'
                                                       "    for order in repo.stuck('cancelling', minutes=10):\n"
                                                       '        state = gateway.get(order.payment_id).status\n'
                                                       "        order.mark_canceled() if state == 'CANCELED' else "
                                                       'order.mark_paid()\n'}}},
           {'t': '2026-08-28 10:40',
            'turn': {'kind': 'verified',
                     'ev': '$ python -m tools.loadtest --scenario cancel --rps 20 --duration 300\n'
                           '6000 requests · 6000 ok · 0 failed\n'
                           'response time p50=0.08 p95=0.14 p99=0.21 (accept)\n'
                           'time to confirm p50=1.4 p95=19.2 p99=44.8 (job queue)\n'
                           '\n'
                           '$ python -m orders.recovery --once\n'
                           'checked 17 orders in cancelling: confirmed 17 · rolled back 0\n'
                           '\n'
                           "sql> SELECT status, count(*) FROM orders WHERE status = 'cancelling';\n"
                           ' count\n'
                           '-------\n'
                           '     0',
                     'note': 'Cancel failures are at zero and the 17 stuck orders are unstuck\n'
                             '\n'
                             'What the user sees comes back at p99 0.21s. Payrail can burn 44 seconds and it happens '
                             'inside the job queue while the screen already says the cancellation was accepted. The '
                             'sweep confirmed all 17 stuck orders as canceled.'}},
           {'t': '2026-08-28 10:55',
            'close': 'Splitting cancel into accept and confirm took failures to zero\n'
                     '\n'
                     "The cause was Payrail's cancel API at p99 41s against our 30s timeout. When the call was cut "
                     'off the order stayed in cancelling and only the money went back, so customers saw a failure '
                     'screen (17 of them). The request now only accepts; the job queue confirms. User-facing p99 is '
                     '0.21s, and the sweep clears anything that gets stuck.'}]}

THREADS["A2"] = {'repo': 'onstore/backend',
 'start': '2026-09-01 13:20',
 'focus': 'Payment webhooks arrive twice and loyalty points get credited twice\n'
          '\n'
          'Six orders turned up this morning with their points credited twice. It looks like duplicate webhook\n'
          'deliveries. Find out how far the double-handling reaches and stop it.',
 'steps': [{'t': '2026-09-01 14:05',
            'turn': {'kind': 'finding',
                     'ev': "$ grep 'webhook.received' /var/log/app/webhook-2026-09-01.log | grep PAY-41288\n"
                           '2026-09-01 09:12:03 webhook.received event=payment.approved payment_id=PAY-41288 '
                           'delivery_id=whd_8813\n'
                           '2026-09-01 09:12:34 webhook.received event=payment.approved payment_id=PAY-41288 '
                           'delivery_id=whd_8819\n'
                           '\n'
                           "$ grep 'webhook.handled' /var/log/app/webhook-2026-09-01.log | grep PAY-41288\n"
                           '2026-09-01 09:12:04 webhook.handled  payment_id=PAY-41288 points_awarded=1500\n'
                           '2026-09-01 09:12:35 webhook.handled  payment_id=PAY-41288 points_awarded=1500\n'
                           '\n'
                           'Payrail docs 7.3:\n'
                           '  Webhooks are delivered at least once. If we do not get a 200 back within 2 seconds, we '
                           'redeliver.\n'
                           '  The same event may arrive more than once, so make your handler idempotent on '
                           'delivery_id.',
                     'note': 'A redelivery 31 seconds later, exactly what the docs warn about\n'
                             '\n'
                             'Same payment_id, two different delivery_ids. The docs say delivery is at-least-once '
                             "and tell you to handle it idempotently, and we don't. Our first response looks like it "
                             'went over 2 seconds, which is what triggered the retry.'}},
           {'t': '2026-09-01 15:30',
            'turn': {'kind': 'finding',
                     'ev': "$ grep 'webhook.received\\|webhook.handled' /var/log/app/webhook-2026-09-01.log | \\\n"
                           '    awk \'/received/{t[$6]=$1" "$2} /handled/{print $4, $1" "$2}\' | head -3\n'
                           'payment_id=PAY-41288 2026-09-01 09:12:04\n'
                           'payment_id=PAY-41290 2026-09-01 09:12:11\n'
                           'payment_id=PAY-41301 2026-09-01 09:13:02\n'
                           '\n'
                           "$ grep 'webhook.duration' /var/log/app/webhook-2026-09-01.log | awk '{print $NF}' | sort "
                           '-n | tail -5\n'
                           '2.81\n'
                           '3.02\n'
                           '3.44\n'
                           '4.10\n'
                           '5.21\n'
                           '\n'
                           'What the webhook handler does:\n'
                           '  1. update payment state        0.02s\n'
                           '  2. credit loyalty points       0.04s\n'
                           '  3. send order confirmation     2.9s   <-- waits on the external mail API',
                     'note': 'Sending mail inside the webhook is what pushes us past 2 seconds\n'
                             '\n'
                             "Nearly all of the handling time is the mail API. We can't return a 200 inside 2 "
                             'seconds, so Payrail redelivers, and with no idempotency the points land twice. The '
                             "repo constraint we set during the cancel incident (don't wait on the outside inside a "
                             'request) covers this case too.'}},
           {'t': '2026-09-01 15:50',
            'decide': {'s': 'Make the webhook idempotent on delivery_id and return 200 right away',
                       'why': 'Redelivery is Payrail working as designed\n'
                              '\n'
                              "At-least-once means we can't stop the duplicates, so the receiving side has to absorb "
                              'them. Store delivery_id under a unique key and drop the second one quietly. Slow work '
                              'like mail moves to the job queue so the response finishes well inside 2 seconds (same '
                              'reason as the repo constraint from the cancel incident).',
                       'by': 'user'}},
           {'t': '2026-09-02 10:15',
            'commit': {'m': 'Key the webhook on delivery_id and move mail to the queue',
                       'files': {'webhooks/receiver.py': 'def receive(payload):\n'
                                                         "    if not seen.add_if_absent(payload['delivery_id']):\n"
                                                         '        return 200            # second delivery onward: '
                                                         'drop it quietly\n'
                                                         '    apply_payment_state(payload)\n'
                                                         "    queue.enqueue('send_order_mail', "
                                                         "order_id=payload['order_id'])\n"
                                                         '    return 200\n',
                                 'webhooks/models.py': 'class WebhookDelivery(Model):\n'
                                                       '    delivery_id = CharField(unique=True)   # the unique '
                                                       'constraint is what makes this idempotent\n'
                                                       '    received_at = DateTimeField()\n'}}},
           {'t': '2026-09-02 14:40',
            'turn': {'kind': 'verified',
                     'ev': '$ python -m tools.webhook_replay --file samples/duplicated.json --times 3\n'
                           'sent 3 times (same delivery_id whd_9001)\n'
                           '  attempt 1  200  handled (1500 points)\n'
                           '  attempt 2  200  dropped as duplicate\n'
                           '  attempt 3  200  dropped as duplicate\n'
                           'points total: 1500\n'
                           '\n'
                           "$ grep 'webhook.duration' /var/log/app/webhook-2026-09-02.log | awk '{print $NF}' | sort "
                           '-n | tail -3\n'
                           '0.09\n'
                           '0.11\n'
                           '0.14',
                     'note': 'Three duplicate deliveries, points credited once, 0.14s to respond\n'
                             '\n'
                             'The idempotency key holds, and dropping mail from the handler took the time down to '
                             '0.14s. That is nowhere near the 2-second retry threshold, so redeliveries should '
                             'mostly stop happening at all.'}},
           {'t': '2026-09-02 15:00',
            'close': 'A delivery_id idempotency key ended the duplicate point credits\n'
                     '\n'
                     'Payrail webhooks are at-least-once, so redelivery is normal behavior on their side. The reason '
                     'we missed the 2-second window was the mail API being called inside the webhook (2.9s). With '
                     'the idempotency key in place and mail moved to the queue, handling takes 0.14s. Ops clawed '
                     'back the 6 orders that were credited twice.'}]}

THREADS["A3"] = {'repo': 'onstore/backend',
 'start': '2026-09-10 09:30',
 'focus': 'Find the path that let three orders push stock below zero\n'
          '\n'
          'The morning stock check on September 10 turned up three products sitting at -1, -2 and -1. Sold-out\n'
          "items got sold, so some path isn't stopping the order. Find out where they came in at the same time.",
 'steps': [{'t': '2026-09-10 10:40',
            'turn': {'kind': 'finding',
                     'ev': 'sql> SELECT product_id, stock FROM products WHERE stock < 0;\n'
                           ' product_id | stock\n'
                           '------------+-------\n'
                           ' SKU-2288   |    -1\n'
                           ' SKU-3401   |    -2\n'
                           ' SKU-3407   |    -1\n'
                           '\n'
                           'sql> SELECT o.id, o.created_at, i.product_id FROM orders o JOIN order_items i ON '
                           'i.order_id = o.id\n'
                           "     WHERE i.product_id IN ('SKU-2288','SKU-3401','SKU-3407') AND o.created_at > "
                           "'2026-09-09'\n"
                           '     ORDER BY o.created_at;\n'
                           ' id       | created_at              | product_id\n'
                           '----------+-------------------------+------------\n'
                           ' ORD-9881 | 2026-09-09 20:00:01.204 | SKU-3401\n'
                           ' ORD-9882 | 2026-09-09 20:00:01.219 | SKU-3401\n'
                           ' ORD-9883 | 2026-09-09 20:00:01.402 | SKU-3401\n'
                           ' ORD-9885 | 2026-09-09 20:00:02.118 | SKU-2288\n'
                           ' ORD-9886 | 2026-09-09 20:00:02.140 | SKU-2288\n'
                           ' ORD-9890 | 2026-09-09 20:00:03.901 | SKU-3407\n'
                           ' ORD-9891 | 2026-09-09 20:00:03.933 | SKU-3407',
                     'note': 'Orders for the same product piled up in the first three seconds of the 20:00 flash '
                             'sale\n'
                             '\n'
                             'All three products took overlapping orders tens of milliseconds apart, right after '
                             '20:00 on September 9. That is when the flash sale opens. The stock decrement does not '
                             'look able to hold off simultaneous requests.'}},
           {'t': '2026-09-10 14:20',
            'turn': {'kind': 'finding',
                     'ev': 'inventory/service.py:41\n'
                           '\n'
                           'def reserve(product_id, qty):\n'
                           '    product = Product.objects.get(id=product_id)     # 1) read\n'
                           '    if product.stock < qty:                          # 2) compare\n'
                           '        raise OutOfStock(product_id)\n'
                           '    product.stock -= qty                             # 3) subtract\n'
                           '    product.save()                                   # 4) write\n'
                           '    return True\n'
                           '\n'
                           '$ grep -n "reserve(" -r orders/ | head\n'
                           'orders/service.py:88:        inventory.reserve(item.product_id, item.qty)\n'
                           '\n'
                           '$ psql -c "SHOW transaction_isolation;"\n'
                           ' transaction_isolation\n'
                           '-----------------------\n'
                           ' read committed',
                     'note': 'Nothing holds the gap between the read and the write, a textbook race\n'
                             '\n'
                             'We read stock, compare it, subtract and save, with no lock anywhere in between. The '
                             'isolation level is read committed as well, so two requests read the same number and '
                             'each takes one off. One unit of stock plus two requests means both pass and we land at '
                             '-1.'}},
           {'t': '2026-09-10 14:35',
            'declare': {'kind': 'open',
                        's': 'We have not picked which of the three ways to block this we use\n'
                             '\n'
                             '(1) Lock the row with SELECT FOR UPDATE: certain, but a popular product piles up '
                             'requests and the wait gets long. (2) A single conditional UPDATE (subtract only when '
                             'stock >= qty): atomic and fast with no lock. (3) Hold stock separately and reserve up '
                             'front: a big change, but it may be the right one for flash sales. We decide once we '
                             'see how big sale traffic actually is.',
                        'by': 'agent'}},
           {'t': '2026-09-10 14:45',
            'declare': {'kind': 'next',
                        's': 'Hold sale traffic up against the three stock approaches and pick one\n'
                             '\n'
                             '(a) Pull requests per second from the 9/9 sale and how concentrated they were per '
                             'product. (b) Replay the same load against the conditional UPDATE and see whether stock '
                             'still goes negative. (c) Pick one of the three from what that shows, then fix the '
                             'three negative stock rows by hand.',
                        'owner': 'claude',
                        'by': 'user'}}]}

THREADS["B1"] = {'repo': 'onstore/backend',
 'start': '2026-09-03 09:30',
 'focus': 'Decide whether coupons are allowed to stack\n'
          '\n'
          'Marketing is putting together the Chuseok promo and asked whether it can be used alongside the\n'
          'birthday coupon. Today the code neither blocks stacking nor allows it on purpose, it just happens.\n'
          'Pick a policy, then make the code enforce it.',
 'steps': [{'t': '2026-09-03 10:50',
            'turn': {'kind': 'finding',
                     'ev': 'sql> SELECT cnt, count(*) AS orders, round(avg(discount)) AS avg_discount FROM (\n'
                           '       SELECT order_id, count(*) AS cnt, sum(amount) AS discount\n'
                           '       FROM order_coupons GROUP BY order_id) t GROUP BY cnt ORDER BY cnt;\n'
                           ' cnt | orders | avg_discount\n'
                           '-----+--------+--------------\n'
                           '   1 |  22841 |         3200\n'
                           '   2 |    918 |         9700\n'
                           '   3 |     41 |        24500\n'
                           '\n'
                           'sql> SELECT max(discount_rate) FROM (\n'
                           '       SELECT order_id, sum(amount)::float / max(total) AS discount_rate\n'
                           '       FROM order_coupons c JOIN orders o ON o.id = c.order_id GROUP BY order_id) t;\n'
                           ' max\n'
                           '------\n'
                           ' 0.94',
                     'note': '959 orders already stacked coupons and one of them went out at 94% off\n'
                             '\n'
                             'Nothing has ever blocked it, so it is already happening. The 41 orders that stacked '
                             'three coupons gave away 24,500 KRW on average, and one order came out 94% off its own '
                             'total. Leave the policy unwritten and the Chuseok promo widens this hole the day it '
                             'ships.'}},
           {'t': '2026-09-03 14:20',
            'turn': {'kind': 'thought',
                     'ev': "Marketing's position (notes from the 2026-09-03 meeting)\n"
                           '\n'
                           '- Allowing stacking turns coupon collecting into a game and brings people back. Two '
                           'competitors allow up to 2.\n'
                           '- But a birthday coupon (flat 5,000 KRW) on top of a seasonal one (20% off) leaves '
                           'small-basket orders nearly free.\n'
                           '- Settlement team: stacked orders throw the fee math off in the payout reconciliation, '
                           'so it gets fixed by hand every month-end.',
                     'note': 'Allowing it lifts repeat visits but leaves settlement a manual fix every month\n'
                             '\n'
                             'Marketing wants it open, settlement wants it shut. The damage only gets large where a '
                             'percentage coupon meets a flat-amount one, so the right call for the CEO is between '
                             "'one coupon, the best value' and 'up to two'."}},
           {'t': '2026-09-04 10:00',
            'decide': {'s': 'Coupons do not stack; only the single best coupon applies',
                       'why': 'Reconciling settlement by hand every month costs more than we gain\n'
                              '\n'
                              'The repeat visits stacking buys are worth less than the fee reconciliation someone '
                              'redoes by hand every month, plus the risk of another 94%-off order. So the customer '
                              'is not the one who loses out: when they hold several coupons we pick the one that '
                              'helps them most, automatically.',
                       'by': 'user'}},
           {'t': '2026-09-04 10:15',
            'define': {'term': 'best coupon',
                       'meaning': 'The one coupon that leaves the lowest amount to pay\n'
                                  '\n'
                                  'Percentage and flat-amount coupons are mixed, so face value does not compare '
                                  'them. Apply each one to the order total and keep whichever leaves the smallest '
                                  'amount due. On a tie, use the one that expires first.'}},
           {'t': '2026-09-04 11:30',
            'commit': {'m': 'Limit an order to one coupon and pick the best coupon',
                       'files': {'pricing/coupon.py': 'def pick_best(order_total, coupons):\n'
                                                      '    # The one that leaves the lowest amount due. On a tie, '
                                                      'the one expiring first.\n'
                                                      '    return min(coupons, key=lambda c: (apply(order_total, c), '
                                                      'c.expires_at))\n'}}},
           {'t': '2026-09-04 15:40',
            'close': 'One coupon per order, and the best one is picked automatically\n'
                     '\n'
                     '959 orders had already stacked coupons and one went out at 94% off. Marketing argued for '
                     'opening it up, but the settlement that goes wrong every month costs more, so we shut it. In '
                     'exchange, a customer holding several coupons gets the best one applied without asking. The 959 '
                     'orders already out the door are not repriced.'}]}

THREADS["B2"] = {'repo': 'onstore/backend',
 'start': '2026-09-05 09:20',
 'focus': 'Pull the scattered discount math into the order service\n'
          '\n'
          'Cart, orders, and settlement each work out their own discount and land on different totals for the\n'
          'same order. Now that the coupon policy is settled (previous thread), the math moves to one place so\n'
          'all three read the same number.',
 'steps': [{'t': '2026-09-05 11:00',
            'turn': {'kind': 'finding',
                     'ev': '$ grep -rn "discount" --include="*.py" cart/ orders/ settlement/ | grep -v test | wc -l\n'
                           '34\n'
                           '\n'
                           '$ python -m tools.compare_pricing --sample 500\n'
                           'cart vs order differ:       31 / 500\n'
                           'order vs settlement differ: 44 / 500\n'
                           'all three differ:            7 / 500\n'
                           'largest gap: 2,400 KRW (ORD-9120, cart 18,600 / order 18,600 / settlement 21,000)\n'
                           '\n'
                           'where they diverge:\n'
                           '  - whether shipping is part of the discount base (cart excludes it, settlement includes '
                           'it)\n'
                           '  - rounding direction on the won (orders floors, settlement rounds half up)',
                     'note': '44 of 500 orders come out different in orders and settlement\n'
                             '\n'
                             'All three places decide for themselves whether shipping counts and which way to round. '
                             'The worst gap is 2,400 KRW. The math is spread across 34 spots, so fixing one leaves '
                             'the rest behind.'}},
           {'t': '2026-09-05 15:30',
            'decide': {'s': 'Only the pricing module in the order service computes discounts',
                       'why': 'Three separate implementations disagreed on 44 orders\n'
                              '\n'
                              'Cart and settlement stop computing and read the number the order service produced. '
                              'Shipping stays out of the discount base and rounding is always down. When a rule '
                              'changes there has to be exactly one place to change it.',
                       'by': 'user'}},
           {'t': '2026-09-07 16:40',
            'commit': {'m': 'Move price math into the pricing module and pin the rules',
                       'files': {'pricing/calculator.py': '# Shipping stays out of the discount base. Round down.\n'
                                                          'def price(order):\n'
                                                          '    base = sum(i.price * i.qty for i in order.items)\n'
                                                          '    coupon = pick_best(base, order.coupons)\n'
                                                          '    return floor_won(base - apply(base, coupon)) + '
                                                          'order.shipping_fee\n'}}},
           {'t': '2026-09-08 14:10',
            'commit': {'m': 'Have cart and settlement read the pricing result',
                       'files': {'cart/view.py': 'total = pricing.price(draft_order)   # never computed here\n',
                                 'settlement/report.py': 'amount = order.paid_amount           # take what the order '
                                                         'settled on\n'}}},
           {'t': '2026-09-09 11:20',
            'turn': {'kind': 'verified',
                     'ev': '$ python -m tools.compare_pricing --sample 500\n'
                           'cart vs order differ:       0 / 500\n'
                           'order vs settlement differ: 0 / 500\n'
                           '\n'
                           '$ pytest tests/pricing -q\n'
                           '................................................ 48 passed in 2.31s\n'
                           '\n'
                           '$ grep -rn "discount" --include="*.py" cart/ orders/ settlement/ | grep -v test | wc -l\n'
                           '9',
                     'note': 'All three agree now and the math dropped from 34 spots to 9\n'
                             '\n'
                             'Zero mismatches across the 500-order sample. The 9 remaining hits are display strings '
                             'and test fixtures, not math.'}},
           {'t': '2026-09-09 11:45',
            'close': 'Discount math now lives in the order service and the gaps went to zero\n'
                     '\n'
                     'Cart, orders, and settlement each ran their own math, 44 of 500 orders disagreed, and the '
                     'worst was off by 2,400 KRW. Each place had picked its own answer on shipping and on rounding. '
                     'The math now lives in the pricing module alone, with shipping excluded and rounding down. '
                     'Discount code went from 34 spots to 9.'}]}

THREADS["B3"] = {'repo': 'onstore/backend',
 'start': '2026-09-11 10:10',
 'focus': 'Raise the free shipping threshold from 20,000 to 30,000\n'
          '\n'
          'Carrier rates went up, so at a 20,000 threshold there are now orders that cost more to ship than\n'
          "they make. We want the threshold higher, but first let's see how many orders it actually touches.",
 'steps': [{'t': '2026-09-11 11:30',
            'turn': {'kind': 'finding',
                     'ev': 'sql> SELECT width_bucket(total, 0, 50000, 10) AS bucket,\n'
                           '            min(total) AS from_amt, max(total) AS to_amt, count(*)\n'
                           "     FROM orders WHERE created_at > now() - interval '30 days' GROUP BY 1 ORDER BY 1;\n"
                           ' bucket | from_amt | to_amt | count\n'
                           '--------+----------+--------+-------\n'
                           '      4 |    15012 |  19980 |  4102\n'
                           '      5 |    20000 |  24990 |  3881    <-- the band that ships free today\n'
                           '      6 |    25010 |  29970 |  2240\n'
                           '      7 |    30000 |  34980 |  1904\n'
                           '\n'
                           'Parcel rate: 3,000 -> 3,600 KRW as of 2026-08\n'
                           'Average margin on orders in the 20k-30k band: 4,100 KRW',
                     'note': '6,121 orders sit in the 20k-30k band with 500 KRW of margin left\n'
                             '\n'
                             'Over the last 30 days, 6,121 orders landed between 20,000 and 30,000 (buckets 5 and '
                             '6). Ship those free at 3,600 a parcel and the average 4,100 of margin comes down to '
                             '500. Move the threshold to 30,000 and the customer carries shipping in that band.'}},
           {'t': '2026-09-11 14:00',
            'decide': {'s': 'Raise the free shipping threshold to 30,000',
                       'why': 'Only 500 KRW of margin survives in the 20k-30k band\n'
                              '\n'
                              'The parcel rate going to 3,600 wipes out almost all the margin on those 6,121 orders. '
                              "Raise the threshold, but announce it two weeks ahead so regulars aren't blindsided, "
                              'and send a 3,000 coupon the week it kicks in to soften the jump.',
                       'by': 'user'}},
           {'t': '2026-09-12 10:30',
            'commit': {'m': 'Raise the free shipping threshold to 30000',
                       'files': {'pricing/shipping.py': 'FREE_SHIPPING_THRESHOLD = 30_000   # from 2026-09-26. was '
                                                        '20,000\n'
                                                        'APPLY_FROM = date(2026, 9, 26)\n'}}},
           {'t': '2026-09-12 11:00',
            'turn': {'kind': 'verified',
                     'ev': '$ pytest tests/pricing/test_shipping.py -q\n'
                           '........................ 24 passed in 0.84s\n'
                           '\n'
                           '$ python -m tools.preview_shipping --date 2026-09-26 --sample 200\n'
                           'free shipping applied: 84 / 200 (138 / 200 under the old threshold)\n'
                           'average shipping paid by customer: 1,970 KRW (was 940)',
                     'note': 'On the start date free shipping drops from 138 orders to 84\n'
                             '\n'
                             'The date gate holds, so anything before September 26 still prices at the old '
                             'threshold. In a 200-order sample free shipping falls to 84 and the shipping a customer '
                             'pays averages 1,970.'}},
           {'t': '2026-09-12 11:20',
            'close': 'Free shipping threshold goes to 30,000, effective September 26\n'
                     '\n'
                     'The parcel rate moving to 3,600 had squeezed the margin on 6,121 orders in the 20k-30k band '
                     'down to 500. The threshold goes to 30,000, but it only takes effect two weeks out so there is '
                     'time to announce it, and a 3,000 coupon goes out that same week.'}]}
