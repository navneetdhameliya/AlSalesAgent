-- Day 3 additions. Apply after 0002_day2.sql.
CREATE TABLE webhook_outbox (
	id UUID NOT NULL, 
	event VARCHAR(30) NOT NULL, 
	lead_id UUID, 
	payload JSONB NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_webhook_outbox_lead_id ON webhook_outbox (lead_id);
CREATE INDEX ix_webhook_outbox_status ON webhook_outbox (status);
ALTER TABLE webhook_outbox ENABLE ROW LEVEL SECURITY;

