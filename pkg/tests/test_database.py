"""Tests for database operations."""

from kyo_mcp.database import (
    create_concept,
    create_link,
    get_all_links,
    get_concept_by_id,
    query_catalog,
    update_node_verified,
)


def test_duplicate_link_prevention():
    """INSERT OR IGNORE should prevent duplicate links from causing errors."""
    concept1 = {
        "id": "dup-src",
        "type": "concept",
        "title": "Dup Source",
        "description": "Source",
        "resource": None,
        "tags": [],
        "status": "stable",
        "verified": None,
        "sources": [],
    }
    concept2 = {
        "id": "dup-tgt",
        "type": "concept",
        "title": "Dup Target",
        "description": "Target",
        "resource": None,
        "tags": [],
        "status": "stable",
        "verified": None,
        "sources": [],
    }
    create_concept(concept1, "Source body")
    create_concept(concept2, "Target body")

    # First link
    create_link("dup-src", "dup-tgt", "references")

    # Second link with same parameters should not raise error
    create_link("dup-src", "dup-tgt", "references")

    links = get_all_links()
    # Should only have one link, not two
    assert len(links) == 1
    assert links[0]["relation_type"] == "references"


def test_query_catalog_type_and_search():
    """query_catalog should support both type and search term filters simultaneously."""
    concept1 = {
        "id": "combo-1",
        "type": "concept",
        "title": "Search Concept Alpha",
        "description": "Test",
        "resource": None,
        "tags": [],
        "status": "stable",
        "verified": None,
        "sources": [],
    }
    concept2 = {
        "id": "combo-2",
        "type": "concept",
        "title": "Search Concept Beta",
        "description": "Test",
        "resource": None,
        "tags": [],
        "status": "stable",
        "verified": None,
        "sources": [],
    }
    concept3 = {
        "id": "combo-3",
        "type": "dataset",
        "title": "Search Concept Gamma",
        "description": "Test",
        "resource": None,
        "tags": [],
        "status": "stable",
        "verified": None,
        "sources": [],
    }
    create_concept(concept1, "Body 1")
    create_concept(concept2, "Body 2")
    create_concept(concept3, "Body 3")

    results = query_catalog(type_filter="concept", search_term="Search")
    assert len(results) == 2
    assert all(r["type"] == "concept" for r in results)
    assert all("Search" in r["title"] for r in results)


def test_update_node_verified_idempotent():
    """Multiple verify calls with same actor should not create duplicates."""
    concept = {
        "id": "idempotent-test",
        "type": "concept",
        "title": "Idempotent Test",
        "description": "Testing verification",
        "resource": None,
        "tags": [],
        "status": "stable",
        "verified": None,
        "sources": [],
    }
    create_concept(concept, "Test body")

    update_node_verified("idempotent-test", {"by": "alice"})
    update_node_verified("idempotent-test", {"by": "alice"})
    update_node_verified("idempotent-test", {"by": "alice"})

    result = get_concept_by_id("idempotent-test")
    verified_list = result["verified"]

    # Should only have one entry, not three
    assert len(verified_list) == 1
    assert verified_list[0]["by"] == "human:alice"


def test_create_link_with_new_concept():
    """create_link should work even if concepts aren't in memory graph yet."""
    concept1 = {
        "id": "link-new-src",
        "type": "concept",
        "title": "New Source",
        "description": "Source",
        "resource": None,
        "tags": [],
        "status": "stable",
        "verified": None,
        "sources": [],
    }
    concept2 = {
        "id": "link-new-tgt",
        "type": "concept",
        "title": "New Target",
        "description": "Target",
        "resource": None,
        "tags": [],
        "status": "stable",
        "verified": None,
        "sources": [],
    }
    create_concept(concept1, "Source body")
    create_concept(concept2, "Target body")

    # This should not raise an error even though concepts aren't in memory graph
    create_link("link-new-src", "link-new-tgt", "references")

    links = get_all_links()
    assert len(links) == 1


