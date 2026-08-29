"""Cloud 2.3 DOI identity, translation versions, API-key lifecycle and provider profiles.

Revision ID: 0001_cloud_23
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_cloud_23"
down_revision = None
branch_labels = None
depends_on = None


def _columns(bind, table):
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(bind, table):
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {x["name"] for x in insp.get_indexes(table) if x.get("name")}


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "jobs" in tables:
        cols = _columns(bind, "jobs")
        if "document_doi" not in cols:
            op.add_column("jobs", sa.Column("document_doi", sa.String(length=255), nullable=True))
        if "ix_jobs_document_doi" not in _indexes(bind, "jobs"):
            op.create_index("ix_jobs_document_doi", "jobs", ["document_doi"], unique=False)

    if "client_api_keys" in tables:
        cols = _columns(bind, "client_api_keys")
        if "scopes" not in cols:
            op.add_column("client_api_keys", sa.Column("scopes", sa.JSON(), nullable=True))
        if "expires_at" not in cols:
            op.add_column("client_api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        if "rotated_from_id" not in cols:
            op.add_column("client_api_keys", sa.Column("rotated_from_id", sa.String(length=40), nullable=True))
        idx = _indexes(bind, "client_api_keys")
        if "ix_client_api_keys_expires_at" not in idx:
            op.create_index("ix_client_api_keys_expires_at", "client_api_keys", ["expires_at"], unique=False)
        if "ix_client_api_keys_rotated_from_id" not in idx:
            op.create_index("ix_client_api_keys_rotated_from_id", "client_api_keys", ["rotated_from_id"], unique=False)
        bind.execute(sa.text("UPDATE client_api_keys SET scopes = :scopes WHERE scopes IS NULL"), {"scopes": '["translate","lookup","download","account:read"]'})

    if "user_provider_profiles" in tables:
        cols = _columns(bind, "user_provider_profiles")
        if "is_custom" not in cols:
            op.add_column("user_provider_profiles", sa.Column("is_custom", sa.Boolean(), nullable=True, server_default=sa.text("0")))
        if "created_at" not in cols:
            op.add_column("user_provider_profiles", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        idx = _indexes(bind, "user_provider_profiles")
        if "ix_user_provider_profiles_is_custom" not in idx:
            op.create_index("ix_user_provider_profiles_is_custom", "user_provider_profiles", ["is_custom"], unique=False)

                                                                                  
                     
    if "translation_versions" not in tables:
        op.create_table(
            "translation_versions",
            sa.Column("id", sa.String(length=40), primary_key=True),
            sa.Column("document_doi", sa.String(length=255), nullable=False),
            sa.Column("lang_in", sa.String(length=32), nullable=False, server_default="en"),
            sa.Column("lang_out", sa.String(length=32), nullable=False, server_default="zh-CN"),
            sa.Column("pages_key", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("output_mode", sa.String(length=16), nullable=False, server_default="mono"),
            sa.Column("job_id", sa.String(length=40), nullable=False),
            sa.Column("created_by_user_id", sa.String(length=40), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("job_id", name="uq_translation_version_job"),
        )
        op.create_index("ix_translation_versions_document_doi", "translation_versions", ["document_doi"], unique=False)
        op.create_index("ix_translation_versions_job_id", "translation_versions", ["job_id"], unique=True)
        op.create_index("ix_translation_versions_created_by_user_id", "translation_versions", ["created_by_user_id"], unique=False)
        op.create_index("ix_translation_versions_created_at", "translation_versions", ["created_at"], unique=False)
        op.create_index("ix_translation_versions_doi_profile", "translation_versions", ["document_doi", "lang_in", "lang_out", "pages_key", "output_mode"], unique=False)

    if "user_document_bindings" in tables:
        col_meta = {c["name"]: c for c in sa.inspect(bind).get_columns("user_document_bindings")}
        cols = set(col_meta)
                                                                             
                                                                              
                                                                      
        needs_nullable_rebuild = any(
            name in col_meta and not bool(col_meta[name].get("nullable", True))
            for name in ("source_sha256", "bound_job_id")
        )
        if needs_nullable_rebuild:
            with op.batch_alter_table("user_document_bindings", recreate="always") as batch:
                if "document_doi" not in cols:
                    batch.add_column(sa.Column("document_doi", sa.String(length=255), nullable=True))
                if "current_version_id" not in cols:
                    batch.add_column(sa.Column("current_version_id", sa.String(length=40), nullable=True))
                if "source_sha256" in cols:
                    batch.alter_column("source_sha256", existing_type=sa.String(length=64), nullable=True)
                if "bound_job_id" in cols:
                    batch.alter_column("bound_job_id", existing_type=sa.String(length=40), nullable=True)
        else:
            if "document_doi" not in cols:
                op.add_column("user_document_bindings", sa.Column("document_doi", sa.String(length=255), nullable=True))
            if "current_version_id" not in cols:
                op.add_column("user_document_bindings", sa.Column("current_version_id", sa.String(length=40), nullable=True))
        idx = _indexes(bind, "user_document_bindings")
        if "ix_user_document_bindings_document_doi" not in idx:
            op.create_index("ix_user_document_bindings_document_doi", "user_document_bindings", ["document_doi"], unique=False)
        if "ix_user_document_bindings_current_version_id" not in idx:
            op.create_index("ix_user_document_bindings_current_version_id", "user_document_bindings", ["current_version_id"], unique=False)
        if "ix_user_document_binding_doi_profile" not in idx:
            op.create_index("ix_user_document_binding_doi_profile", "user_document_bindings", ["user_id", "document_doi", "lang_in", "lang_out", "pages_key", "output_mode"], unique=True)

                                                                                   
    if "jobs" in tables:
        idx = _indexes(bind, "jobs")
        if "ix_jobs_user_created" not in idx:
            op.create_index("ix_jobs_user_created", "jobs", ["user_id", "created_at"], unique=False)
        if "ix_jobs_doi_profile_status" not in idx:
            op.create_index("ix_jobs_doi_profile_status", "jobs", ["document_doi", "lang_in", "lang_out", "status"], unique=False)
    if "usage_events" in tables and "ix_usage_events_user_created" not in _indexes(bind, "usage_events"):
        op.create_index("ix_usage_events_user_created", "usage_events", ["user_id", "created_at"], unique=False)


def downgrade():
                                                                                
                                                                                    
    pass
