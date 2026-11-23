-- Day 2 additions. Apply after 0001_init.sql.
ALTER TABLE conversations ADD COLUMN subject VARCHAR(300);

CREATE TABLE products (
	id UUID NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	description TEXT, 
	unit VARCHAR(30) NOT NULL, 
	currency VARCHAR(8) NOT NULL, 
	attributes JSONB NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

ALTER TABLE products ENABLE ROW LEVEL SECURITY;

CREATE TABLE price_rules (
	id UUID NOT NULL, 
	product_id UUID NOT NULL, 
	rule_type VARCHAR(12) NOT NULL, 
	min_qty FLOAT NOT NULL, 
	max_qty FLOAT, 
	amount VARCHAR(40), 
	tiers JSONB NOT NULL, 
	formula VARCHAR(300), 
	note VARCHAR(300), 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);

CREATE INDEX ix_price_rules_product_id ON price_rules (product_id);
ALTER TABLE price_rules ENABLE ROW LEVEL SECURITY;

CREATE TABLE followups (
	id UUID NOT NULL, 
	lead_id UUID NOT NULL, 
	step INTEGER NOT NULL, 
	day_offset INTEGER NOT NULL, 
	due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	skip_reason VARCHAR(40), 
	sent_message_id UUID, 
	done_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id)
);

CREATE INDEX ix_followups_due_at ON followups (due_at);
CREATE INDEX ix_followups_lead_id ON followups (lead_id);
ALTER TABLE followups ENABLE ROW LEVEL SECURITY;

CREATE TABLE handoffs (
	id UUID NOT NULL, 
	lead_id UUID NOT NULL, 
	reason VARCHAR(300) NOT NULL, 
	summary TEXT NOT NULL, 
	assigned_to UUID, 
	status VARCHAR(12) NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id), 
	FOREIGN KEY(assigned_to) REFERENCES profiles (id)
);

CREATE INDEX ix_handoffs_lead_id ON handoffs (lead_id);
ALTER TABLE handoffs ENABLE ROW LEVEL SECURITY;

CREATE TABLE notifications (
	id UUID NOT NULL, 
	profile_id UUID, 
	lead_id UUID, 
	kind VARCHAR(30) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	body TEXT NOT NULL, 
	whatsapp_status VARCHAR(20), 
	read_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(profile_id) REFERENCES profiles (id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id)
);

CREATE INDEX ix_notifications_profile_id ON notifications (profile_id);
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

CREATE TABLE quotations (
	id UUID NOT NULL, 
	lead_id UUID NOT NULL, 
	conversation_id UUID, 
	product_id UUID, 
	product_name VARCHAR(200) NOT NULL, 
	quantity FLOAT NOT NULL, 
	attributes JSONB NOT NULL, 
	unit_price VARCHAR(40) NOT NULL, 
	total VARCHAR(40) NOT NULL, 
	currency VARCHAR(8) NOT NULL, 
	cover_message TEXT NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	message_id UUID, 
	decided_by UUID, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	reject_reason TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(decided_by) REFERENCES profiles (id)
);

CREATE INDEX ix_quotations_lead_id ON quotations (lead_id);
ALTER TABLE quotations ENABLE ROW LEVEL SECURITY;

