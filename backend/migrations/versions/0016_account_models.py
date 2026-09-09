"""Account-owned chat providers; shared, administrator-managed Embedding.

Legacy personal settings are retained solely for rollback.
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _migrate_settings() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    providers = sa.Table("t_model_providers", metadata, autoload_with=connection)
    models = sa.Table("t_ai_models", metadata, autoload_with=connection)
    selections = sa.Table("t_user_chat_models", metadata, autoload_with=connection)
    global_selections = sa.Table("t_active_ai_models", metadata, autoload_with=connection)
    users = sa.Table("t_users", metadata, autoload_with=connection)
    legacy = sa.Table("t_user_llm_configs", metadata, autoload_with=connection)
    administrator = connection.execute(sa.select(users.c.id).where(users.c.role == "admin").order_by(users.c.id).limit(1)).scalar()
    chat_providers = connection.execute(sa.select(providers).where(providers.c.id.in_(
        sa.select(models.c.provider_id).where(models.c.kind == "chat")))).mappings().all()
    # Previously shared chat configuration belongs to the earliest administrator.
    # Preserve model IDs so historical/queued generation references remain valid.
    for provider in chat_providers:
        mixed = connection.execute(sa.select(models.c.id).where(
            models.c.provider_id == provider["id"], models.c.kind == "embedding")).first()
        if mixed:
            values = dict(provider)
            values.pop("id")
            values["user_id"] = administrator
            new_id = connection.execute(providers.insert().values(**values)).inserted_primary_key[0]
            connection.execute(models.update().where(models.c.provider_id == provider["id"], models.c.kind == "chat").values(provider_id=new_id))
        else:
            connection.execute(providers.update().where(providers.c.id == provider["id"]).values(user_id=administrator))
    selected = connection.execute(sa.select(global_selections.c.model_id).where(global_selections.c.kind == "chat")).scalar()
    if selected is not None and administrator is not None:
        connection.execute(selections.insert().values(user_id=administrator, model_id=selected))
    connection.execute(global_selections.delete().where(global_selections.c.kind == "chat"))
    for config in connection.execute(sa.select(legacy)).mappings().all():
        name = "个人对话模型"
        suffix = 1
        while connection.execute(sa.select(providers.c.id).where(providers.c.user_id == config["user_id"], providers.c.name == name)).first():
            name = "个人对话模型（%s）" % suffix
            suffix += 1
        provider_id = connection.execute(providers.insert().values(
            user_id=config["user_id"], name=name, base_url=config["base_url"],
            encrypted_api_key=config["encrypted_api_key"], allow_insecure_http=config["allow_insecure_http"],
            created_at=config["created_at"], updated_at=config["updated_at"],
        )).inserted_primary_key[0]
        model_id = connection.execute(models.insert().values(provider_id=provider_id, kind="chat", model_id=config["model_id"],
                                                             created_at=config["created_at"], updated_at=config["updated_at"])).inserted_primary_key[0]
        connection.execute(selections.delete().where(selections.c.user_id == config["user_id"]))
        connection.execute(selections.insert().values(user_id=config["user_id"], model_id=model_id))


def upgrade() -> None:
    offline = op.get_context().as_sql
    if not offline:
        connection = op.get_bind()
        has_chat = connection.execute(sa.text("SELECT id FROM t_ai_models WHERE kind = 'chat' LIMIT 1")).first()
        has_admin = connection.execute(sa.text("SELECT id FROM t_users WHERE role = 'admin' LIMIT 1")).first()
        if has_chat and not has_admin:
            raise RuntimeError("迁移已有对话模型需要至少一个系统管理员账户")
    constraint_name = "name"  # MySQL names an unnamed single-column UNIQUE after the column.
    if not offline:
        uniques = sa.inspect(op.get_bind()).get_unique_constraints("t_model_providers")
        constraint = next((item for item in uniques if item["column_names"] == ["name"]), None)
        constraint_name = (constraint["name"] or "uq_t_model_providers_name") if constraint else None
    with op.batch_alter_table("t_model_providers", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch:
        if constraint_name:
            batch.drop_constraint(constraint_name, type_="unique")
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_model_provider_user", "t_users", ["user_id"], ["id"], ondelete="CASCADE")
        batch.create_unique_constraint("uq_model_provider_user_name", ["user_id", "name"])
        batch.create_index("ix_t_model_providers_user_id", ["user_id"])
    op.create_table(
        "t_user_chat_models",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("t_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("t_ai_models.id", ondelete="CASCADE"), nullable=False, unique=True),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci",
    )
    if not offline:
        _migrate_settings()


def downgrade() -> None:
    with op.batch_alter_table("t_model_providers") as batch:
        batch.drop_constraint("uq_model_provider_user_name", type_="unique")
    if not op.get_context().as_sql:
        connection = op.get_bind()
        metadata = sa.MetaData()
        providers = sa.Table("t_model_providers", metadata, autoload_with=connection)
        models = sa.Table("t_ai_models", metadata, autoload_with=connection)
        selections = sa.Table("t_user_chat_models", metadata, autoload_with=connection)
        legacy = sa.Table("t_user_llm_configs", metadata, autoload_with=connection)
        rows = connection.execute(sa.select(providers, models.c.model_id).join(models, models.c.provider_id == providers.c.id)
                                  .join(selections, selections.c.model_id == models.c.id)).mappings().all()
        users = sa.Table("t_users", metadata, autoload_with=connection)
        global_selections = sa.Table("t_active_ai_models", metadata, autoload_with=connection)
        administrator_model = connection.execute(sa.select(selections.c.model_id).join(users, users.c.id == selections.c.user_id)
                                                .where(users.c.role == "admin").order_by(users.c.id).limit(1)).scalar()
        if administrator_model is not None:
            connection.execute(global_selections.insert().values(kind="chat", model_id=administrator_model))
        connection.execute(legacy.delete())
        for row in rows:
            connection.execute(legacy.insert().values(user_id=row["user_id"], base_url=row["base_url"], model_id=row["model_id"],
                encrypted_api_key=row["encrypted_api_key"], allow_insecure_http=row["allow_insecure_http"],
                created_at=row["created_at"], updated_at=datetime.utcnow()))
        # Retain every provider/model on downgrade, assigning unambiguous global names.
        rows = connection.execute(sa.select(providers.c.id, providers.c.name)).all()
        for row in rows:
            connection.execute(providers.update().where(providers.c.id == row.id).values(name="%s #%s" % (row.name[:100], row.id)))
    op.drop_table("t_user_chat_models")
    with op.batch_alter_table("t_model_providers") as batch:
        batch.drop_index("ix_t_model_providers_user_id")
        batch.drop_constraint("fk_model_provider_user", type_="foreignkey")
        batch.drop_column("user_id")
        batch.create_unique_constraint("name", ["name"])
