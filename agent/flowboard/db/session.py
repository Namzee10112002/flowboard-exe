from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from flowboard.config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_sqlite_fk(dbapi_conn, _connection_record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def init_db() -> None:
    from sqlalchemy import inspect

    from flowboard.db import models

    # Targeted migration: if an older `asset` table exists without `url`,
    # drop it. Acceptable because the app has not stored real asset rows
    # prior to Run 6; other tables (board, node, edge, chatmessage, request)
    # are left alone.
    with engine.connect() as conn:
        insp = inspect(conn)
        if insp.has_table("asset"):
            cols = {c["name"] for c in insp.get_columns("asset")}
            if "url" not in cols:
                models.Asset.__table__.drop(conn, checkfirst=True)
                conn.commit()

        # Edge.source_variant_idx — added when per-edge variant pinning
        # shipped. SQLite ALTER TABLE ADD COLUMN is non-destructive (and
        # idempotent via the column-existence check), so existing DBs
        # pick up the new column on first boot without losing data.
        # `create_all` below won't help because it skips ALTERs on
        # existing tables.
        if insp.has_table("edge"):
            edge_cols = {c["name"] for c in insp.get_columns("edge")}
            if "source_variant_idx" not in edge_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE edge ADD COLUMN source_variant_idx INTEGER"
                )
                conn.commit()

        if insp.has_table("scenarioscene"):
            scene_cols = {c["name"] for c in insp.get_columns("scenarioscene")}
            if "voice_media_id" not in scene_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE scenarioscene ADD COLUMN voice_media_id VARCHAR"
                )
                conn.commit()

        if insp.has_table("scenario"):
            scenario_cols = {c["name"] for c in insp.get_columns("scenario")}
            if "video_audio_mode" not in scenario_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE scenario ADD COLUMN video_audio_mode VARCHAR DEFAULT 'silent'"
                )
                conn.commit()
            if "final_video_media_id" not in scenario_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE scenario ADD COLUMN final_video_media_id VARCHAR"
                )
                conn.commit()
            if "voice_id" not in scenario_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE scenario ADD COLUMN voice_id VARCHAR"
                )
                conn.commit()

        if insp.has_table("request"):
            request_cols = {c["name"] for c in insp.get_columns("request")}
            if "account_id" not in request_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE request ADD COLUMN account_id INTEGER"
                )
                conn.commit()
            if "dispatch_attempts" not in request_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE request ADD COLUMN dispatch_attempts INTEGER DEFAULT 0"
                )
                conn.commit()
            if "next_retry_at" not in request_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE request ADD COLUMN next_retry_at TIMESTAMP"
                )
                conn.commit()
            if "last_dispatch_error" not in request_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE request ADD COLUMN last_dispatch_error VARCHAR"
                )
                conn.commit()

        if insp.has_table("flowaccount"):
            account_cols = {c["name"] for c in insp.get_columns("flowaccount")}
            if "credential" not in account_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE flowaccount ADD COLUMN credential VARCHAR"
                )
                conn.commit()
            if "email" not in account_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE flowaccount ADD COLUMN email VARCHAR"
                )
                conn.commit()
            if "chrome_user_data_dir" not in account_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE flowaccount ADD COLUMN chrome_user_data_dir VARCHAR"
                )
                conn.commit()

    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session():
    with Session(engine) as session:
        yield session
