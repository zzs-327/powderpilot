# 数据库迁移与安全接管

## 范围与原则

本次保持 MySQL + 本地 Chroma，不迁移业务数据或向量数据。Alembic 只管理 MySQL 六表：

- user、user_profile、resort、run、price、photo。
- 初始 revision：0001_initial；该文件是冻结 DDL，不 import 当前 models、不调用 create_all。
- resort.resort_key 和 user_profile.user_id 是唯一索引；三个子表的 resort_id 有 CASCADE 外键，user_profile.user_id **没有**外键。保持现状，不额外添加约束。
- 所有现有 default/onupdate 都是 SQLAlchemy 客户端行为；初始 DDL **没有 server_default**。直接 SQL INSERT 时仍需显式填写必填字段。
- Chroma、checkpoints.db、照片文件不由 Alembic 管理；业务备份需另行覆盖这些资产。

以下命令在 ski-backend 目录执行，使用课程解释器：

~~~powershell
<你的 Python 环境>/python.exe -m pip install -r requirements.txt
<你的 Python 环境>/python.exe -m alembic heads
~~~

DATABASE_URL 只由 config.config.Settings 读取（已有 .env 或安全注入的进程环境变量）。
不要把连接串写入 alembic.ini、命令行参数、文档、终端记录或工单；不要输出 Settings 的完整内容。
本交付不修改 .env，不对现有 MySQL 执行 stamp/upgrade/downgrade，不删除数据。

## 新空库：先迁移，再启动服务

1. DBA 创建**独立的新空 MySQL 数据库**，确认备份与权限；Alembic 不负责 CREATE DATABASE。
2. 在隔离进程中让 Settings 指向已确认的新库，核对目标身份（私下核对，不打印 URL）。
3. 确认没有业务表、没有旧数据、没有并发应用写入，然后执行：

~~~powershell
<你的 Python 环境>/python.exe -m alembic upgrade head
<你的 Python 环境>/python.exe -m alembic current
<你的 Python 环境>/python.exe -m migrations.check_schema
<你的 Python 环境>/python.exe -m alembic check
~~~

期望版本 0001_initial，六业务表与 alembic_version，结构检查退出 0。
随后按原流程同步 YAML 等业务数据；迁移本身不插入样例、不填充 Chroma。
**已存在六表不等于新空库**，不能直接运行初始 upgrade，也不要使用 if_not_exists 掩盖漂移。

## 已有数据库：先备份、检查一致，再由负责人 stamp

stamp 只写版本记录，不验证结构、不建表、不修复历史差异。**禁止自动 stamp，禁止先 stamp 再检查。**

1. 停止写入/安排维护窗口。由负责人确认目标，记录数据行数与当前结构。
2. DBA 完成完整备份（例如 MySQL dump，包含表结构、数据、触发器等；凭证使用安全配置或交互输入），
   保存校验和，并在隔离恢复库验证恢复成功。未验证恢复的备份不能作为接管门槛。
3. 先在恢复的副本运行只读检查，不对原库做 DDL：

~~~powershell
<你的 Python 环境>/python.exe -m migrations.check_schema
~~~

该命令使用 Settings，只有反射和 SELECT；**不会创建/修改 alembic_version，不会 stamp**。
退出 0 表示当前模型与数据库自动比较未发现差异；1 表示结构漂移、禁止 stamp；2 表示检查失败。
检查开启 compare_type=True、compare_server_default=True，比较列、可空性、索引、唯一约束、外键等；
不打印原始 diff 或连接异常（服务器默认值/驱动异常也可能含秘密）。

4. 接管目标是**冻结的 0001_initial**，不是任意未来 models。
   本次六表 models 与初始 revision 一致；以后必须使用该基线对应版本的模型做检查，并逐项人工核对冻结 DDL。
   在受控环境用 SHOW CREATE TABLE 对照 revision/离线 SQL，特别检查主键、自增、整数 signedness、
   字符长度、可空性、Date/DateTime/Float、全部服务器默认值、唯一索引、CASCADE、注释。
   另核对 MySQL 引擎/字符集/排序规则（建议 InnoDB/utf8mb4），以及触发器、视图、未纳管表。
   Alembic 自动比较不能证明这些全部一致，更不能证明数据一致。
