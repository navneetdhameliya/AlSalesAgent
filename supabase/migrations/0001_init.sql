-- Generated from backend/app/models.py. Do not edit by hand; regenerate instead.

CREATE TABLE audit_logs (
	id UUID NOT NULL, 
	at TIMESTAMP WITH TIME ZONE NOT NULL, 
	actor_id UUID, 
	action VARCHAR(60) NOT NULL, 
	target_type VARCHAR(40) NOT NULL, 
	target_id VARCHAR(60), 
	before JSONB, 
	after JSONB, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_audit_logs_at ON audit_logs (at);
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

CREATE TABLE opt_outs (
	id UUID NOT NULL, 
	phone_e164 VARCHAR(20), 
	email VARCHAR(320), 
	channel VARCHAR(20) NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_opt_outs_email ON opt_outs (email);
CREATE INDEX ix_opt_outs_phone_e164 ON opt_outs (phone_e164);
ALTER TABLE opt_outs ENABLE ROW LEVEL SECURITY;

CREATE TABLE profiles (
	id UUID NOT NULL, 
	full_name VARCHAR(200) NOT NULL, 
	email VARCHAR(320), 
	phone_e164 VARCHAR(20), 
	role VARCHAR(20) NOT NULL, 
	whatsapp_alerts_enabled BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

CREATE TABLE settings (
	key VARCHAR(80) NOT NULL, 
	value JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (key)
);

ALTER TABLE settings ENABLE ROW LEVEL SECURITY;

CREATE TABLE webhook_events (
	id UUID NOT NULL, 
	provider VARCHAR(30) NOT NULL, 
	event_id VARCHAR(200) NOT NULL, 
	payload JSONB NOT NULL, 
	processed_at TIMESTAMP WITH TIME ZONE, 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_webhook_event UNIQUE (provider, event_id)
);

ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;

CREATE TABLE knowledge_items (
	id UUID NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	source VARCHAR(10) NOT NULL, 
	file_key VARCHAR(500), 
	raw_text TEXT NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	updated_by UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(updated_by) REFERENCES profiles (id)
);

ALTER TABLE knowledge_items ENABLE ROW LEVEL SECURITY;

CREATE TABLE leads (
	id UUID NOT NULL, 
	name VARCHAR(200), 
	phone_e164 VARCHAR(20), 
	email VARCHAR(320), 
	company VARCHAR(200), 
	city VARCHAR(120), 
	country VARCHAR(80), 
	source VARCHAR(30) NOT NULL, 
	source_detail VARCHAR(300), 
	requirement TEXT, 
	budget_text VARCHAR(200), 
	budget_amount FLOAT, 
	budget_currency VARCHAR(8), 
	timeline_text VARCHAR(200), 
	location_text VARCHAR(200), 
	deal_size_estimate FLOAT, 
	lead_type VARCHAR(20) NOT NULL, 
	stage VARCHAR(20) NOT NULL, 
	grade VARCHAR(12) NOT NULL, 
	grade_reason TEXT, 
	score_signals JSONB NOT NULL, 
	assigned_to UUID, 
	human_takeover BOOLEAN NOT NULL, 
	ai_paused BOOLEAN NOT NULL, 
	opted_out_at TIMESTAMP WITH TIME ZONE, 
	opt_out_channel VARCHAR(20), 
	first_reply_at TIMESTAMP WITH TIME ZONE, 
	first_response_seconds INTEGER, 
	last_inbound_at TIMESTAMP WITH TIME ZONE, 
	last_outbound_at TIMESTAMP WITH TIME ZONE, 
	next_followup_at TIMESTAMP WITH TIME ZONE, 
	followup_step INTEGER NOT NULL, 
	tags JSONB NOT NULL, 
	notes TEXT, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(assigned_to) REFERENCES profiles (id)
);

CREATE INDEX ix_leads_assigned_to ON leads (assigned_to);
CREATE INDEX ix_leads_email ON leads (email);
CREATE INDEX ix_leads_grade ON leads (grade);
CREATE INDEX ix_leads_next_followup_at ON leads (next_followup_at);
CREATE INDEX ix_leads_phone_e164 ON leads (phone_e164);
CREATE INDEX ix_leads_stage ON leads (stage);
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

CREATE TABLE conversations (
	id UUID NOT NULL, 
	lead_id UUID NOT NULL, 
	channel VARCHAR(20) NOT NULL, 
	channel_thread_id VARCHAR(200), 
	status VARCHAR(10) NOT NULL, 
	last_message_at TIMESTAMP WITH TIME ZONE, 
	running_summary TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_conversation_thread UNIQUE (channel, channel_thread_id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id)
);

CREATE INDEX ix_conversations_lead_id ON conversations (lead_id);
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

CREATE TABLE knowledge_chunks (
	id UUID NOT NULL, 
	knowledge_item_id UUID NOT NULL, 
	chunk_index INTEGER NOT NULL, 
	content TEXT NOT NULL, 
	token_count INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items (id)
);

CREATE INDEX ix_knowledge_chunks_knowledge_item_id ON knowledge_chunks (knowledge_item_id);
ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;

CREATE TABLE ai_runs (
	id UUID NOT NULL, 
	lead_id UUID NOT NULL, 
	conversation_id UUID, 
	trigger VARCHAR(30) NOT NULL, 
	model VARCHAR(80) NOT NULL, 
	prompt_version VARCHAR(40) NOT NULL, 
	input_tokens INTEGER NOT NULL, 
	output_tokens INTEGER NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	tool_calls JSONB NOT NULL, 
	retrieved_chunk_ids JSONB NOT NULL, 
	decision VARCHAR(30) NOT NULL, 
	guardrail_flags JSONB NOT NULL, 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
);

CREATE INDEX ix_ai_runs_lead_id ON ai_runs (lead_id);
ALTER TABLE ai_runs ENABLE ROW LEVEL SECURITY;

CREATE TABLE messages (
	id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	lead_id UUID NOT NULL, 
	direction VARCHAR(10) NOT NULL, 
	sender VARCHAR(10) NOT NULL, 
	channel VARCHAR(20) NOT NULL, 
	body TEXT NOT NULL, 
	attachments JSONB NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	provider_message_id VARCHAR(200), 
	error TEXT, 
	approved_by UUID, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	ai_run_id UUID, 
	is_quote BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id), 
	FOREIGN KEY(approved_by) REFERENCES profiles (id)
);

CREATE INDEX ix_messages_conv_created ON messages (conversation_id, created_at);
CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);
CREATE INDEX ix_messages_lead_id ON messages (lead_id);
CREATE INDEX ix_messages_provider_message_id ON messages (provider_message_id);
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