class TestDatabase:
    """Test suite for database operations using the injected temp DB."""

    def test_create_and_query(self):
        concept = {
            "id": "test-node-001",
            "type": "concept",
            "title": "Test Concept",
            "description": "A test concept for unit testing",
            "resource": None,
            "tags": ["test"],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        create_concept(concept, "Test body")
        results = query_catalog(search_term="Test Concept")
        assert len(results) == 1
        assert results[0]["id"] == "test-node-001"

    def test_get_concept_by_id(self):
        concept = {
            "id": "test-node-002",
            "type": "concept",
            "title": "Get By ID Test",
            "description": "Testing get_concept_by_id",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        create_concept(concept, "Test body")
        result = get_concept_by_id("test-node-002")
        assert result is not None
        assert result["id"] == "test-node-002"
        assert result["title"] == "Get By ID Test"

    def test_get_nonexistent(self):
        result = get_concept_by_id("nonexistent-id")
        assert result is None

    def test_upsert_concept(self):
        concept = {
            "id": "upsert-test",
            "type": "concept",
            "title": "Upsert Test",
            "description": "Original description",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        create_concept(concept, "First body")
        updated_concept = dict(concept)
        updated_concept["description"] = "Updated description"
        create_concept(updated_concept, "Updated body")
        result = get_concept_by_id("upsert-test")
        assert result is not None
        assert result["description"] == "Updated description"

    def test_query_with_type_filter(self):
        concept = {
            "id": "type-test",
            "type": "dataset",
            "title": "Dataset Test",
            "description": "Testing type filter",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        create_concept(concept, "Test body")
        results = query_catalog(type_filter="dataset")
        assert len(results) == 1
        assert results[0]["id"] == "type-test"

    def test_create_link(self):
        concept1 = {
            "id": "link-src",
            "type": "concept",
            "title": "Source",
            "description": "Source node",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        concept2 = {
            "id": "link-tgt",
            "type": "concept",
            "title": "Target",
            "description": "Target node",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        create_concept(concept1, "Source body")
        create_concept(concept2, "Target body")
        create_link("link-src", "link-tgt", "references")
        results = query_catalog()
        assert len(results) == 2

    def test_update_verified(self):
        concept = {
            "id": "verify-test",
            "type": "concept",
            "title": "Verify Test",
            "description": "Testing verification",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        create_concept(concept, "Test body")
        update_node_verified("verify-test", {"by": "human:alice"})
        result = get_concept_by_id("verify-test")
        assert result["verified"] is not None
        assert len(result["verified"]) > 0
        assert "by" in result["verified"][0]

    def test_metadata_parsing(self):
        concept = {
            "id": "meta-test",
            "type": "concept",
            "title": "Metadata Test",
            "description": "Testing metadata",
            "resource": {"uri": "https://example.com"},
            "tags": ["test"],
            "status": "stable",
            "verified": None,
            "sources": [
                {"id": "src-1", "resource": "https://example.com", "title": "Doc"}
            ],
        }
        create_concept(concept, "Test body")
        result = get_concept_by_id("meta-test")
        assert result["resource"] == {"uri": "https://example.com"}

    def test_resource_string_handling(self):
        """Test that resource dict gets stored as string."""
        concept = {
            "id": "res-test",
            "type": "concept",
            "title": "Resource Test",
            "description": "Testing resource string",
            "resource": {"uri": "https://example.com"},
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        create_concept(concept, "Test body")
        result = get_concept_by_id("res-test")
        assert result["resource"] == {"uri": "https://example.com"}

    def test_get_all_links_empty(self):
        """get_all_links returns empty list when no links exist."""
        links = get_all_links()
        assert links == []

    def test_get_all_links(self):
        """get_all_links returns all persisted edges."""
        concept1 = {
            "id": "all-src",
            "type": "concept",
            "title": "All Src",
            "description": "Source",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        concept2 = {
            "id": "all-tgt",
            "type": "concept",
            "title": "All Tgt",
            "description": "Target",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        concept3 = {
            "id": "all-mid",
            "type": "concept",
            "title": "All Mid",
            "description": "Mid",
            "resource": None,
            "tags": [],
            "status": "stable",
            "verified": None,
            "sources": [],
        }
        create_concept(concept1, "Source body")
        create_concept(concept2, "Target body")
        create_concept(concept3, "Mid body")
        create_link("all-src", "all-mid", "references")
        create_link("all-mid", "all-tgt", "derives_from")

        links = get_all_links()
        assert len(links) == 2
        assert links[0]["relation_type"] == "references"
        assert links[1]["relation_type"] == "derives_from"


class TestRegressions:
    """Bugs confirmed against the pre-0.2 code."""

    def test_stale_after_and_extra_metadata_round_trip(self):
        create_concept(
            {
                "id": "fresh",
                "type": "concept",
                "stale_after": "2030-01-01",
                "metadata": {"owner": "team-a"},
            }
        )
        result = get_concept_by_id("fresh")
        assert result["stale_after"] == "2030-01-01"
        assert result["metadata"] == {"owner": "team-a"}

    def test_upsert_keeps_verification_history(self):
        create_concept({"id": "kept", "type": "concept", "title": "v1"})
        update_node_verified("kept", {"by": "alice", "at": "2024-01-01T00:00:00Z"})
        create_concept({"id": "kept", "type": "concept", "title": "v2"})
        result = get_concept_by_id("kept")
        assert result["title"] == "v2"
        assert [v["by"] for v in result["verified"]] == ["human:alice"]

    def test_upsert_refreshes_updated_at(self):
        from kyo_mcp.database import get_connection

        create_concept({"id": "ts", "type": "concept", "title": "v1"})
        conn = get_connection()
        conn.execute(
            "UPDATE knowledge_concepts SET updated_at = '2000-01-01' WHERE id = 'ts'"
        )
        conn.commit()
        create_concept({"id": "ts", "type": "concept", "title": "v2"})
        assert get_concept_by_id("ts")["updated_at"] != "2000-01-01"

    def test_insert_only_refuses_overwrite(self):
        import pytest
        from kyo_mcp.database import ConceptExistsError

        create_concept({"id": "once", "type": "concept", "title": "first"})
        with pytest.raises(ConceptExistsError):
            create_concept(
                {"id": "once", "type": "concept", "title": "second"},
                update_existing=False,
            )
        assert get_concept_by_id("once")["title"] == "first"

    def test_verified_prefix_not_doubled(self):
        create_concept({"id": "pfx", "type": "concept"})
        update_node_verified("pfx", {"by": "human:bob"})
        assert get_concept_by_id("pfx")["verified"][0]["by"] == "human:bob"

    def test_stored_concept_with_resource_validates(self):
        """A stored resource came back as a bare string, which OKFConcept
        rejects, so no node with a resource could be synced or exported."""
        from kyo_mcp.okf_schema import OKFConcept

        create_concept(
            {"id": "res", "type": "concept", "resource": {"uri": "https://x.test"}}
        )
        concept = OKFConcept.model_validate(get_concept_by_id("res"))
        assert concept.resource == {"uri": "https://x.test"}


class TestMigrations:
    def test_upgrades_pre_versioned_database(self, tmp_path):
        """A database created before PRAGMA user_version tracking (the
        original schema, with some rows) upgrades in place and its
        existing rows become searchable."""
        import sqlite3

        from kyo_mcp.database import MIGRATIONS, get_connection

        path = tmp_path / "old.db"
        old = sqlite3.connect(path)
        old.execute(
            "CREATE TABLE knowledge_concepts (id TEXT PRIMARY KEY, type TEXT NOT NULL, "
            "title TEXT, description TEXT, resource TEXT, status TEXT DEFAULT 'stable', "
            "tags TEXT, metadata TEXT, body_text TEXT, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, hindsight_synced_hash TEXT)"
        )
        old.execute(
            "INSERT INTO knowledge_concepts (id, type, title, tags, metadata) "
            "VALUES ('legacy', 'concept', 'Legacy Zebra', '[]', '{}')"
        )
        old.commit()
        old.close()

        conn = get_connection(path)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        results = query_catalog(search_term="zebra", db_path=path)
        assert [r["id"] for r in results] == ["legacy"]
