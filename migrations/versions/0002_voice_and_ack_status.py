"""Voice destination and repair v2.0 acknowledged one-time reminders."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("reminders", sa.Column("voice_channel_id", sa.String(), nullable=True))
    op.execute("""UPDATE reminders SET status='acknowledged', next_due=NULL
        WHERE recurrence='none' AND status='completed' AND EXISTS (
        SELECT 1 FROM reminder_occurrences o WHERE o.reminder_id=reminders.id
        AND o.series_version=reminders.version AND o.status='acknowledged')""")


def downgrade():
    raise RuntimeError("Restore a verified backup instead of destructive downgrade.")
