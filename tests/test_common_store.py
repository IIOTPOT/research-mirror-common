from __future__ import annotations

import sqlite3

from research_mirror_common.store import CommonStore


def test_common_store_schema_init_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "research_mirror.db"

    CommonStore(db_path, source_id="alfa").close()
    store = CommonStore(db_path, source_id="alfa")

    tables = {
        row["name"]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "bot_state",
        "materials",
        "deliveries",
        "comments",
        "summaries",
        "subscribers",
        "migration_runs",
    } <= tables
    assert store.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_delivery_dedupe_is_namespaced_by_source_and_channel(tmp_path) -> None:
    db_path = tmp_path / "research_mirror.db"
    alfa = CommonStore(db_path, source_id="alfa")
    rencap = CommonStore(db_path, source_id="rencap")

    alfa.record_delivery(
        chat_id="-100alfa",
        material_uid="123",
        message_id=10,
        document_message_id=11,
        content_hash="hash-a",
    )
    rencap.record_delivery(
        chat_id="-100rencap",
        report_uid="123",
        message_id=20,
        document_message_id=21,
        content_hash="hash-r",
    )

    assert alfa.was_delivered(chat_id="-100alfa", material_uid="123")
    assert not alfa.was_delivered(chat_id="-100rencap", material_uid="123")
    assert rencap.was_delivered(chat_id="-100rencap", report_uid="123")
    assert alfa.get_delivery(chat_id="-100alfa", material_uid="123")["message_id"] == 10
    assert rencap.get_delivery(chat_id="-100rencap", report_uid="123")["message_id"] == 20


def test_article_and_comment_thread_fields_survive_roundtrip(tmp_path) -> None:
    store = CommonStore(tmp_path / "research_mirror.db", source_id="mozgovik")

    store.record_article(
        external_id="129",
        title="Title",
        url="https://example.test/129",
        author="Author",
        published_at="2026-05-20T10:00:00+03:00",
        channel_message_id=101,
        discussion_chat_id="-100discussion",
        message_thread_id=202,
        discussion_message_id=303,
        content_hash="article-hash",
    )
    store.record_comment(
        external_id="c1",
        post_external_id="129",
        parent_external_id="p0",
        author="Commenter",
        discussion_message_id=404,
        content_hash="comment-hash",
    )

    article = store.get_article_message("129")
    comment = store.get_comment("c1")

    assert article["discussion_chat_id"] == "-100discussion"
    assert article["message_thread_id"] == 202
    assert article["discussion_message_id"] == 303
    assert comment["post_external_id"] == "129"
    assert comment["parent_external_id"] == "p0"
    assert comment["discussion_message_id"] == 404


def test_euler_statuses_subscribers_and_retry_queries_survive(tmp_path) -> None:
    store = CommonStore(tmp_path / "research_mirror.db", source_id="euler")

    store.subscribe_chat(chat_id=7, chat_type="private", username="u", first_name="F")
    store.record_delivery(
        chat_id=7,
        publication_uid="pub-1",
        status="summary_only",
        summary_message_id=1001,
        document_message_id=None,
        error=None,
        material_json={"uid": "pub-1"},
    )
    store.record_delivery(
        chat_id=7,
        publication_uid="pub-2",
        status="summary_pending",
        summary_message_id=1002,
        document_message_id=None,
        error=None,
        material_json={"uid": "pub-2"},
    )

    assert [subscriber.chat_id for subscriber in store.list_active_subscribers()] == [7]
    assert store.was_delivered(chat_id=7, publication_uid="pub-1")
    assert store.get_delivery_summary_message_id(chat_id=7, publication_uid="pub-2") == 1002
    assert [row["publication_uid"] for row in store.list_retryable_deliveries(chat_ids=[7])] == ["pub-1"]
    assert [row["publication_uid"] for row in store.list_pending_summary_deliveries(chat_ids=[7])] == ["pub-2"]


def test_common_store_can_open_existing_sqlite_without_losing_rows(tmp_path) -> None:
    db_path = tmp_path / "research_mirror.db"
    store = CommonStore(db_path, source_id="d8")
    store.record_article(
        external_id="d8-1",
        title="D8 title",
        url="https://d8.test/1",
        published_at="2026-05-20T00:00:00Z",
        content_hash="h1",
        status="delivered",
        channel_message_id=77,
        last_error=None,
        material_topic="russia_related",
    )
    store.close()

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT count(*) FROM materials WHERE source_id = 'd8'").fetchone()[0]
    assert count == 1

    reopened = CommonStore(db_path, source_id="d8")
    assert reopened.is_article_delivered("d8-1", "h1")
