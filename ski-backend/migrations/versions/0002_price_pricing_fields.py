"""price 表增加计价口径列：只加列、全部可空、无服务器默认。

设计见 docs/PRICING_MODEL.md。已有数据行新列为 NULL，由代码按"CNY / 可计入小计"解释，
老雪场行为不变。price.price 的列注释从"单位：元"改为按币种描述（SQLite 无列注释，跳过）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_price_pricing_fields"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

OLD_PRICE_COMMENT = '单位：元'
NEW_PRICE_COMMENT = '原币金额整数；币种见 currency'

COLUMNS = (
    ('currency', sa.String(length=3), 'ISO 4217 币种；空按 CNY；不做换算'),
    ('category', sa.String(length=16), 'lift_ticket/rental/lesson/transfer/lodging/other'),
    ('unit_basis', sa.String(length=24),
     'per_person/per_instructor/per_room/per_seat_one_way/per_set/per_item/unknown'),
    ('valid_from', sa.Date(), '报价适用期起（不是保鲜期）'),
    ('valid_to', sa.Date(), '报价适用期止'),
    ('season', sa.String(length=32), '适用雪季展示文本'),
    ('tax_status', sa.String(length=8), 'included/excluded/unknown'),
    ('includes', sa.JSON(), '包含项短语列表'),
    ('excludes', sa.JSON(), '不包含项短语列表'),
    ('conditions', sa.JSON(), '预约、退改等短条款'),
    ('budget_usable', sa.Boolean(), '能否进入已知小计；空按可用'),
    ('source_url', sa.String(length=512), '单条原页链接'),
)


def _set_price_comment(comment: str, existing: str) -> None:
    if op.get_context().dialect.name == 'sqlite':
        return
    op.alter_column('price', 'price', existing_type=sa.Integer(), existing_nullable=False,
                    comment=comment, existing_comment=existing)


def upgrade() -> None:
    with op.batch_alter_table('price') as batch:
        for name, type_, comment in COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True, comment=comment))
    _set_price_comment(NEW_PRICE_COMMENT, OLD_PRICE_COMMENT)


def downgrade() -> None:
    _set_price_comment(OLD_PRICE_COMMENT, NEW_PRICE_COMMENT)
    with op.batch_alter_table('price') as batch:
        for name, _, _ in reversed(COLUMNS):
            batch.drop_column(name)
