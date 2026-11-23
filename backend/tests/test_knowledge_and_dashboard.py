from sqlalchemy import select

from app.agent.runner import build_messages, load_system_prompt
from app.models import KnowledgeItem, Lead, Message
from app.services import knowledge as kb
from tests.conftest import make_user


def test_chunking_splits_long_text_and_keeps_short_paragraphs():
    long = "word " * 600
    chunks = kb.chunk_text("Short paragraph one.\n\nShort paragraph two.\n\n" + long)
    assert chunks[0].startswith("Short paragraph one.") and "Short paragraph two." in chunks[0]
    assert all(len(c) <= 1000 for c in chunks) and len(chunks) >= 3


def test_search_ranks_the_relevant_chunk_first(db):
    kb.create_item(db, "Hours", "The showroom opens at 10am and closes at 6pm.")
    kb.create_item(db, "LED", "LED signboards come in warm white and cool white colours.")
    db.commit()
    top = kb.search(db, "what colours do LED signboards come in")[0][0]
    assert "colours" in top.content
    assert kb.search(db, "zzzz qqqq") == []


def test_archived_items_are_not_searched(db):
    item = kb.create_item(db, "Old", "Discontinued neon boards are no longer sold.")
    item.status = "archived"
    db.commit()
    assert kb.search(db, "neon boards") == []


def test_knowledge_api_roles_and_audit(client, db):
    _, owner = make_user(db, "owner")
    _, sales = make_user(db, "sales")
    r = client.post("/api/v1/knowledge", headers=sales, json={"title": "x", "text": "y"})
    assert r.status_code == 403
    r = client.post("/api/v1/knowledge", headers=owner, json={"title": "FAQ", "kind": "faq", "text": "We ship across India."})
    assert r.status_code == 201 and r.json()["data"]["chunks"] == 1
    item_id = r.json()["data"]["id"]
    hit = client.post("/api/v1/knowledge/test-search", headers=owner, json={"query": "ship India"}).json()["data"]
    assert hit and hit[0]["title"] == "FAQ"
    assert client.patch(f"/api/v1/knowledge/{item_id}", headers=owner, json={"text": "We ship worldwide."}).status_code == 200
    assert "worldwide" in client.post("/api/v1/knowledge/test-search", headers=owner, json={"query": "ship"}).json()["data"][0]["content"]
    assert client.delete(f"/api/v1/knowledge/{item_id}", headers=owner).status_code == 204
    assert client.post("/api/v1/knowledge/test-search", headers=owner, json={"query": "ship"}).json()["data"] == []
    from app.models import AuditLog

    assert {a.action for a in db.scalars(select(AuditLog)).all()} >= {"knowledge.create", "knowledge.update", "knowledge.archive"}


def test_auth_required_and_sales_sees_only_own_leads(client, db):
    assert client.get("/api/v1/leads").status_code == 401
    sales_user, sales = make_user(db, "sales")
    _, manager = make_user(db, "manager")
    mine = client.post("/api/v1/leads", headers=sales, json={"name": "Mine", "phone": "+919811111111"}).json()["data"]
    other = client.post("/api/v1/leads", headers=manager, json={"name": "Other", "phone": "+919822222222"}).json()["data"]
    seen = [l["name"] for l in client.get("/api/v1/leads", headers=sales).json()["data"]]
    assert seen == ["Mine"]
    assert client.get(f"/api/v1/leads/{other['id']}", headers=sales).status_code == 404
    assert client.get(f"/api/v1/leads/{mine['id']}", headers=sales).status_code == 200
    assert len(client.get("/api/v1/leads", headers=manager).json()["data"]) == 2


def test_manual_lead_dedupes_by_phone(client, db):
    _, owner = make_user(db, "owner")
    a = client.post("/api/v1/leads", headers=owner, json={"name": "A", "phone": "98765 43210"})
    b = client.post("/api/v1/leads", headers=owner, json={"name": "B", "phone": "+9898765 43210"})
    assert a.status_code == 201
    # different normalised numbers are different leads; the same number is not
    c = client.post("/api/v1/leads", headers=owner, json={"name": "C", "phone": "9876543210"})
    assert c.json()["meta"]["created"] is False


def test_message_history_always_starts_with_a_user_turn(db):
    lead = Lead(phone_e164="+919800000010")
    db.add(lead)
    db.flush()
    from app.models import Conversation

    conv = Conversation(lead_id=lead.id, channel="whatsapp", channel_thread_id="t")
    db.add(conv)
    db.flush()
    history = [
        Message(conversation_id=conv.id, lead_id=lead.id, direction="outbound", sender="ai", channel="whatsapp", body="Hello"),
        Message(conversation_id=conv.id, lead_id=lead.id, direction="inbound", sender="lead", channel="whatsapp", body="Hi"),
        Message(conversation_id=conv.id, lead_id=lead.id, direction="inbound", sender="lead", channel="whatsapp", body="Price?"),
    ]
    turns = build_messages(history, None)
    assert turns == [{"role": "user", "content": "Hi\nPrice?"}]


def test_system_prompt_contains_rules_and_lead_context():
    lead = Lead(name="Ravi", requirement="LED board")
    prompt = load_system_prompt(lead)
    assert "Aisha" in prompt and "Acme Signs" in prompt
    assert "Never offer, agree to or hint at a discount" in prompt
    assert "AI assistant" in prompt
    assert "name=Ravi" in prompt and "budget" in prompt.split("Still to learn:")[1]
