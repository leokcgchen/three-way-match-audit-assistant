"""补传单据后分笔门禁必须作废；金额歧义列表默认不重扫。"""

from __future__ import annotations

from src.workflow.job_store import JobStore


def test_set_classified_clears_sample_fields_confirmed_on_new_doc():
    store = JobStore()
    job = store.create(title="reupload")
    jid = job["job_id"]
    store.set_goals(jid, ["gospd01030"])

    classified_v1 = [
        {
            "file_name": "SO25-0296_HT25-0296_01_销售合同.pdf",
            "doc_type": "contract",
            "file_fingerprint": "fp-contract-1",
            "fields": {"contractNo": "HT25-0296", "orderNo": "SO25-0296", "totalAmount": 73066.7},
        },
        {
            "file_name": "SO25-0296_HT25-0296_02_销售订单.pdf",
            "doc_type": "order",
            "file_fingerprint": "fp-order-1",
            "fields": {"orderNo": "SO25-0296", "contractNo": "HT25-0296", "totalAmount": 73066.7},
        },
    ]
    store.set_classified(jid, classified_v1)
    job = store.get(jid)
    assert job
    cid = job.get("active_chain_id") or "SO25-0296"
    # 模拟已确认字段（工作台绿灯）
    samples = dict(job.get("gospd_sample_results") or {})
    samples[cid] = {
        **dict(samples.get(cid) or {}),
        "fields_confirmed": True,
        "fields_confirm_sig": "sig",
        "matching_confirmed": True,
        "matching_confirm_sig": "msig",
    }
    store.update(
        jid,
        gospd_sample_results=samples,
        fields_confirmed=True,
        matching_confirmed=True,
        active_chain_id=cid,
    )
    job = store.get(jid)
    assert job and job.get("fields_confirmed") is True

    classified_v2 = list(classified_v1) + [
        {
            "file_name": "SO25-0296_HT25-0296_05_增值税发票.png",
            "doc_type": "invoice",
            "file_fingerprint": "fp-inv-1",
            "fields": {
                "orderNo": "SO25-0296",
                "contractNo": "HT25-0296",
                "totalAmount": 73066.7,
                "amount": 64660.8,
                "taxAmount": 8405.9,
            },
        }
    ]
    job2 = store.set_classified(jid, classified_v2)
    sample = (job2.get("gospd_sample_results") or {}).get(cid) or {}
    assert sample.get("fields_confirmed") is not True
    assert sample.get("matching_confirmed") is not True
    assert job2.get("fields_confirmed") is not True
    assert job2.get("matching_confirmed") is not True
