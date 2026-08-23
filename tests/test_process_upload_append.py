"""追加上传须保留已有 classified，不得只返回本批。"""

from __future__ import annotations

from pathlib import Path

from src.workflow.pipeline import process_uploaded_files


def test_append_keeps_existing_docs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("src.workflow.pipeline.job_workdir", lambda _j: tmp_path)

    def fake_one(*, filename, content, folder, slot_hint, fingerprint, **_kw):  # noqa: ARG001
        path = Path(folder) / filename
        path.write_bytes(content)
        return {
            "file_name": filename,
            "file_fingerprint": fingerprint,
            "path": str(path),
            "doc_type": "contract",
            "fields": {"contractNo": "HT-NEW"},
        }

    monkeypatch.setattr("src.workflow.pipeline._process_one_file", fake_one)

    existing = [
        {
            "file_name": "old_so.pdf",
            "file_fingerprint": "fp-old-keep",
            "path": str(tmp_path / "old_so.pdf"),
            "doc_type": "order",
            "fields": {"orderNo": "SO25-0001"},
        }
    ]
    out = process_uploaded_files(
        "job-append",
        [{"filename": "new_ht.pdf", "content": b"new-doc-bytes"}],
        existing=existing,
        force=False,
    )
    names = {str(x.get("file_name")) for x in out}
    assert names == {"old_so.pdf", "new_ht.pdf"}
    assert any(x.get("fields", {}).get("orderNo") == "SO25-0001" for x in out)


def test_same_name_replaces_old(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("src.workflow.pipeline.job_workdir", lambda _j: tmp_path)

    def fake_one(*, filename, content, folder, slot_hint, fingerprint, **_kw):  # noqa: ARG001
        path = Path(folder) / filename
        path.write_bytes(content)
        return {
            "file_name": filename,
            "file_fingerprint": fingerprint,
            "path": str(path),
            "doc_type": "order",
            "fields": {"orderNo": "SO-NEW"},
        }

    monkeypatch.setattr("src.workflow.pipeline._process_one_file", fake_one)

    existing = [
        {
            "file_name": "same.pdf",
            "file_fingerprint": "fp-old-content",
            "path": str(tmp_path / "same.pdf"),
            "doc_type": "order",
            "fields": {"orderNo": "SO-OLD"},
        }
    ]
    out = process_uploaded_files(
        "job-replace",
        [{"filename": "same.pdf", "content": b"different-bytes"}],
        existing=existing,
        force=False,
    )
    assert len(out) == 1
    assert out[0]["fields"]["orderNo"] == "SO-NEW"
