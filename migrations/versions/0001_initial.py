"""Frozen initial schema; do not replace with mutable application metadata."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = (
    "\nCREATE TABLE users (\n\tid VARCHAR(32) NOT NULL, \n\tdiscord_id VARCHAR NOT NULL, \n\tname VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (discord_id)\n)\n\n",
    "\nCREATE TABLE workspaces (\n\tid VARCHAR(32) NOT NULL, \n\tguild_id VARCHAR NOT NULL, \n\ttimezone VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (guild_id)\n)\n\n",
    "\nCREATE TABLE memberships (\n\tid VARCHAR(32) NOT NULL, \n\tworkspace_id VARCHAR(32) NOT NULL, \n\tuser_id VARCHAR(32) NOT NULL, \n\ttimezone VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (workspace_id, user_id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n",
    "\nCREATE TABLE action_requests (\n\tid VARCHAR(32) NOT NULL, \n\towner_id VARCHAR(32) NOT NULL, \n\tsource_key VARCHAR NOT NULL, \n\tkind VARCHAR NOT NULL, \n\tpayload JSON NOT NULL, \n\tresult JSON, \n\tstatus VARCHAR NOT NULL, \n\texpires_at INTEGER NOT NULL, \n\tversion INTEGER DEFAULT '1' NOT NULL, \n\tcreated_at INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id) REFERENCES memberships (id), \n\tUNIQUE (source_key)\n)\n\n",
    "\nCREATE TABLE channel_bindings (\n\tid VARCHAR(32) NOT NULL, \n\tworkspace_id VARCHAR(32) NOT NULL, \n\tchannel_id VARCHAR NOT NULL, \n\tpurpose VARCHAR NOT NULL, \n\treply_mode VARCHAR NOT NULL, \n\tprivate_owner_id VARCHAR(32), \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id), \n\tUNIQUE (channel_id), \n\tFOREIGN KEY(private_owner_id) REFERENCES memberships (id)\n)\n\n",
    "\nCREATE TABLE ledgers (\n\tid VARCHAR(32) NOT NULL, \n\towner_id VARCHAR(32) NOT NULL, \n\tcurrency VARCHAR NOT NULL, \n\ttimezone VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id), \n\tFOREIGN KEY(owner_id) REFERENCES memberships (id)\n)\n\n",
    "\nCREATE TABLE audit_events (\n\tid VARCHAR(32) NOT NULL, \n\taction_id VARCHAR(32), \n\towner_id VARCHAR(32) NOT NULL, \n\tentity_type VARCHAR NOT NULL, \n\tentity_id VARCHAR NOT NULL, \n\toperation VARCHAR NOT NULL, \n\tbefore_data JSON, \n\tafter_data JSON, \n\tcreated_at INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(action_id) REFERENCES action_requests (id), \n\tFOREIGN KEY(owner_id) REFERENCES memberships (id)\n)\n\n",
    "\nCREATE TABLE categories (\n\tid VARCHAR(32) NOT NULL, \n\tledger_id VARCHAR(32) NOT NULL, \n\tname VARCHAR NOT NULL, \n\tkind VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (ledger_id, kind, name), \n\tFOREIGN KEY(ledger_id) REFERENCES ledgers (id)\n)\n\n",
    "\nCREATE TABLE conversations (\n\tid VARCHAR(32) NOT NULL, \n\tbinding_id VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (binding_id), \n\tFOREIGN KEY(binding_id) REFERENCES channel_bindings (id)\n)\n\n",
    "\nCREATE TABLE reminders (\n\tid VARCHAR(32) NOT NULL, \n\towner_id VARCHAR(32) NOT NULL, \n\taction_id VARCHAR(32) NOT NULL, \n\ttitle VARCHAR NOT NULL, \n\ttimezone VARCHAR NOT NULL, \n\tlocal_time VARCHAR(5) NOT NULL, \n\trecurrence VARCHAR NOT NULL, \n\tweekdays JSON NOT NULL, \n\tnext_due INTEGER, \n\tchannel_id VARCHAR NOT NULL, \n\tmode VARCHAR NOT NULL, \n\tmax_sends INTEGER NOT NULL, \n\tinterval_seconds INTEGER NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id) REFERENCES memberships (id), \n\tUNIQUE (action_id), \n\tFOREIGN KEY(action_id) REFERENCES action_requests (id)\n)\n\n",
    "CREATE INDEX ix_reminders_due ON reminders (status, next_due)",
    "\nCREATE TABLE memory_items (\n\tid VARCHAR(32) NOT NULL, \n\tconversation_id VARCHAR(32) NOT NULL, \n\towner_id VARCHAR(32), \n\tkind VARCHAR NOT NULL, \n\tcontent TEXT NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tcreated_at INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(conversation_id) REFERENCES conversations (id), \n\tFOREIGN KEY(owner_id) REFERENCES memberships (id)\n)\n\n",
    "\nCREATE TABLE messages (\n\tid VARCHAR(32) NOT NULL, \n\tconversation_id VARCHAR(32) NOT NULL, \n\tauthor_id VARCHAR NOT NULL, \n\tauthor_name VARCHAR NOT NULL, \n\tdiscord_id VARCHAR NOT NULL, \n\trole VARCHAR NOT NULL, \n\tcontent TEXT NOT NULL, \n\tsent_at INTEGER NOT NULL, \n\tdeleted_at INTEGER, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(conversation_id) REFERENCES conversations (id), \n\tUNIQUE (discord_id)\n)\n\n",
    "CREATE INDEX ix_messages_conversation_time ON messages (conversation_id, sent_at)",
    "\nCREATE TABLE reminder_occurrences (\n\tid VARCHAR(32) NOT NULL, \n\treminder_id VARCHAR(32) NOT NULL, \n\tscheduled_at INTEGER NOT NULL, \n\teffective_at INTEGER NOT NULL, \n\tseries_version INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tsend_count INTEGER NOT NULL, \n\tnext_fire INTEGER, \n\tacknowledged_at INTEGER, \n\tcompleted_at INTEGER, \n\tlate INTEGER DEFAULT '0' NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (reminder_id, scheduled_at, series_version), \n\tFOREIGN KEY(reminder_id) REFERENCES reminders (id)\n)\n\n",
    "CREATE INDEX ix_occurrences_fire ON reminder_occurrences (status, next_fire)",
    "\nCREATE TABLE transactions (\n\tid VARCHAR(32) NOT NULL, \n\tledger_id VARCHAR(32) NOT NULL, \n\tcategory_id VARCHAR(32) NOT NULL, \n\taction_id VARCHAR(32) NOT NULL, \n\titem_index INTEGER NOT NULL, \n\tamount INTEGER NOT NULL, \n\tkind VARCHAR NOT NULL, \n\toccurred_on VARCHAR(10) NOT NULL, \n\tnote VARCHAR NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (action_id, item_index), \n\tCHECK (amount > 0), \n\tCHECK (kind IN ('income','expense')), \n\tFOREIGN KEY(ledger_id) REFERENCES ledgers (id), \n\tFOREIGN KEY(category_id) REFERENCES categories (id), \n\tFOREIGN KEY(action_id) REFERENCES action_requests (id)\n)\n\n",
    "CREATE INDEX ix_transactions_report ON transactions (ledger_id, occurred_on, status)",
    "\nCREATE TABLE deliveries (\n\tid VARCHAR(32) NOT NULL, \n\toccurrence_id VARCHAR(32) NOT NULL, \n\tevent_key VARCHAR NOT NULL, \n\trevision INTEGER NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tnext_attempt INTEGER NOT NULL, \n\tlease_until INTEGER, \n\tmessage_id VARCHAR, \n\terror_code VARCHAR, \n\tcreated_at INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(occurrence_id) REFERENCES reminder_occurrences (id), \n\tUNIQUE (event_key)\n)\n\n",
    "CREATE INDEX ix_deliveries_pending ON deliveries (status, next_attempt)",
    "\nCREATE TABLE memory_sources (\n\tmemory_id VARCHAR(32) NOT NULL, \n\tmessage_id VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (memory_id, message_id), \n\tFOREIGN KEY(memory_id) REFERENCES memory_items (id) ON DELETE CASCADE, \n\tFOREIGN KEY(message_id) REFERENCES messages (id)\n)\n\n",
)


def upgrade():
    for statement in SQL:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup instead.")
