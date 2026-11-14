def _auth(client, email: str, name: str = "Member") -> str:
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "a-long-password", "display_name": name},
    )
    tokens = client.post("/api/v1/auth/login", json={"email": email, "password": "a-long-password"}).json()
    return tokens["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_knowledge_base_crud_and_roles(client) -> None:
    owner = _auth(client, "owner@example.com", "Owner")
    created = client.post(
        "/api/v1/knowledge-bases",
        headers=_headers(owner),
        json={"name": "Research", "description": "Notes"},
    )
    assert created.status_code == 201
    kb = created.json()
    assert kb["role"] == "owner"
    assert kb["document_count"] == 0
    assert kb["ready_count"] == 0
    assert kb["retrieval_config"]["rrf_k"] == 60
    kb_id = kb["id"]

    listed = client.get("/api/v1/knowledge-bases", headers=_headers(owner))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [kb_id]

    outsider = _auth(client, "outsider@example.com", "Outsider")
    hidden = client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=_headers(outsider))
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "not_found"

    viewer_token = _auth(client, "viewer@example.com", "Viewer")
    added = client.put(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=_headers(owner),
        json={"email": "viewer@example.com", "role": "viewer"},
    )
    assert added.status_code == 200
    assert added.json()["role"] == "viewer"

    seen = client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=_headers(viewer_token))
    assert seen.status_code == 200
    assert seen.json()["role"] == "viewer"

    forbidden = client.patch(
        f"/api/v1/knowledge-bases/{kb_id}",
        headers=_headers(viewer_token),
        json={"name": "Changed"},
    )
    assert forbidden.status_code == 403

    removed = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=_headers(viewer_token))
    assert removed.status_code == 403

    renamed = client.patch(
        f"/api/v1/knowledge-bases/{kb_id}",
        headers=_headers(owner),
        json={"description": "Revised"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["description"] == "Revised"

    last_owner = client.delete(
        f"/api/v1/knowledge-bases/{kb_id}/members/{added.json()['user_id']}",
        headers=_headers(outsider),
    )
    assert last_owner.status_code == 404

    owner_id = client.get("/api/v1/auth/me", headers=_headers(owner)).json()["id"]
    keep = client.delete(
        f"/api/v1/knowledge-bases/{kb_id}/members/{owner_id}",
        headers=_headers(owner),
    )
    assert keep.status_code == 409

    deleted = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=_headers(owner))
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    gone = client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=_headers(owner))
    assert gone.status_code == 404
    empty = client.get("/api/v1/knowledge-bases", headers=_headers(owner))
    assert empty.json()["items"] == []


def test_editor_and_viewer_membership_rules(client) -> None:
    owner = _auth(client, "kbowner@example.com", "Owner")
    created = client.post(
        "/api/v1/knowledge-bases",
        headers=_headers(owner),
        json={"name": "Team", "description": "Shared"},
    ).json()
    kb_id = created["id"]

    editor = _auth(client, "kbeditor@example.com", "Editor")
    viewer = _auth(client, "kbviewer@example.com", "Viewer")
    for email, role in (("kbeditor@example.com", "editor"), ("kbviewer@example.com", "viewer")):
        added = client.put(
            f"/api/v1/knowledge-bases/{kb_id}/members",
            headers=_headers(owner),
            json={"email": email, "role": role},
        )
        assert added.status_code == 200

    edited = client.patch(
        f"/api/v1/knowledge-bases/{kb_id}",
        headers=_headers(editor),
        json={"description": "Editor change"},
    )
    assert edited.status_code == 200
    assert edited.json()["description"] == "Editor change"

    editor_delete = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=_headers(editor))
    assert editor_delete.status_code == 403

    editor_member = client.put(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=_headers(editor),
        json={"email": "kbeditor@example.com", "role": "viewer"},
    )
    assert editor_member.status_code == 403

    viewer_member = client.put(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=_headers(viewer),
        json={"email": "kbviewer@example.com", "role": "owner"},
    )
    assert viewer_member.status_code == 403

    owner_delete = client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=_headers(owner))
    assert owner_delete.status_code == 200
    assert owner_delete.json()["deleted"] is True


def test_invalid_knowledge_base_cursor(client) -> None:
    token = _auth(client, "cursor@example.com")
    response = client.get("/api/v1/knowledge-bases?cursor=not-valid", headers=_headers(token))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_unauthenticated_knowledge_base_list(client) -> None:
    response = client.get("/api/v1/knowledge-bases")
    assert response.status_code == 401


def test_providers_are_not_ready(client) -> None:
    token = _auth(client, "settings@example.com")
    response = client.get("/api/v1/settings/providers", headers=_headers(token))
    assert response.status_code == 200
    body = response.json()
    assert body["embedding"]["ready"] is False
    assert body["reranker"]["ready"] is False
    assert body["language_model"]["ready"] is False
    assert "api_key" not in response.text
