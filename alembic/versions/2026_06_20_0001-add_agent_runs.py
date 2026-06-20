from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260620_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("case_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("trace_id", sa.String(length=255), nullable=True),
        sa.Column("layer", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=False),
        sa.Column("run_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("prompt_chars", sa.Integer(), nullable=False),
        sa.Column("prompt_rough_tokens", sa.Integer(), nullable=False),
        sa.Column("response_chars", sa.Integer(), nullable=False),
        sa.Column("response_rough_tokens", sa.Integer(), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("safety_report", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("prompt_artifact_ref", sa.Text(), nullable=True),
        sa.Column("response_artifact_ref", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_runs_case_id", "agent_runs", ["case_id"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_trace_id", "agent_runs", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_trace_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_conversation_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_case_id", table_name="agent_runs")
    op.drop_table("agent_runs")
