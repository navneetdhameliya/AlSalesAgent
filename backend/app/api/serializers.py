from app.models import KnowledgeItem, Lead, Message


def lead_out(l: Lead) -> dict:
    return {
        "id": str(l.id),
        "name": l.name,
        "phone": l.phone_e164,
        "email": l.email,
        "company": l.company,
        "city": l.city,
        "source": l.source,
        "requirement": l.requirement,
        "budget": l.budget_text,
        "timeline": l.timeline_text,
        "location": l.location_text,
        "deal_size_estimate": l.deal_size_estimate,
        "stage": l.stage,
        "grade": l.grade,
        "grade_reason": l.grade_reason,
        "assigned_to": str(l.assigned_to) if l.assigned_to else None,
        "human_takeover": l.human_takeover,
        "ai_paused": l.ai_paused,
        "opted_out": l.opted_out_at is not None,
        "first_response_seconds": l.first_response_seconds,
        "last_inbound_at": l.last_inbound_at.isoformat() if l.last_inbound_at else None,
        "last_outbound_at": l.last_outbound_at.isoformat() if l.last_outbound_at else None,
        "tags": l.tags or [],
        "notes": l.notes,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }


def message_out(m: Message) -> dict:
    return {
        "id": str(m.id),
        "direction": m.direction,
        "sender": m.sender,
        "channel": m.channel,
        "body": m.body,
        "status": m.status,
        "error": m.error,
        "is_quote": m.is_quote,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def knowledge_out(k: KnowledgeItem, with_text: bool = False) -> dict:
    out = {
        "id": str(k.id),
        "title": k.title,
        "kind": k.kind,
        "source": k.source,
        "status": k.status,
        "chunks": len(k.chunks),
        "updated_at": k.updated_at.isoformat() if k.updated_at else None,
    }
    if with_text:
        out["text"] = k.raw_text
    return out