5. 核对副本与原库结构未变，保存脱敏检查证据；负责人批准后，**人工在已核实的目标执行**：

~~~powershell
# 仅在上述门槛全部满足并由负责人批准后；本次实现没有执行此命令
<你的 Python 环境>/python.exe -m alembic stamp 0001_initial
<你的 Python 环境>/python.exe -m alembic current
<你的 Python 环境>/python.exe -m migrations.check_schema
<你的 Python 环境>/python.exe -m alembic check
~~~

6. 复核数据行数和业务读写。若发现缺表/缺列/额外默认值/索引或类型不一致：停止，不 stamp，
   不删表重建；制定独立、可审计的数据保留修复方案并先在恢复副本验证。
   若已有 alembic_version，先确认它的历史归属，禁止覆盖未知版本记录。

## 后续开发流程

~~~powershell
<你的 Python 环境>/python.exe -m alembic revision --autogenerate -m "描述变更"
# 人工审查新 revision；修正重命名误判，确认数据回填和 nullable/默认行为
<你的 Python 环境>/python.exe -m alembic upgrade head
<你的 Python 环境>/python.exe -m alembic check
~~~

autogenerate/check 需要连接数据库：只能针对获准的开发库/恢复副本，不能盲目指向线上。
旧 revision 永不改成 import 当前 models，也不修改已发布 revision。数据回填应显式、可验证、可重试。
MySQL DDL 常隐式提交，失败不保证整体回滚；先验证副本，失败后审查实际结构，不能直接重复或降级。
初始 downgrade 会**删除六表和数据**，只用于可丢弃测试库；生产回退优先备份恢复或前向修复。

## 离线 SQL 与日志安全

~~~powershell
<你的 Python 环境>/python.exe -m alembic upgrade head --sql
~~~

离线只从 Settings URL 获取方言，不联网，不将 URL 注入 ini 或 SQL 输出；迁移 engine 关闭 echo，
不受应用 DEBUG 控制。连接/执行失败只显示安全摘要；排障时在受控环境核对设置，不公开原始异常。
离线 SQL 仍可能包含未来 revision 编写者添加的业务值：**输出前审查 revision**，不应假定任意未来 SQL 自动脱敏。

## 本次只读现有库检查结果

2026-10-11 检查发现 **4 组 / 7 项差异**。同日按负责人指示**跳过备份**，已对现有库执行 `stamp 0001_initial` 并 `upgrade 0002_price_pricing_fields`：价格 98 行、雪场 12 行都还在，新增列均为 NULL（代码按人民币、可计入小计解释）。下面 4 组旧差异没有改，`check_schema` 仍退出 1，不影响当前读写：

| 表/列 | 差异类型 |
| --- | --- |
| price.unit | 服务器默认、注释 |
| price.data_status | 可空性、注释 |
| resort.official_runs_total | 注释 |
| run.data_status | 可空性、注释 |

不在公开输出中列出实际默认表达式或业务值。后续须先备份并验证恢复，在恢复副本人工审查：
确认服务器默认应保留还是去除；检查可空性差异方向与历史 NULL 数量，设计显式数据回填后再调整约束；
注释也要核对意图。另行批准独立修复方案，今晚不自动修表以追齐模型。

## 验证与短交付边界

~~~powershell
<你的 Python 环境>/python.exe -m pytest tests/test_migrations.py -q
~~~

测试只使用临时 SQLite 文件，覆盖空库升级、版本表、六表/索引/外键、模型零差异、
重复 upgrade、临时库 downgrade/再次 upgrade、类型/服务器默认漂移检测、冻结 revision、
MySQL 离线 DDL 和密码/URL 不泄漏。SQLite 是测试替身，**不是替换生产 MySQL**；
不能证明真实 MySQL DDL、排序规则、隐式提交行为等。在真实新空 MySQL/恢复副本的验收属于部署门槛。

由于限定修改范围，config/database.py、main.py、init_db.py 保持不变，已有启动 create_all 仍存在。
它不是迁移器，也不会补齐旧列；部署必须先显式 Alembic 迁移，再启动服务，不再用 init_db.py 管理结构。
此次未切换启动生命周期、未自动接管旧库、未改变 Chroma/模型/业务逻辑。
