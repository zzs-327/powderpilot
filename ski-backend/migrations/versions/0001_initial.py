"""冻结六表初始结构；Python default/onupdate 不是服务器默认。"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('resort',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('resort_key', sa.String(length=32), nullable=False, comment='对应 YAML 的 id'),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('region', sa.String(length=64), nullable=False),
    sa.Column('transport_hint', sa.String(length=255), nullable=True),
    sa.Column('handbook_url', sa.String(length=255), nullable=True),
    sa.Column('price_updated_at', sa.Date(), nullable=True),
    sa.Column('runs_beginner', sa.Integer(), nullable=True),
    sa.Column('runs_intermediate', sa.Integer(), nullable=True),
    sa.Column('runs_advanced', sa.Integer(), nullable=True),
    sa.Column('official_runs_total', sa.Integer(), nullable=True),
    sa.Column('data_status', sa.String(length=16), nullable=False, comment='数据可信度：verified=官方直采 / transcribed=二手转引 / sample=示例待核实'),
    sa.Column('data_status_note', sa.Text(), nullable=True),
    sa.Column('create_time', sa.DateTime(), nullable=False),
    sa.Column('update_time', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_resort_resort_key'), 'resort', ['resort_key'], unique=True)
    op.create_table('user',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=32), nullable=False),
    sa.Column('password', sa.String(length=128), nullable=False),
    sa.Column('create_time', sa.DateTime(), nullable=False),
    sa.Column('update_time', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('user_profile',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False, comment='对应 user.id'),
    sa.Column('level', sa.String(length=16), nullable=True, comment='新手/进阶/老手'),
    sa.Column('board_type', sa.String(length=16), nullable=True, comment='单板/双板/都玩'),
    sa.Column('home_region', sa.String(length=32), nullable=True, comment='常活动区域'),
    sa.Column('budget_band', sa.String(length=32), nullable=True, comment='预算带'),
    sa.Column('create_time', sa.DateTime(), nullable=False),
    sa.Column('update_time', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_profile_user_id'), 'user_profile', ['user_id'], unique=True)
    op.create_table('photo',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('resort_id', sa.Integer(), nullable=False),
    sa.Column('object_key', sa.String(length=255), nullable=False, comment='MinIO object_key 或本地相对路径'),
    sa.Column('shot_at', sa.Date(), nullable=True, comment='拍摄日期'),
    sa.Column('location', sa.String(length=64), nullable=False),
    sa.Column('credit', sa.String(length=64), nullable=False, comment='拍摄者'),
    sa.Column('license', sa.String(length=64), nullable=False, comment='授权状态'),
    sa.Column('source_type', sa.String(length=4), nullable=False),
    sa.ForeignKeyConstraint(['resort_id'], ['resort.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_photo_resort_id'), 'photo', ['resort_id'], unique=False)
    op.create_table('price',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('resort_id', sa.Integer(), nullable=False),
    sa.Column('item', sa.String(length=64), nullable=False, comment='价格项，如"平日全天雪票"'),
    sa.Column('price', sa.Integer(), nullable=False, comment='单位：元'),
    sa.Column('unit', sa.String(length=64), nullable=False, comment='计价单位；可分时段优惠价写清时段口径'),
    sa.Column('updated_at', sa.Date(), nullable=True, comment='录入日期'),
    sa.Column('expire_at', sa.Date(), nullable=True, comment='保鲜期截止'),
    sa.Column('source', sa.String(length=128), nullable=False),
    sa.Column('source_type', sa.String(length=4), nullable=False),
    sa.Column('data_status', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['resort_id'], ['resort.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_price_resort_id'), 'price', ['resort_id'], unique=False)
    op.create_table('run',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('resort_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('difficulty', sa.String(length=16), nullable=False, comment='初级/中级/高级'),
    sa.Column('level', sa.String(length=16), nullable=False, comment='beginner/intermediate/advanced'),
    sa.Column('length_m', sa.Integer(), nullable=True),
    sa.Column('vertical_m', sa.Integer(), nullable=True, comment='落差（米），不是所有官方口径都给'),
    sa.Column('top_altitude_m', sa.Integer(), nullable=True, comment='最高海拔（米）'),
    sa.Column('avg_slope_deg', sa.Float(), nullable=True, comment='平均坡度（度）'),
    sa.Column('max_slope_deg', sa.Float(), nullable=True, comment='最大坡度（度）'),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=255), nullable=False),
    sa.Column('source_type', sa.String(length=4), nullable=False),
    sa.Column('data_status', sa.String(length=16), nullable=False),
    sa.Column('date', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['resort_id'], ['resort.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_run_resort_id'), 'run', ['resort_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_run_resort_id'), table_name='run')
    op.drop_table('run')
    op.drop_index(op.f('ix_price_resort_id'), table_name='price')
    op.drop_table('price')
    op.drop_index(op.f('ix_photo_resort_id'), table_name='photo')
    op.drop_table('photo')
    op.drop_index(op.f('ix_user_profile_user_id'), table_name='user_profile')
    op.drop_table('user_profile')
    op.drop_table('user')
    op.drop_index(op.f('ix_resort_resort_key'), table_name='resort')
    op.drop_table('resort')
