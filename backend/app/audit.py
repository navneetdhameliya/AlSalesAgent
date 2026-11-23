import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit(
    db: Session,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type, target_id=target_id, before=before, after=after))
