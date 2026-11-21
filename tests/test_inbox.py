"""Uploads and links become inbox documents the verifier can cite."""
from __future__ import annotations

import dataclasses
import io

import pytest

from desk import inbox
from desk.evidence import load_inbox_document

from conftest import events


@pytest.fixture
def doc_cfg(tmp_path, cfg):
    """A throwaway document tree so tests never write into the repo's inbox."""
    root = tmp_path / "repo"
    (root / "data" / "documents" / "inbox").mkdir(parents=True)
    (root / "data" / "reference").mkdir(parents=True)
    return dataclasses.replace(cfg, root=root)


def test_an_upload_becomes_citable_evidence(doc_cfg, conn):
    result = inbox.add_document(
        doc_cfg, title="Harbor letter agreement", body="Distributor waives any claim ...",
        parties=["Harbor Roasters", "Brewline Equipment Co."], doc_date="2025-06-30",
        relates_to=["CON-002"], uploaded_by="human_controller", work_item_id="WI-0007",
        conn=conn)

    assert result["doc_id"] == "DOC-UPL-0001"
    assert result["path"].startswith("data/documents/inbox/") and result["path"].endswith(".md")
    path, text, meta = load_inbox_document(result["path"], doc_cfg)
    assert meta["doc_id"] == "DOC-UPL-0001"
    assert meta["relates_to"] == ["CON-002"]
    assert meta["date"] == "2025-06-30"
    assert "Distributor waives any claim" in text
    logged = events(conn, action="inbox:upload", subject="WI-0007", allowed=True)
    assert logged and "DOC-UPL-0001" in logged[0]["reason"]


def test_doc_ids_and_filenames_never_collide(doc_cfg):
    first = inbox.add_document(doc_cfg, title="Same title", body="one")
    second = inbox.add_document(doc_cfg, title="Same title", body="two")
    assert (first["doc_id"], second["doc_id"]) == ("DOC-UPL-0001", "DOC-UPL-0002")
    assert first["path"] != second["path"]


def test_untitled_or_empty_uploads_are_refused(doc_cfg):
    with pytest.raises(inbox.InboxError):
        inbox.add_document(doc_cfg, title="  ", body="x")
    with pytest.raises(inbox.InboxError):
        inbox.add_document(doc_cfg, title="Empty", body="   ")
    with pytest.raises(inbox.InboxError):
        inbox.add_document(doc_cfg, title="Nothing", body=None, url=None)
    with pytest.raises(inbox.InboxError):
        inbox.add_document(doc_cfg, title="Bad date", body="x", doc_date="30/06/2025")


def test_only_http_links_are_fetched(doc_cfg):
    with pytest.raises(inbox.InboxError):
        inbox.add_document(doc_cfg, title="Local file", url="file:///etc/passwd")
    with pytest.raises(inbox.InboxError):
        inbox.fetch_url("ftp://example.com/x")


def test_a_fetched_page_is_stored_as_text_with_its_source(doc_cfg, monkeypatch):
    class FakeResponse(io.BytesIO):
        headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    html = (b"<html><head><style>p{}</style><script>alert(1)</script></head>"
            b"<body><h1>Notice</h1><p>Refunds &amp; interest<br>apply.</p></body></html>")
    monkeypatch.setattr(inbox.urllib.request, "urlopen", lambda req, timeout: FakeResponse(html))

    result = inbox.add_document(doc_cfg, title="CBP notice page", url="https://example.com/notice",
                                relates_to=["CON-001"])
    _path, text, meta = load_inbox_document(result["path"], doc_cfg)
    assert "alert" not in text and "<p>" not in text
    assert "Notice" in text and "Refunds & interest apply." in text
    assert meta["source_url"] == "https://example.com/notice"
    assert result["source_url"] == "https://example.com/notice"


def test_list_documents_sees_the_upload_and_the_rest(doc_cfg):
    (doc_cfg.root / "data" / "reference" / "policy.md").write_text("---\ndoc_id: POL-X\ntype: policy\n---\n# P\n")
    inbox.add_document(doc_cfg, title="Letter", body="text", relates_to=["CON-002"])
    listed = inbox.list_documents(doc_cfg)
    paths = {d["path"] for d in listed}
    assert "data/reference/policy.md" in paths
    upload = next(d for d in listed if d["folder"] == "data/documents")
    assert upload["doc_id"] == "DOC-UPL-0001" and upload["relates_to"] == ["CON-002"]
