import os
import uuid

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
os.environ["ENVIRONMENT"] = "development"
os.environ["BUSINESS_NAME"] = "Acme Signs"
os.environ["PERSONA_NAME"] = "Aisha"

import jwt
import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.agent import llm as llmmod
from app.channels import whatsapp as wa
from app.channels.webchat import broker
from app.config import get_settings
from app.db import Base
from app.main import app
from app.models import Profile


class FakeWhatsApp:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    def send_text(self, to, body):
        if self.fail:
            raise wa.ChannelError("boom")
        self.sent.append((to, body))
        return f"wamid.OUT{len(self.sent)}"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    Base.metadata.drop_all(dbmod.engine)
    Base.metadata.create_all(dbmod.engine)
    broker.clear()
    fake_wa = FakeWhatsApp()
    monkeypatch.setattr(wa, "whatsapp_client", fake_wa)
    fake_llm = llmmod.FakeLLM()
    llmmod.set_llm(fake_llm)
    yield
    llmmod.set_llm(None)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_wa():
    return wa.whatsapp_client


@pytest.fixture
def fake_llm():
    return llmmod.get_llm()


@pytest.fixture
def db():
    s = dbmod.SessionLocal()
    yield s
    s.close()


def make_user(db, role: str, name: str = "User") -> tuple[Profile, dict]:
    p = Profile(id=uuid.uuid4(), full_name=name, email=f"{uuid.uuid4().hex[:8]}@example.com", role=role)
    db.add(p)
    db.commit()
    token = jwt.encode({"sub": str(p.id), "aud": "authenticated"}, get_settings().supabase_jwt_secret, algorithm="HS256")
    return p, {"Authorization": f"Bearer {token}"}


def text(t: str) -> list[dict]:
    return [{"type": "text", "text": t}]


def tool(name: str, tool_input: dict, tid: str = "tu_1") -> list[dict]:
    return [{"type": "tool_use", "id": tid, "name": name, "input": tool_input}]


def wa_payload(msg_id: str, sender: str, body: str, name: str = "Ravi") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": sender, "profile": {"name": name}}],
                            "messages": [{"id": msg_id, "from": sender, "type": "text", "text": {"body": body}}],
                        }
                    }
                ]
            }
        ]
    }
