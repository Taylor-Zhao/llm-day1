"""${message}"""  # Alembic 新版本迁移说明。

from alembic import op  # 导入数据库操作入口。
import sqlalchemy as sa  # 导入字段和约束类型。

revision = ${repr(up_revision)}  # 当前迁移版本 ID。
down_revision = ${repr(down_revision)}  # 上一迁移版本 ID。
branch_labels = ${repr(branch_labels)}  # 可选分支标签。
depends_on = ${repr(depends_on)}  # 可选跨分支依赖。


def upgrade() -> None:  # 应用向前迁移。
    ${upgrades if upgrades else "pass"}  # 写入自动生成的升级操作。


def downgrade() -> None:  # 回滚当前迁移。
    ${downgrades if downgrades else "pass"}  # 写入自动生成的降级操作。
